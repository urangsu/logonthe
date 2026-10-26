import time
import uuid
import subprocess
import threading
from typing import Optional, List
from playwright.sync_api import Page, Locator
from browser.session import interruptible_wait, ensure_page_alive
from services.draft import DraftService
from services.comments.validators import PositiveSafetyValidator
from services.comments.intents import CommentCandidate, ReactionIntent, FirstPersonIntent
from src.logger import logger


class GeminiWebBridge:
    """
    Google Gemini 웹페이지(gemini.google.com)와 Playwright로 상호작용하여
    1) 프롬프트 자동 입력 및 전송
    2) ResponseSnapshot 및 스트리밍 응답 완료 감지
    3) Request ID 마커 파싱 및 PositiveSafetyValidator 검증
    4) 최종 답변 추출 및 OS 클립보드 복사
    를 수행하는 브릿지 서비스 (v5.0)
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
        "button[aria-label*='submit']"
    ]

    # 탑레벨 답변 컨테이너 셀렉터
    TOP_LEVEL_RESPONSE_SELECTORS = [
        "model-response",
        "div.response-container"
    ]

    @classmethod
    def ensure_open(cls, page: Page, target_url: str = GEMINI_DEFAULT_URL, stop_event: Optional[threading.Event] = None) -> bool:
        """Gemini 페이지로 이동 및 로드 확인"""
        ensure_page_alive(page)
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
        """OS 클립보드에 텍스트 복사"""
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
        stop_event: Optional[threading.Event] = None,
        preset: str = "community",
        request_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Managed Playwright Gemini 탭을 통한 댓글 자동 생성 (v5.0):
        1) Request ID 마커 및 ResponseSnapshot(전송 전 응답 개수) 확보
        2) 에디터 텍스트 주입 및 Read-back 검증
        3) 전송 후 신규 응답 안정화 감지
        4) Request ID 마커 파싱 및 clean_ai_response
        5) PositiveSafetyValidator 검증
        """
        req_id = request_id or uuid.uuid4().hex[:8]
        if not cls.ensure_open(page, target_url=gemini_url, stop_event=stop_event):
            return None

        # 1. 전송 전 탑레벨 응답 개수 스냅샷
        before_count = 0
        for sel in cls.TOP_LEVEL_RESPONSE_SELECTORS:
            cnt = page.locator(sel).count()
            if cnt > before_count:
                before_count = cnt

        # 2. 에디터 탐색 및 입력
        injected = False
        for sel in cls.EDITOR_SELECTORS:
            editor = page.locator(sel).first
            if editor.count() > 0:
                try:
                    if editor.is_visible():
                        editor.click(timeout=1500)
                        editor.fill(prompt, timeout=2000)
                        injected = True
                        break
                except Exception:
                    continue

        if not injected:
            logger.log("❌ [GEMINI] Gemini 입력창을 찾지 못했습니다.", "ERROR")
            return None

        interruptible_wait(stop_event, 0.5)

        # 3. 전송 버튼 클릭
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
            try:
                page.keyboard.press("Enter")
                send_clicked = True
            except Exception:
                pass

        logger.log("⏳ [GEMINI] 프롬프트 전송 완료. 신규 답변 생성 대기 중...")

        # 4. 답변 생성 완료 감지 루프
        start_time = time.time()
        previous_text = ""
        stable_since = None

        while time.time() - start_time < 40.0:
            if stop_event and stop_event.is_set():
                return None

            ensure_page_alive(page)

            # 새 탑레벨 응답 컨테이너 확인
            top_responses = page.locator("model-response, div.response-container")
            current_count = top_responses.count()

            if current_count > before_count:
                latest_node = top_responses.last
                # 내부 실제 콘텐츠 요소 우선 탐색
                content_loc = latest_node.locator("message-content, div.markdown, div.model-response-text, .response-body-inner").first
                if content_loc.count() > 0:
                    try:
                        cur_txt = content_loc.inner_text().strip()
                    except Exception:
                        cur_txt = ""
                else:
                    try:
                        cur_txt = latest_node.inner_text().strip()
                    except Exception:
                        cur_txt = ""

                if cur_txt and cur_txt != previous_text:
                    previous_text = cur_txt
                    stable_since = time.time()
                elif cur_txt and stable_since and (time.time() - stable_since >= 1.5):
                    # 중지 버튼 소멸 확인
                    stop_btns = page.locator("button[aria-label*='중지'], button[aria-label*='Stop']")
                    if stop_btns.count() == 0 or not stop_btns.first.is_visible():
                        break

            time.sleep(0.3)

        # 5. Request ID 마커 파싱 및 정제
        final_answer = DraftService.clean_ai_response(previous_text, expected_request_id=req_id)
        if not final_answer:
            logger.log("⚠️ [GEMINI] 유효한 답변 텍스트를 읽어오지 못했습니다. (로컬 엔진으로 전환)", "WARNING")
            return None

        # 6. FinalQualityGate 검증
        from services.comments.community_rhythm import FinalQualityGate
        gate_res = FinalQualityGate.validate_final_text(final_answer, preset=preset, source="gemini")
        if not gate_res.valid:
            logger.log(f"⚠️ [GEMINI] AI 생성 댓글이 품질 게이트를 통과하지 못했습니다 ([{gate_res.code}] {gate_res.reason} / 매칭: {gate_res.matched}). 로컬 엔진으로 전환합니다.", "WARNING")
            return None

        # 7. OS 클립보드 복사
        cls.copy_to_os_clipboard(final_answer)

        logger.log(f"✨ [GEMINI] 댓글 생성 완료! (클립보드에 자동 복사됨)")
        logger.log(f"  📝 [GEMINI 결과]: \"{final_answer}\"")

        return final_answer
