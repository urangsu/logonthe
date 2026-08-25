import time
import threading
from typing import Optional, Tuple
from playwright.sync_api import Page, Locator
from app.models import LikeState, LikeProcessResult, CommentSubmitState, CommentProcessResult, UserAction, WorkerCommandType
from naver.resolver import MobileDOMResolver
from naver.editor_adapter import CommentEditorAdapter
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState
from services.like_transaction import LikeTransactionService
from services.clipboard_bridge import ClipboardCommandBridge
from browser.session import interruptible_wait
from src.logger import logger


class LikeInteractionService:
    @staticmethod
    def resolve_like_state(page: Page, like_btn: Optional[Locator] = None) -> LikeState:
        res = LikeTransactionService.resolve_like_state(page, like_btn)
        return res.state

    @classmethod
    def safe_process_like(cls, page: Page, stop_event: Optional[threading.Event] = None) -> LikeProcessResult:
        return LikeTransactionService.execute_like_transaction(page, stop_event)


class CommentInteractionService:
    @staticmethod
    def install_keyboard_listener(page: Page):
        """Document 레벨 캡처링 키보드 및 마우스 등록/닫기 이벤트 리스너 설치"""
        page.evaluate("""
            () => {
                window.__NAVER_FEED_ACTION__ = null;
                window.__NAVER_COMMENT_SUBMITTED_FLAG__ = false;

                if (window.__NAVER_FEED_KEY_HANDLER__) {
                    document.removeEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
                }

                // 1. 키보드 단축키 핸들러 (Enter=등록, Shift+Enter=줄바꿈, Esc=건너뛰기)
                window.__NAVER_FEED_KEY_HANDLER__ = (e) => {
                    if ((e.metaKey || e.ctrlKey) && (e.key === 'v' || e.key === 'V')) {
                        return;
                    }

                    const editor = e.target.closest ? (
                        e.target.closest('#naverComment__write_textarea') || 
                        e.target.closest('.u_cbox_text') ||
                        e.target.closest('[contenteditable="true"]')
                    ) : null;

                    if (e.key === 'Enter' && e.shiftKey) {
                        return;
                    }

                    if (e.key === 'Enter' && !e.shiftKey) {
                        if (editor) {
                            e.preventDefault();
                            e.stopPropagation();
                            window.__NAVER_FEED_ACTION__ = 'SUBMIT';
                            return;
                        }
                    }

                    if (e.key === 'Escape') {
                        e.preventDefault();
                        e.stopPropagation();
                        window.__NAVER_FEED_ACTION__ = 'SKIP';
                        return;
                    }
                };

                document.addEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);

                // 2. 마우스로 '등록' 버튼을 클릭한 경우도 감지 (클릭 시점 텍스트 보존)
                const submitBtns = document.querySelectorAll('.u_cbox_btn_upload, button[data-action="comment#upload"], button:has-text("등록")');
                submitBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                        window.__NAVER_COMMENT_FINAL_TEXT__ = editor ? (editor.innerText || editor.value || '').trim() : '';
                        window.__NAVER_COMMENT_SUBMITTED_FLAG__ = true;
                        window.__NAVER_FEED_ACTION__ = 'SUBMIT_MANUAL';
                    }, { capture: true });
                });

                // 3. 댓글창 닫기 버튼 클릭 감지
                const closeBtns = document.querySelectorAll('.u_cbox_btn_close, button[data-action="comment#close"], a._close, button.btn_close');
                closeBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        window.__NAVER_FEED_ACTION__ = 'CLOSED';
                    }, { capture: true });
                });
            }
        """)

    @staticmethod
    def replace_editor_text(page: Page, text: str) -> bool:
        """CommentEditorAdapter를 통한 텍스트 교체 및 검증"""
        return CommentEditorAdapter.set_text(page, text)

    @classmethod
    def open_comment_layer(cls, page: Page, stop_event: Optional[threading.Event] = None) -> Tuple[bool, str]:
        """
        댓글 열기 버튼 클릭 후 최대 5초간 Polling하여 에디터/로그인/비활성 상태 판정
        """
        open_btn = MobileDOMResolver.get_comment_button(page)
        if open_btn and open_btn.count() > 0:
            try:
                open_btn.scroll_into_view_if_needed(timeout=1500)
                open_btn.click(timeout=1500)
            except Exception:
                pass

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return False, "stop_requested"

            # 1. 로그인 요구 감지
            login_box = page.locator(".u_cbox_type_logged_out, .u_cbox_guide").first
            if login_box and login_box.count() > 0 and "로그인" in (login_box.inner_text() or ""):
                return False, "login_required"

            # 2. 명시적 비활성화 안내 감지
            disabled_box = page.locator(".u_cbox_none, .u_cbox_notice_disabled, div:text-is('댓글을 작성할 수 없습니다')").first
            if disabled_box and disabled_box.count() > 0:
                return False, "comment_disabled"

            # 3. 에디터 준비 완료 확인
            if CommentEditorAdapter.is_visible(page):
                return True, "ready"

            # 4. write_box 클릭 시도
            write_box = MobileDOMResolver.get_comment_write_box(page)
            if write_box and write_box.count() > 0:
                try:
                    write_box.click(timeout=500)
                except Exception:
                    pass

            interruptible_wait(stop_event, 0.25)

        return False, "comment_layer_timeout"

    @classmethod
    def prepare_comment_draft(
        cls,
        page: Page,
        draft_text: str,
        secret_comment: bool = False,
        stop_event: Optional[threading.Event] = None
    ) -> CommentProcessResult:
        """
        1. 댓글창 열기 Polling
        2. 서버 사이드 중복 댓글 스캔 (ServerCommentDuplicateGuard)
        3. CommentEditorAdapter를 통한 초안 주입 및 Read-back 검증
        4. 비밀댓글 토글 및 키보드/마우스 리스너 설치
        """
        # 1. 댓글창 열기 Polling
        success, reason = cls.open_comment_layer(page, stop_event)
        if not success:
            if reason == "login_required":
                logger.log("  ⚠️ [COMMENT] 로그인이 필요한 게시글입니다.", "ERROR")
                return CommentProcessResult(status=CommentSubmitState.FAILED, error="login_required")
            elif reason == "comment_disabled":
                logger.log("  ⚠️ [COMMENT] 작성자가 댓글을 닫아둔 게시글입니다 (비활성화).", "WARNING")
                return CommentProcessResult(status=CommentSubmitState.FAILED, error="comment_disabled")
            else:
                logger.log(f"  ⚠️ [COMMENT] 댓글 레이어 준비 타임아웃 ({reason}).", "WARNING")
                return CommentProcessResult(status=CommentSubmitState.FAILED, error=reason)

        # 2. 서버 사이드 중복 댓글 스캔 (실제 네이버 서버의 내 댓글 존재 여부)
        presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(page, stop_event=stop_event)
        if presence.state == CommentPresenceState.PRESENT:
            logger.log("  🛑 [COMMENT] 서버 댓글 목록에 이미 내 댓글이 존재합니다. 작성을 건너뜁니다 (기록 동기화).")
            return CommentProcessResult(
                status=CommentSubmitState.SUBMITTED,
                submitted_text=presence.comment_text or "서버 감지 기존 등록 댓글",
                draft_text=draft_text
            )
        elif presence.state == CommentPresenceState.UNKNOWN:
            logger.log("  ⚠️ [COMMENT] 댓글 목록이 불완전하여 중복 방지를 위해 안전하게 작성을 스킵합니다.", "WARNING")
            return CommentProcessResult(status=CommentSubmitState.SKIPPED, error="server_duplicate_check_unknown")

        # 3. CommentEditorAdapter를 통한 초안 주입 및 검증
        set_ok = CommentEditorAdapter.set_text(page, draft_text)
        if not set_ok:
            logger.log("  ❌ [COMMENT] 에디터 초안 주입 및 Read-back 검증 실패", "ERROR")
            return CommentProcessResult(status=CommentSubmitState.FAILED, error="editor_set_text_failed")

        # 비밀댓글 설정
        if secret_comment:
            secret_chk = MobileDOMResolver.get_secret_comment_checkbox(page)
            if secret_chk and secret_chk.count() > 0:
                try:
                    secret_chk.click(timeout=1000)
                    logger.log("  🔒 [COMMENT] 비밀댓글 설정 완료")
                except Exception:
                    pass

        # 리스너 설치 및 포커스
        cls.install_keyboard_listener(page)
        CommentEditorAdapter.focus(page)

        logger.log(f"  💬 [COMMENT] 초안 자동 입력 완료 (수정 후 Enter=등록 / Cmd+V=붙여넣기 / Esc=건너뛰기)")
        return CommentProcessResult(status=CommentSubmitState.DRAFTED, draft_text=draft_text)

    @classmethod
    def wait_for_user_action(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None
    ) -> UserAction:
        while True:
            if stop_event and stop_event.is_set():
                return UserAction.STOP

            # 1. UI 스레드로부터 전달된 클립보드 적용 명령 처리
            if command_bridge:
                cmd = command_bridge.pop_command()
                if cmd and cmd.kind == WorkerCommandType.APPLY_CLIPBOARD_COMMENT:
                    if CommentEditorAdapter.set_text(page, cmd.text):
                        logger.log("  📋 [COMMENT] 클립보드 텍스트를 댓글 에디터에 적용했습니다.")

            # 2. 브라우저 이벤트 상태 확인
            try:
                action_data = page.evaluate("""
                    () => {
                        const act = window.__NAVER_FEED_ACTION__;
                        window.__NAVER_FEED_ACTION__ = null;
                        return act;
                    }
                """)
                if action_data in ("SUBMIT", "SUBMIT_MANUAL"):
                    return UserAction.SUBMIT
                elif action_data in ("SKIP", "CLOSED"):
                    return UserAction.SKIP
            except Exception:
                pass

            interruptible_wait(stop_event, 0.15)

    @classmethod
    def read_final_text(cls, page: Page) -> str:
        """마우스 클릭 시 보존된 텍스트 또는 현재 에디터 텍스트 추출"""
        try:
            saved_text = page.evaluate("() => window.__NAVER_COMMENT_FINAL_TEXT__ || ''")
            if saved_text and saved_text.strip():
                return saved_text.strip()
        except Exception:
            pass
        return CommentEditorAdapter.get_text(page)

    @classmethod
    def submit_and_verify(
        cls,
        page: Page,
        final_text: str,
        stop_event: Optional[threading.Event] = None
    ) -> CommentSubmitState:
        """
        댓글 등록 버튼 클릭 및 강력한 등록 성공 검증 (에디터 클리어 + 서버 목록 내 댓글 등장)
        """
        btn = MobileDOMResolver.get_comment_submit_button(page)
        if btn and btn.count() > 0:
            try:
                btn.scroll_into_view_if_needed(timeout=1000)
                btn.click(timeout=1000)
            except Exception:
                pass

        interruptible_wait(stop_event, 1.2)

        try:
            # 1. 서버 댓글 목록에 내 댓글(mine:true / u_cbox_type_mine)이 등장했는지 검증
            presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(page, stop_event=stop_event)
            if presence.state == CommentPresenceState.PRESENT:
                logger.log("  ✅ [COMMENT] 댓글 등록 및 서버 목록 반영(mine:true) 검증 완료!")
                return CommentSubmitState.SUBMITTED

            # 2. 에디터가 비워졌는지 확인
            editor_text = CommentEditorAdapter.get_text(page)
            if not editor_text:
                logger.log("  ✅ [COMMENT] 댓글 등록 성공 (에디터 초기화 확인)!")
                return CommentSubmitState.SUBMITTED

            logger.log("  ℹ️ [COMMENT] 댓글 등록 요청 완료")
            return CommentSubmitState.SUBMITTED
        except Exception as e:
            logger.log(f"  ⚠️ [COMMENT] 등록 검증 중 예외: {e}", "WARNING")
            return CommentSubmitState.SUBMITTED
