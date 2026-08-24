import time
import threading
from typing import Optional, Tuple
from playwright.sync_api import Page, Locator
from app.models import LikeState, LikeProcessResult, CommentSubmitState, CommentProcessResult, UserAction, FailureReason
from naver.resolver import MobileDOMResolver
from browser.session import interruptible_wait
from src.logger import logger


class LikeInteractionService:
    @staticmethod
    def resolve_like_state(page: Page, like_btn: Optional[Locator] = None) -> LikeState:
        """
        DOM 요소를 다각도로 정밀 검사하여 LikeState(LIKED, NOT_LIKED, UNKNOWN) 판별
        절대 UNKNOWN 상태에서 임의로 클릭하지 않습니다.
        """
        try:
            btn = like_btn if like_btn else MobileDOMResolver.get_like_button(page)
            if not btn or btn.count() == 0:
                return LikeState.UNKNOWN

            # Playwright Locator JavaScript evaluation
            state_str = btn.evaluate("""
                el => {
                    const outer = el.outerHTML || '';
                    const inner = el.innerHTML || '';
                    const cls = el.className || '';
                    const ariaPressed = el.getAttribute('aria-pressed');

                    // 1. 확실한 LIKED 시그니처
                    if (outer.includes('__reaction__like') || inner.includes('__reaction__like')) return 'liked';
                    if (ariaPressed === 'true') return 'liked';
                    if (cls.includes('_on') || cls.includes(' active')) return 'liked';

                    // 숫자 카운트 색상 검사 (네이버 활성 초록색 rgb(3, 199, 90))
                    const countEl = el.querySelector('._count, .u_likeit_text, .num, span[class*="count"]');
                    if (countEl) {
                        const style = window.getComputedStyle(countEl);
                        const color = style.color || '';
                        if (color.includes('3, 199, 90') || color.includes('03C75A') || color.includes('03c75a')) return 'liked';
                    }

                    // 2. 확실한 NOT_LIKED 시그니처
                    if (outer.includes('__reaction__zeroface') || inner.includes('__reaction__zeroface')) return 'not_liked';
                    if (ariaPressed === 'false') return 'not_liked';
                    if (cls.includes('off')) return 'not_liked';

                    return 'unknown';
                }
            """)
            if state_str == "liked":
                return LikeState.LIKED
            elif state_str == "not_liked":
                return LikeState.NOT_LIKED
            return LikeState.UNKNOWN
        except Exception as e:
            logger.log(f"[LIKE] 공감 상태 판별 중 예외: {e}", "WARNING")
            return LikeState.UNKNOWN

    @classmethod
    def safe_process_like(cls, page: Page, stop_event: Optional[threading.Event] = None) -> LikeProcessResult:
        """
        안전한 공감 처리:
        - LIKED: 아무것도 하지 않음 (기존 공감 유지)
        - UNKNOWN: 클릭 절대 금지 (취소 방지)
        - NOT_LIKED: 클릭 후 상태 전이(LIKED) 검증
        """
        btn = MobileDOMResolver.get_like_button(page)
        if btn.count() == 0:
            return LikeProcessResult(state_before=LikeState.UNKNOWN, state_after=LikeState.UNKNOWN, error="like_button_not_found")

        state_before = cls.resolve_like_state(page, btn)

        if state_before == LikeState.LIKED:
            logger.log("  ❤️ [LIKE] 이미 공감 완료된 글입니다. (클릭 생략)")
            return LikeProcessResult(state_before=LikeState.LIKED, action_taken=False, state_after=LikeState.LIKED)

        if state_before == LikeState.UNKNOWN:
            logger.log("  ⚠️ [LIKE] 공감 상태를 명확히 판별할 수 없어 취소 방지를 위해 클릭을 건너뜁니다.", "WARNING")
            return LikeProcessResult(state_before=LikeState.UNKNOWN, action_taken=False, state_after=LikeState.UNKNOWN)

        # NOT_LIKED 상태인 경우에만 클릭
        logger.log("  🤍 [LIKE] 미공감 글 감지, 공감(하트)을 클릭합니다.")
        try:
            btn.scroll_into_view_if_needed(timeout=1500)
            btn.click(timeout=1500)
            interruptible_wait(stop_event, 0.6)

            state_after = cls.resolve_like_state(page, btn)
            if state_after == LikeState.LIKED:
                logger.log("  ✅ [LIKE] 공감 클릭 및 상태 전환(LIKED) 확인 완료!")
            else:
                logger.log(f"  ℹ️ [LIKE] 공감 클릭 완료 (후속 상태: {state_after.value})")

            return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=state_after)
        except Exception as e:
            logger.log(f"  ❌ [LIKE] 공감 클릭 실패: {e}", "ERROR")
            return LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=False, state_after=LikeState.UNKNOWN, error=str(e))


class CommentInteractionService:
    @staticmethod
    def install_keyboard_listener(page: Page):
        """Document 레벨 캡처링 키보드 리스너 설치"""
        page.evaluate("""
            () => {
                window.__NAVER_FEED_ACTION__ = null;

                if (window.__NAVER_FEED_KEY_HANDLER__) {
                    document.removeEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
                }

                window.__NAVER_FEED_KEY_HANDLER__ = (e) => {
                    const editor = e.target.closest ? (
                        e.target.closest('#naverComment__write_textarea') || 
                        e.target.closest('.u_cbox_text') ||
                        e.target.closest('[contenteditable="true"]')
                    ) : null;

                    if (!editor) return;

                    // Shift + Enter = 줄바꿈 허용
                    if (e.key === 'Enter' && e.shiftKey) {
                        return;
                    }

                    // Enter = 댓글 등록 승인
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        e.stopPropagation();
                        window.__NAVER_FEED_ACTION__ = 'SUBMIT';
                        return;
                    }

                    // Escape = 건너뛰기
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        e.stopPropagation();
                        window.__NAVER_FEED_ACTION__ = 'SKIP';
                        return;
                    }
                };

                document.addEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
            }
        """)

    @classmethod
    def prepare_comment_draft(
        cls,
        page: Page,
        draft_text: str,
        secret_comment: bool = False,
        stop_event: Optional[threading.Event] = None
    ) -> CommentProcessResult:
        """
        1. 댓글창 열기 버튼 클릭
        2. ContentEditable 에디터 확인 및 fill
        3. 비밀댓글 옵션 설정
        4. Document 키보드 리스너 설치 및 포커스
        """
        # 1. 댓글 열기 버튼 클릭
        open_btn = MobileDOMResolver.get_comment_open_button(page)
        if open_btn.count() > 0:
            try:
                open_btn.scroll_into_view_if_needed(timeout=1500)
                open_btn.click(timeout=1500)
                interruptible_wait(stop_event, 0.8)
            except Exception:
                pass

        # 2. 비로그인 안내 확인
        login_box = page.locator(".u_cbox_type_logged_out, .u_cbox_guide").first
        if login_box.count() > 0 and "로그인" in (login_box.inner_text() or ""):
            logger.log("  ⚠️ [COMMENT] 로그인이 필요한 게시글입니다.", "ERROR")
            return CommentProcessResult(status=CommentSubmitState.FAILED, error="login_required")

        # 3. 입력 영역 클릭으로 에디터 활성화
        write_box = MobileDOMResolver.get_comment_write_box(page)
        if write_box.count() > 0:
            try:
                write_box.click(timeout=1500)
                interruptible_wait(stop_event, 0.4)
            except Exception:
                pass

        # 4. 에디터 탐색 및 초안 입력
        editor = MobileDOMResolver.get_comment_editor(page)
        if editor.count() == 0:
            logger.log("  ⚠️ [COMMENT] 댓글 입력창을 찾을 수 없습니다. (댓글 비활성화 글)", "WARNING")
            return CommentProcessResult(status=CommentSubmitState.FAILED, error="comment_editor_not_found")

        try:
            editor.wait_for(state="visible", timeout=5000)
            # Playwright locator.fill() on contenteditable
            editor.fill(draft_text)

            # 비밀댓글 설정
            if secret_comment:
                secret_chk = MobileDOMResolver.get_secret_checkbox(page)
                if secret_chk.count() > 0:
                    try:
                        secret_chk.click(timeout=1000)
                        logger.log("  🔒 [COMMENT] 비밀댓글 설정 완료")
                    except Exception:
                        pass

            # 키보드 리스너 설치 및 포커스
            cls.install_keyboard_listener(page)
            editor.focus()

            logger.log(f"  💬 [COMMENT] 초안 자동 입력 완료 (수정 후 Enter=등록 / Shift+Enter=줄바꿈 / Esc=건너뛰기)")
            return CommentProcessResult(status=CommentSubmitState.DRAFTED, draft_text=draft_text)
        except Exception as e:
            logger.log(f"  ❌ [COMMENT] 초안 입력 실패: {e}", "ERROR")
            return CommentProcessResult(status=CommentSubmitState.FAILED, error=str(e))

    @staticmethod
    def wait_for_user_action(page: Page, stop_event: Optional[threading.Event] = None) -> UserAction:
        """사용자의 키보드 입력(Enter=SUBMIT, Esc=SKIP, Stop=STOP)을 비차단 폴링으로 대기"""
        while True:
            if stop_event and stop_event.is_set():
                return UserAction.STOP

            try:
                action_str = page.evaluate("() => window.__NAVER_FEED_ACTION__")
                if action_str == "SUBMIT":
                    return UserAction.SUBMIT
                elif action_str == "SKIP":
                    return UserAction.SKIP
            except Exception:
                # 브라우저가 닫힌 경우 등
                return UserAction.STOP

            time.sleep(0.1)

    @staticmethod
    def read_final_text(page: Page) -> str:
        """사용자가 수정한 최종 에디터 텍스트 추출"""
        try:
            editor = MobileDOMResolver.get_comment_editor(page)
            if editor.count() > 0:
                return editor.inner_text().strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def submit_and_verify(
        page: Page,
        final_text: str,
        stop_event: Optional[threading.Event] = None
    ) -> CommentSubmitState:
        """
        등록 버튼 클릭 후 성공 신호(에디터 비워짐, 신규 댓글 생성)를 엄격히 검증
        """
        submit_btn = MobileDOMResolver.get_comment_submit_button(page)
        if submit_btn.count() == 0:
            logger.log("  ⚠️ [COMMENT] 댓글 등록 버튼을 찾지 못했습니다.", "WARNING")
            return CommentSubmitState.FAILED

        try:
            logger.log("  🚀 [COMMENT] 댓글 등록 버튼을 클릭합니다...")
            submit_btn.scroll_into_view_if_needed(timeout=1500)
            submit_btn.click(timeout=1500)

            # 성공 신호 검증 (최대 3초간 폴링)
            verified = False
            start_t = time.time()
            while time.time() - start_t < 3.5:
                if stop_event and stop_event.is_set():
                    break

                try:
                    editor = MobileDOMResolver.get_comment_editor(page)
                    txt = editor.inner_text().strip() if editor.count() > 0 else ""
                    # 1. 에디터가 비워졌거나
                    if not txt or txt != final_text:
                        verified = True
                        break

                    # 2. 본문 댓글 목록에 내가 쓴 텍스트가 등장했는지 확인
                    if page.locator(f".u_cbox_contents:has-text('{final_text[:15]}')").count() > 0:
                        verified = True
                        break
                except Exception:
                    pass

                time.sleep(0.3)

            if verified:
                logger.log("  ✅ [COMMENT] 댓글 등록 및 성공 검증 완료!")
                return CommentSubmitState.SUBMITTED
            else:
                logger.log("  ⚠️ [COMMENT] 등록 버튼을 눌렀으나 서버 응답 검증이 불명확합니다 (UNKNOWN 처리)", "WARNING")
                return CommentSubmitState.UNKNOWN

        except Exception as e:
            logger.log(f"  ❌ [COMMENT] 댓글 등록 중 오류: {e}", "ERROR")
            return CommentSubmitState.FAILED
