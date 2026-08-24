import time
import subprocess
import threading
from typing import Optional, List
from playwright.sync_api import Page, Locator
from browser.session import interruptible_wait
from src.logger import logger


class GeminiWebBridge:
    """
    Google Gemini 웹페이지(gemini.google.com)와 Playwright로 상호작용하여
    1) 프롬프트 자동 입력 및 전송
    2) 스트리밍 응답 완료 감지
    3) 최종 답변 추출 및 OS 클립보드 복사
    를 수행하는 브릿지 서비스
    """
    GEMINI_DEFAULT_URL = "https://gemini.google.com/app"

    # Gemini 입력창 셀렉터 우선순위
    EDITOR_SELECTORS = [
        "rich-textarea div[contenteditable='true']",
        "div.ql-editor[contenteditable='true']",
        "div[role='textbox'][contenteditable='true']",
        "rich-textarea p",
        "div[contenteditable='true']",
        "textarea[aria-label*='프롬프트']",
        "textarea"
    ]

    # Gemini 전송 버튼 셀렉터 우선순위
    SEND_BUTTON_SELECTORS = [
        "button[aria-label*='프롬프트 보내기']",
        "button[aria-label*='보내기']",
        "button[aria-label*='전송']",
        "button[aria-label*='Send']",
        "button.send-button",
        "button[data-test-id='send-button']",
        "button:has(mat-icon:text-is('send'))",
        "button:has(.mat-mdc-button-touch-target)",
        "button[aria-label*='submit']"
    ]

    # Gemini 생성 중 / 중지 버튼 셀렉터
    STOP_BUTTON_SELECTORS = [
        "button[aria-label*='응답 생성 중지']",
        "button[aria-label*='중지']",
        "button[aria-label*='Stop']",
        "mat-icon:text-is('stop')"
    ]

    # 답변 컨테이너 셀렉터
    RESPONSE_SELECTORS = [
        "model-response",
        "div.response-container",
        "message-content",
        "div.markdown",
        "div[class*='model-response']",
        "div[class*='response_content']",
        "div.model-response-text"
    ]

    # Gemini 자체 복사 버튼 셀렉터
    COPY_BUTTON_SELECTORS = [
        "button[aria-label*='복사']",
        "button[aria-label*='Copy']",
        "button:has(mat-icon:text-is('content_copy'))",
        "button:has(mat-icon:has-text('content_copy'))"
    ]

    @classmethod
    def ensure_open(cls, page: Page, target_url: str = GEMINI_DEFAULT_URL, stop_event: Optional[threading.Event] = None) -> bool:
        """Gemini 페이지로 이동 및 로드 확인"""
        current_url = page.url or ""
        url_to_go = target_url if target_url else cls.GEMINI_DEFAULT_URL

        if "gemini.google.com" in current_url:
            if target_url and target_url != cls.GEMINI_DEFAULT_URL and target_url not in current_url:
                logger.log(f"[GEMINI] 지정 대화 URL 이동: {target_url}")
                page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                interruptible_wait(stop_event, 1.5)
        else:
            logger.log(f"[GEMINI] Gemini 웹페이지 접속: {url_to_go}")
            try:
                page.goto(url_to_go, wait_until="domcontentloaded", timeout=25000)
                interruptible_wait(stop_event, 2.0)
            except Exception as e:
                logger.log(f"[GEMINI] 페이지 로드 안내: {e}", "WARNING")

        # 로그인 확인
        if "accounts.google.com" in (page.url or ""):
            logger.log("⚠️ [GEMINI] Google 로그인이 필요합니다. [🌐 로그인 창 열기]에서 구글 로그인을 먼저 진행해 주세요.", "ERROR")
            return False

        return True

    @classmethod
    def copy_to_os_clipboard(cls, text: str):
        """OS 클립보드에 텍스트 복사 (macOS pbcopy 및 GUI Tkinter 클립보드 동시 동기화)"""
        if not text:
            return
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-8"))
        except Exception:
            pass

    @classmethod
    def generate_comment(
        cls,
        page: Page,
        prompt: str,
        gemini_url: str = GEMINI_DEFAULT_URL,
        stop_event: Optional[threading.Event] = None
    ) -> Optional[str]:
        """
        Gemini 자동화 전체 파이프라인:
        1. 페이지 준비
        2. 프롬프트 다중 방식으로 입력 (Quill/ContentEditable/DOM)
        3. 전송 버튼 클릭 및 스트리밍 답변 완료 감지
        4. 최신 답변 추출 및 클립보드 자동 복사
        """
        if not cls.ensure_open(page, target_url=gemini_url, stop_event=stop_event):
            return None

        # 페이지 로딩 대기
        try:
            page.wait_for_selector(
                "rich-textarea, div.ql-editor, div[contenteditable='true'], textarea",
                timeout=8000
            )
        except Exception:
            pass

        logger.log("🤖 [GEMINI] Gemini 입력창에 프롬프트 자동 입력 중...")

        # JavaScript를 통한 신뢰성 있는 입력창 주입 및 이벤트 디스패치
        injected = page.evaluate("""
            (text) => {
                const editor = document.querySelector('rich-textarea div[contenteditable="true"], div.ql-editor, div[role="textbox"], rich-textarea p, div[contenteditable="true"], textarea');
                if (!editor) return false;

                editor.focus();

                if (editor.tagName.toLowerCase() === 'textarea') {
                    editor.value = text;
                    editor.dispatchEvent(new Event('input', { bubbles: true }));
                    editor.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }

                // ContentEditable / Quill 처리
                try {
                    editor.innerHTML = '';
                    const p = document.createElement('p');
                    p.textContent = text;
                    editor.appendChild(p);
                } catch(e) {
                    editor.innerText = text;
                }

                editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                editor.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
        """, prompt)

        if not injected:
            # Fallback: Playwright locator fill
            for sel in cls.EDITOR_SELECTORS:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    try:
                        loc.click(timeout=1000)
                        loc.fill(prompt)
                        injected = True
                        break
                    except Exception:
                        continue

        if not injected:
            logger.log("❌ [GEMINI] Gemini 입력창을 찾지 못했습니다.", "ERROR")
            return None

        interruptible_wait(stop_event, 0.5)

        # 전송 버튼 탐색 및 클릭
        send_clicked = False
        for btn_sel in cls.SEND_BUTTON_SELECTORS:
            btn = page.locator(btn_sel).first
            if btn.count() > 0:
                try:
                    if btn.is_visible() and btn.is_enabled():
                        btn.click(timeout=1500)
                        send_clicked = True
                        break
                except Exception:
                    continue

        if not send_clicked:
            # Fallback: Enter 키 전송
            try:
                page.keyboard.press("Enter")
                send_clicked = True
            except Exception:
                pass

        logger.log("⏳ [GEMINI] 프롬프트 전송 완료. 답변 생성 대기 중...")

        # 답변 생성 완료 감지 루프
        start_time = time.time()
        previous_text = ""
        stable_since = None

        while time.time() - start_time < 40.0:
            if stop_event and stop_event.is_set():
                return None

            # 답변 컨테이너 탐색
            response_texts = []
            for sel in cls.RESPONSE_SELECTORS:
                locs = page.locator(sel)
                cnt = locs.count()
                if cnt > 0:
                    try:
                        txt = locs.last.inner_text().strip()
                        if txt:
                            response_texts.append(txt)
                    except Exception:
                        pass

            current_text = response_texts[-1] if response_texts else ""

            if current_text and current_text != previous_text:
                previous_text = current_text
                stable_since = time.time()
            elif current_text and stable_since and (time.time() - stable_since >= 1.5):
                # 1.5초 이상 텍스트 변화가 없고 중지 버튼이 사라진 경우 완료로 판단
                stop_btns = page.locator("button[aria-label*='중지'], button[aria-label*='Stop']")
                if stop_btns.count() == 0 or not stop_btns.first.is_visible():
                    break

            time.sleep(0.3)

        final_answer = previous_text.strip()
        if not final_answer:
            logger.log("⚠️ [GEMINI] 생성된 답변 텍스트를 읽어오지 못했습니다.", "WARNING")
            return None

        # 마크다운 코드 블록(```) 제거 및 정제
        if final_answer.startswith("```"):
            final_answer = final_answer.strip("`")
            if final_answer.startswith("text") or final_answer.startswith("markdown"):
                final_answer = final_answer.split("\n", 1)[-1]
        final_answer = final_answer.strip()

        # 1) Gemini 자체 '복사' 버튼 클릭 시도 (브라우저 네이티브 복사)
        try:
            copy_btns = page.locator("button[aria-label*='복사'], button[aria-label*='Copy'], button:has(mat-icon:text-is('content_copy'))")
            if copy_btns.count() > 0:
                copy_btns.last.click(timeout=1000)
        except Exception:
            pass

        # 2) OS 시스템 클립보드 복사
        cls.copy_to_os_clipboard(final_answer)

        logger.log(f"✨ [GEMINI] 댓글 생성 완료! (클립보드에 자동 복사됨)")
        logger.log(f"  📝 [GEMINI 결과]: \"{final_answer}\"")

        return final_answer
