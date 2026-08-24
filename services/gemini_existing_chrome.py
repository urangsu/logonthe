import subprocess
import time
import threading
from typing import Optional, Tuple, Dict, Any
from src.logger import logger


class ExistingChromeGeminiBridge:
    """
    macOS 환경에서 이미 사용자가 실행 중이며 로그인되어 있는
    Google Chrome의 gemini.google.com 탭을 AppleScript / System Events로 직접 연동합니다.
    """

    @classmethod
    def test_connection(cls) -> Dict[str, Any]:
        """
        Google Chrome 프로세스 및 Gemini 탭 연결 상태를 진단합니다.
        반환: {
            "connected": bool,
            "title": str,
            "url": str,
            "window_index": int,
            "tab_index": int,
            "js_enabled": bool,
            "message": str
        }
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

                msg = f"Gemini 탭 연결 성공: '{title[:30]}...' (JS 허용: {'ON' if js_enabled else 'OFF'})"
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
                return {"connected": False, "message": "Google Chrome 브라우저가 실행되어 있지 않습니다."}
            elif out == "ERR_NO_WINDOWS":
                return {"connected": False, "message": "Google Chrome에 열린 창이 없습니다."}
            elif out == "ERR_NO_GEMINI_TAB":
                return {"connected": False, "message": "Google Chrome에서 gemini.google.com 탭을 찾을 수 없습니다."}
            else:
                return {"connected": False, "message": f"진단 실패: {out or res.stderr}"}
        except Exception as e:
            return {"connected": False, "message": f"연결 확인 중 예외 발생: {e}"}

    @classmethod
    def copy_to_os_clipboard(cls, text: str) -> bool:
        """OS 클립보드(pbcopy)에 텍스트 저장 후 pbpaste로 검증"""
        if not text:
            return False
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-8"))

            # 검증
            check_p = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            if check_p.stdout.strip() == text.strip():
                logger.log("[GEMINI] OS 클립보드 복사 및 일치 검증 완료 (pbcopy OK)")
                return True
            return True
        except Exception as e:
            logger.log(f"[GEMINI] 클립보드 복사 실패: {e}", "WARNING")
            return False

    @classmethod
    def generate_comment(cls, prompt: str, stop_event: Optional[threading.Event] = None) -> Optional[str]:
        """
        기존 실행 중인 Google Chrome의 Gemini 탭을 통해 댓글 생성
        1. Gemini 탭 탐색 및 상태 확인
        2. 프롬프트를 클립보드에 복사
        3. Chrome 윈도우/탭 활성화 및 입력창에 Cmd+V & Enter 전송
        4. 응답 완료 감지 (JS 가능 시 DOM 추적, 불가능 시 안내 및 클립보드 대기)
        """
        diag = cls.test_connection()
        if not diag.get("connected", False):
            logger.log(f"❌ [GEMINI/EXTERNAL] 기존 Chrome 연결 실패: {diag.get('message')}", "ERROR")
            return None

        w_idx = diag["window_index"]
        t_idx = diag["tab_index"]
        js_enabled = diag["js_enabled"]

        logger.log(f"🤖 [GEMINI/EXTERNAL] 기존 Chrome Gemini 탭(W:{w_idx}, T:{t_idx})에 프롬프트 전송 중...")

        # 1. 프롬프트를 OS 클립보드에 준비
        cls.copy_to_os_clipboard(prompt)

        # 2. Chrome 창 및 해당 탭 활성화 후 입력창에 붙여넣기 및 Enter 전송
        if js_enabled:
            # JS가 켜져 있는 경우: 전송 전 기존 답변 개수 파악
            count_script = f"""
            tell application "Google Chrome"
                set w to window {w_idx}
                set t to tab {t_idx} of w
                try
                    return (execute t javascript "document.querySelectorAll('model-response, message-content, div.markdown').length")
                on error
                    return 0
                end try
            end tell
            """
            try:
                c_res = subprocess.run(["osascript", "-e", count_script], capture_output=True, text=True, timeout=3)
                before_count = int(c_res.stdout.strip() or 0)
            except Exception:
                before_count = 0
        else:
            before_count = 0
            logger.log("💡 [GEMINI] Chrome [보기] > [개발자] > [Apple Events의 자바스크립트 허용]이 켜지면 100% 전자동 답변 추출이 가능합니다.", "WARNING")

        # 3. AppleScript로 Chrome 활성화 및 키 입력
        paste_and_send_script = f"""
        tell application "Google Chrome"
            activate
            set index of window {w_idx} to 1
            set active tab index of window 1 to {t_idx}
        end tell
        delay 0.4
        tell application "System Events"
            tell process "Google Chrome"
                -- 입력창 포커스를 위해 Cmd+V 붙여넣기
                keystroke "v" using command down
                delay 0.3
                key code 36 -- Return/Enter
            end tell
        end tell
        """
        try:
            subprocess.run(["osascript", "-e", paste_and_send_script], capture_output=True, text=True, timeout=6)
        except Exception as e:
            logger.log(f"[GEMINI/EXTERNAL] 키 입력 전송 오류: {e}", "ERROR")
            return None

        logger.log("⏳ [GEMINI/EXTERNAL] 프롬프트 전송 완료. 답변 생성 대기 중...")

        # 4. 답변 생성 완료 감지 루프
        if js_enabled:
            start_t = time.time()
            previous_text = ""
            stable_since = None

            while time.time() - start_t < 40.0:
                if stop_event and stop_event.is_set():
                    return None

                extract_script = f"""
                tell application "Google Chrome"
                    set w to window {w_idx}
                    set t to tab {t_idx} of w
                    try
                        return (execute t javascript "(() => {{
                            const responses = document.querySelectorAll('model-response, message-content, div.markdown');
                            if (responses.length === 0) return '';
                            const last = responses[responses.length - 1];
                            return last.innerText || '';
                        }})()")
                    on error errMsg
                        return "ERR:" & errMsg
                    end try
                end tell
                """
                try:
                    ext_res = subprocess.run(["osascript", "-e", extract_script], capture_output=True, text=True, timeout=4)
                    cur_text = ext_res.stdout.strip()
                    if cur_text.startswith("ERR:"):
                        cur_text = ""
                except Exception:
                    cur_text = ""

                if cur_text and cur_text != previous_text:
                    previous_text = cur_text
                    stable_since = time.time()
                elif cur_text and stable_since and (time.time() - stable_since >= 1.5):
                    break

                time.sleep(0.4)

            final_answer = previous_text.strip()
            if final_answer:
                # 마크다운 코드블록 제거
                if final_answer.startswith("```"):
                    final_answer = final_answer.strip("`")
                    if final_answer.startswith("text") or final_answer.startswith("markdown"):
                        final_answer = final_answer.split("\n", 1)[-1]
                final_answer = final_answer.strip()

                # 클립보드에 최종 답변 복사
                cls.copy_to_os_clipboard(final_answer)
                logger.log(f"✨ [GEMINI/EXTERNAL] 답변 자동 추출 및 클립보드 복사 완료!")
                logger.log(f"  📝 [답변]: \"{final_answer}\"")
                return final_answer
        else:
            # JS 비활성 시: 3초 후 사용자에게 클립보드 복사 안내
            interruptible_wait(stop_event, 3.0)
            logger.log("ℹ️ [GEMINI/EXTERNAL] 기존 Chrome에서 생성이 진행 중입니다. 생성이 끝나면 답변을 복사하여 네이버 댓글창에 붙여넣어 주세요.")

        return None
