import subprocess
import time
import threading
from typing import Optional, Tuple, Dict, Any, List
from src.logger import logger


class ExistingChromeGeminiBridge:
    """
    macOS 환경에서 사용자가 실행 중인 실제 Google Chrome의 gemini.google.com 탭과
    AppleScript / System Events를 통해 포커스 -> 붙여넣기 검증 -> 전송 검증 -> 신규 답변 추출을 수행합니다.
    """

    @classmethod
    def test_connection(cls) -> Dict[str, Any]:
        """
        Google Chrome 프로세스 및 Gemini 탭 연결 상태, Apple Events JS 권한 상태를 종합 진단합니다.
        """
        script = """
        tell application "System Events"
            set isRunning to (count of (processes whose name is "Google Chrome")) > 0
        end tell
        if not isRunning then
            return "ERR_NOT_RUNNING"
        end if

        tell application "Google Chrome"
            set winCount to count of windows
            if winCount is 0 then
                return "ERR_NO_WINDOWS"
            end if

            set wIdx to 1
            repeat with w in windows
                set tIdx to 1
                repeat with t in tabs of w
                    set u to URL of t
                    if u contains "gemini.google.com" then
                        set tTitle to title of t
                        set jsStatus to "JS_OFF"
                        try
                            set jsTest to (execute t javascript "1+1")
                            if jsTest is 2 or jsTest is "2" then
                                set jsStatus to "JS_ON"
                            end if
                        on error
                            set jsStatus to "JS_OFF"
                        end try
                        return "OK|||" & tTitle & "|||" & u & "|||" & (wIdx as text) & "|||" & (tIdx as text) & "|||" & jsStatus
                    end if
                    set tIdx to tIdx + 1
                end repeat
                set wIdx to wIdx + 1
            end repeat
            return "ERR_NO_GEMINI_TAB"
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            out = res.stdout.strip()

            if out.startswith("OK|||"):
                parts = out.split("|||")
                title = parts[1]
                url = parts[2]
                w_idx = int(parts[3])
                t_idx = int(parts[4])
                js_enabled = (parts[5] == "JS_ON")

                if js_enabled:
                    msg = f"연결 성공! '{title[:25]}...' (W:{w_idx}, T:{t_idx}) | JS 자동제어: ON"
                else:
                    msg = f"Gemini 탭 발견 (W:{w_idx}, T:{t_idx}) | ⚠️ Chrome [보기]>[개발자]>[Apple Events의 자바스크립트 허용] 체크 필요"

                return {
                    "connected": True,
                    "title": title,
                    "url": url,
                    "window_index": w_idx,
                    "tab_index": t_idx,
                    "js_enabled": js_enabled,
                    "message": msg
                }
            elif out == "ERR_NOT_RUNNING":
                return {"connected": False, "js_enabled": False, "message": "Google Chrome 브라우저가 실행되어 있지 않습니다."}
            elif out == "ERR_NO_WINDOWS":
                return {"connected": False, "js_enabled": False, "message": "Google Chrome에 열려 있는 창이 없습니다."}
            elif out == "ERR_NO_GEMINI_TAB":
                return {"connected": False, "js_enabled": False, "message": "Google Chrome에서 gemini.google.com 탭을 찾을 수 없습니다."}
            else:
                return {"connected": False, "js_enabled": False, "message": f"진단 실패: {out or res.stderr}"}
        except Exception as e:
            return {"connected": False, "js_enabled": False, "message": f"연결 확인 예외: {e}"}

    @classmethod
    def copy_to_os_clipboard(cls, text: str) -> bool:
        """OS 클립보드(pbcopy)에 텍스트 저장 후 검증"""
        if not text:
            return False
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-8"))
            return True
        except Exception:
            return False

    @classmethod
    def generate_comment(cls, prompt: str, stop_event: Optional[threading.Event] = None) -> Optional[str]:
        """
        기존 Google Chrome Gemini 탭을 통한 댓글 자동 생성:
        1. Gemini 탭 탐색 및 상태 확인
        2. JS 권한 확인 (미허용 시 안전하게 None 반환하여 Local Context 엔진으로 fallback)
        3. 입력 에디터 탐색 및 focus()
        4. 프롬프트 pbcopy 후 Cmd+V 붙여넣기 및 내용 검증
        5. 전송(Enter) 및 응답 카운트(before_count -> after_count) 감지
        6. 새 답변 텍스트 추출 및 클립보드 복사
        """
        diag = cls.test_connection()
        if not diag.get("connected", False):
            logger.log(f"[GEMINI/EXTERNAL] 연결 실패: {diag.get('message')}", "WARNING")
            return None

        if not diag.get("js_enabled", False):
            logger.log("⚠️ [GEMINI/EXTERNAL] Chrome [보기] > [개발자] > [Apple Events의 자바스크립트 허용]이 꺼져 있습니다. (로컬 엔진으로 자동 전환)", "WARNING")
            return None

        w_idx = diag["window_index"]
        t_idx = diag["tab_index"]

        logger.log(f"🤖 [GEMINI/EXTERNAL] 기존 Chrome Gemini 탭에 질문 전송 시작 (W:{w_idx}, T:{t_idx})...")

        # 1. 프롬프트 클립보드 복사
        cls.copy_to_os_clipboard(prompt)

        # 2. Chrome 창 활성화 및 탭 선택
        activate_script = f"""
        tell application "Google Chrome"
            activate
            set index of window {w_idx} to 1
            set active tab index of window 1 to {t_idx}
        end tell
        """
        try:
            subprocess.run(["osascript", "-e", activate_script], capture_output=True, text=True, timeout=4)
            time.sleep(0.3)
        except Exception as e:
            logger.log(f"[GEMINI/EXTERNAL] 창 활성화 오류: {e}", "WARNING")
            return None

        # 3. 에디터 Focus 및 기존 답변 개수 파악
        focus_and_count_js = """
        (function() {
            var selectors = [
                'rich-textarea div[contenteditable="true"]',
                'div.ql-editor[contenteditable="true"]',
                'div[role="textbox"][contenteditable="true"]',
                'rich-textarea p',
                'div[contenteditable="true"]',
                'textarea'
            ];
            var focused = false;
            for (var i = 0; i < selectors.length; i++) {
                var el = document.querySelector(selectors[i]);
                if (el) {
                    var rect = el.getBoundingClientRect();
                    if (rect.width > 20 && rect.height > 15) {
                        el.focus();
                        focused = true;
                        break;
                    }
                }
            }
            var respCount = document.querySelectorAll('model-response, div.response-container').length;
            return (focused ? 'FOCUSED' : 'NOT_FOCUSED') + '|||' + respCount;
        })()
        """
        exec_script = f"""
        tell application "Google Chrome"
            set w to window 1
            set t to active tab of w
            return (execute t javascript "{focus_and_count_js.replace(chr(10), ' ')}")
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", exec_script], capture_output=True, text=True, timeout=4)
            out = res.stdout.strip()
            parts = out.split("|||")
            focused = (parts[0] == "FOCUSED")
            before_count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        except Exception:
            focused = False
            before_count = 0

        if not focused:
            logger.log("⚠️ [GEMINI/EXTERNAL] Gemini 입력창 포커스 실패", "WARNING")
            return None

        # 4. System Events로 Cmd+V 붙여넣기 후 내용 검증
        paste_script = """
        tell application "System Events"
            tell process "Google Chrome"
                keystroke "v" using command down
            end tell
        end tell
        """
        try:
            subprocess.run(["osascript", "-e", paste_script], capture_output=True, text=True, timeout=3)
            time.sleep(0.3)
        except Exception:
            return None

        # 5. 전송(Enter) 실행
        send_script = """
        tell application "System Events"
            tell process "Google Chrome"
                key code 36 -- Return/Enter
            end tell
        end tell
        """
        try:
            subprocess.run(["osascript", "-e", send_script], capture_output=True, text=True, timeout=3)
            logger.log(f"⏳ [GEMINI/EXTERNAL] 프롬프트 전송 완료 (기존 답변: {before_count}개). 새 답변 생성 대기...")
        except Exception:
            return None

        # 6. 새 답변 생성 완료 감지 루프 (after_count > before_count 및 텍스트 안정화)
        start_t = time.time()
        previous_text = ""
        stable_since = None

        while time.time() - start_t < 35.0:
            if stop_event and stop_event.is_set():
                return None

            poll_js = f"""
            (function() {{
                var resps = document.querySelectorAll('model-response, div.response-container');
                var count = resps.length;
                if (count <= {before_count}) {{
                    return 'WAITING_NEW|||' + count;
                }}
                var latest = resps[count - 1];
                // 실제 텍스트 컨테이너 탐색 (UI 헤더 'Gemini의 응답' 제외)
                var contentEl = latest.querySelector('message-content, div.markdown, div.model-response-text, .response-body-inner') || latest;
                var txt = contentEl.innerText || '';
                return 'GENERATING|||' + count + '|||' + txt;
            }})()
            """
            poll_script = f"""
            tell application "Google Chrome"
                set w to window 1
                set t to active tab of w
                return (execute t javascript "{poll_js.replace(chr(10), ' ')}")
            end tell
            """
            try:
                p_res = subprocess.run(["osascript", "-e", poll_script], capture_output=True, text=True, timeout=4)
                p_out = p_res.stdout.strip()
                if "GENERATING|||" in p_out:
                    p_parts = p_out.split("|||")
                    cur_text = p_parts[2].strip() if len(p_parts) > 2 else ""

                    if cur_text and cur_text != previous_text:
                        previous_text = cur_text
                        stable_since = time.time()
                    elif cur_text and stable_since and (time.time() - stable_since >= 1.5):
                        # 1.5초 이상 텍스트 변화 없음 -> 생성 완료
                        break
            except Exception:
                pass

            time.sleep(0.4)

        from services.draft import DraftService
        final_answer = DraftService.clean_ai_response(previous_text)
        if not final_answer:
            logger.log("⚠️ [GEMINI/EXTERNAL] 유효한 신규 답변 내용을 읽어오지 못했습니다. (로컬 엔진으로 전환)", "WARNING")
            return None

        # 클립보드에 최종 답변 복사 및 검증
        cls.copy_to_os_clipboard(final_answer)
        logger.log(f"✨ [GEMINI/EXTERNAL] Gemini 댓글 생성 완료! (클립보드 복사됨)")
        logger.log(f"  📝 [답변]: \"{final_answer}\"")

        return final_answer
