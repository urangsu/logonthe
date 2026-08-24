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
        "div[contenteditable='true']",
        "textarea[aria-label*='프롬프트']",
        "textarea"
    ]

    # Gemini 전송 버튼 셀렉터 우선순위
    SEND_BUTTON_SELECTORS = [
        "button[aria-label*='프롬프트 보내기']",
        "button[aria-label*='전송']",
        "button[aria-label*='Send']",
        "button[data-test-id='send-button']",
        "button.send-button",
        "button:has(mat-icon:text-is('send'))",
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
        "div[class*='response_content']"
    ]

    @classmethod
    def ensure_open(cls, page: Page, target_url: str = GEMINI_DEFAULT_URL, stop_event: Optional[threading.Event] = None) -> bool:
        """Gemini 페이지로 이동 및 로드 확인"""
        current_url = page.url or ""
        if "gemini.google.com" in current_url:
            # 이미 열려 있는 경우 지정 URL이 다르면 이동
            if target_url and target_url != cls.GEMINI_DEFAULT_URL and target_url not in current_url:
                page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        else:
            url_to_go = target_url if target_url else cls.GEMINI_DEFAULT_URL
            logger.log(f"[GEMINI] Gemini 웹페이지 접속: {url_to_go}")
            try:
                page.goto(url_to_go, wait_until="domcontentloaded", timeout=25000)
                interruptible_wait(stop_event, 2.0)
            except Exception as e:
                logger.log(f"[GEMINI] 페이지 로드 안내: {e}", "WARNING")

        # 로그인/동의 확인
        if "accounts.google.com" in (page.url or ""):
            logger.log("⚠️ [GEMINI] Google 로그인이 필요합니다. [🌐 로그인 창 열기]에서 구글 로그인을 먼저 진행해 주세요.", "ERROR")
            return False

        return True

    @classmethod
    def get_editor(cls, page: Page) -> Optional[Locator]:
        for sel in cls.EDITOR_SELECTORS:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return None

    @classmethod
    def get_send_button(cls, page: Page) -> Optional[Locator]:
        for sel in cls.SEND_BUTTON_SELECTORS:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return None

    @classmethod
    def get_response_elements(cls, page: Page) -> Locator:
        for sel in cls.RESPONSE_SELECTORS:
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc
        return page.locator("model-response, message-content")

    @classmethod
    def copy_to_os_clipboard(cls, text: str):
        """OS 클립보드에 텍스트 복사 (macOS pbcopy 및 일반 클립보드 지원)"""
        if not text:
            return
        try:
            # macOS pbcopy 사용
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-8"))
        except Exception:
            try:
                import tkinter as tk
                r = tk.Tk()
                r.withdraw()
                r.clipboard_clear()
                r.clipboard_append(text)
                r.update()
                r.destroy()
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
        2. 프롬프트 입력 및 전송
        3. 생성 완료 대기 (텍스트 스트리밍 안정화)
        4. 최신 답변 추출 및 클립보드 복사
        """
        if not cls.ensure_open(page, target_url=gemini_url, stop_event=stop_event):
            return None

        # 전송 전 기존 답변 개수 파악
        responses = cls.get_response_elements(page)
        initial_count = responses.count()

        # 에디터 찾기
        editor = cls.get_editor(page)
        if not editor:
            logger.log("⚠️ [GEMINI] Gemini 입력창(에디터)을 찾을 수 없습니다. (화면 로딩 대기 중)", "WARNING")
            interruptible_wait(stop_event, 2.0)
            editor = cls.get_editor(page)

        if not editor:
            logger.log("❌ [GEMINI] Gemini 입력창을 찾지 못했습니다.", "ERROR")
            return None

        logger.log("🤖 [GEMINI] Gemini 입력창에 프롬프트 자동 입력 중...")
        try:
            editor.click(timeout=2000)
            editor.fill(prompt)
            interruptible_wait(stop_event, 0.5)

            # 전송 버튼 클릭 (또는 Enter)
            send_btn = cls.get_send_button(page)
            if send_btn and send_btn.is_visible():
                send_btn.click(timeout=1500)
            else:
                editor.press("Enter")

            logger.log("⏳ [GEMINI] 프롬프트 전송 완료. 답변 생성 대기 중...")

            # 답변 완료 감지 루프
            start_time = time.time()
            previous_text = ""
            stable_since = None

            while time.time() - start_time < 35.0:
                if stop_event and stop_event.is_set():
                    return None

                curr_responses = cls.get_response_elements(page)
                curr_count = curr_responses.count()

                if curr_count > 0:
                    latest = curr_responses.last
                    try:
                        current_text = latest.inner_text().strip()
                    except Exception:
                        current_text = ""

                    if current_text and current_text != previous_text:
                        previous_text = current_text
                        stable_since = time.time()
                    elif current_text and stable_since and (time.time() - stable_since >= 1.5):
                        # 1.5초간 텍스트 변화가 없고 중지 버튼이 없으면 생성 완료!
                        stop_btns = page.locator("button[aria-label*='중지'], button[aria-label*='Stop']")
                        if stop_btns.count() == 0 or not stop_btns.first.is_visible():
                            break

                time.sleep(0.3)

            # 최종 텍스트 추출 및 정제
            final_answer = previous_text.strip()
            if not final_answer:
                logger.log("⚠️ [GEMINI] 생성된 답변 내용을 읽어오지 못했습니다.", "WARNING")
                return None

            # 마크다운 코드 블록(```) 제거
            if final_answer.startswith("```"):
                final_answer = final_answer.strip("`")
                if final_answer.startswith("text") or final_answer.startswith("markdown"):
                    final_answer = final_answer.split("\n", 1)[-1]
            final_answer = final_answer.strip()

            # 클립보드에 자동 복사
            cls.copy_to_os_clipboard(final_answer)
            logger.log(f"✨ [GEMINI] 댓글 생성 완료! (클립보드에 자동 복사됨: '{final_answer[:35]}...')")

            return final_answer

        except Exception as e:
            logger.log(f"❌ [GEMINI] Gemini 생성 중 오류: {e}", "ERROR")
            return None
