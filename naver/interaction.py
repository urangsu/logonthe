import time
import threading
from typing import Optional, Tuple
from playwright.sync_api import Page, Locator
from app.models import LikeState, LikeProcessResult, CommentSubmitState, CommentProcessResult, UserAction, WorkerCommandType, FeedPost
from app.errors import BrowserDisconnectedError
from naver.resolver import MobileDOMResolver
from naver.editor_adapter import CommentEditorAdapter
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState
from services.like_transaction import LikeTransactionService
from services.clipboard_bridge import ClipboardCommandBridge
from browser.session import interruptible_wait, ensure_page_alive
from src.logger import logger


class LikeInteractionService:
    @staticmethod
    def resolve_like_state(page: Page, like_btn: Optional[Locator] = None) -> LikeState:
        res = LikeTransactionService.resolve_like_state(page, like_btn)
        return res.state

    @classmethod
    def safe_process_like(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None,
        post: Optional[FeedPost] = None
    ) -> LikeProcessResult:
        return LikeTransactionService.execute_like_transaction(page, stop_event, post=post)


class CommentInteractionService:
    @staticmethod
    def install_keyboard_listener(page: Page):
        """
        Document 레벨 캡처링 키보드 및 Delegated 마우스 등록/닫기 이벤트 리스너 설치
        (표준 CSS 매칭만 사용하여 브라우저 evaluate 내 SyntaxError 완전 방지)
        """
        ensure_page_alive(page)

        context = MobileDOMResolver.get_comment_editor_context(page)
        frame = context["frame"] if context else page.main_frame
        frame.evaluate("""
            () => {
                window.__NAVER_FEED_ACTION__ = null;
                window.__NAVER_COMMENT_SUBMITTED_FLAG__ = false;

                if (window.__NAVER_FEED_KEY_HANDLER__) {
                    document.removeEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
                }
                if (window.__NAVER_FEED_CLICK_HANDLER__) {
                    document.removeEventListener('click', window.__NAVER_FEED_CLICK_HANDLER__, true);
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

                // 2. Delegated 마우스 클릭 리스너 (동적 DOM 재생성에도 안전하게 등록 버튼 감지)
                window.__NAVER_FEED_CLICK_HANDLER__ = (e) => {
                    const rawBtn = e.target.closest ? e.target.closest('button, a, input[type="submit"]') : null;
                    if (!rawBtn) return;

                    const isSubmit = rawBtn.matches('.u_cbox_btn_upload') ||
                                     rawBtn.matches('button[data-action="comment#upload"]') ||
                                     (rawBtn.tagName === 'BUTTON' && (rawBtn.textContent || '').trim() === '등록');

                    if (isSubmit) {
                        window.__NAVER_COMMENT_SUBMISSION_BASELINE__ = Array.from(
                            document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']")
                        ).filter(item => /(?:^|[,{\\s])mine\\s*:\\s*true(?:[,}\\s]|$)/i.test(item.getAttribute('data-info') || '') ||
                                         (item.className || '').split(/\\s+/).includes('u_cbox_type_mine')).map(item => ({
                            info: item.getAttribute('data-info') || '',
                            text: (item.querySelector('.u_cbox_contents, .u_cbox_text_mention, p.text')?.innerText || '').trim()
                        }));
                        const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                        window.__NAVER_COMMENT_FINAL_TEXT__ = editor ? (editor.innerText || editor.value || '').trim() : '';
                        window.__NAVER_COMMENT_SUBMITTED_FLAG__ = true;
                        window.__NAVER_FEED_ACTION__ = 'SUBMIT_MANUAL';
                        return;
                    }

                    const isClose = rawBtn.matches('.u_cbox_btn_close') ||
                                    rawBtn.matches('button[data-action="comment#close"]') ||
                                    rawBtn.matches('a._close') ||
                                    rawBtn.matches('button.btn_close');

                    if (isClose) {
                        window.__NAVER_FEED_ACTION__ = 'CLOSED';
                    }
                };

                document.addEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
                document.addEventListener('click', window.__NAVER_FEED_CLICK_HANDLER__, true);
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
        ensure_page_alive(page)

        open_btn = MobileDOMResolver.get_comment_button(page)
        if open_btn and open_btn.count() > 0:
            try:
                if open_btn.is_visible():
                    open_btn.scroll_into_view_if_needed(timeout=1500)
                    open_btn.click(timeout=1500)
            except Exception:
                pass

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                return False, "stop_requested"

            ensure_page_alive(page)

            # 1. 실제 화면에 보이는 로그인 요구 감지 (hidden template 오탐 방지)
            login_box = page.locator(".u_cbox_write_box.u_cbox_type_logged_out, .u_cbox_guide").first
            if login_box and login_box.count() > 0:
                try:
                    if login_box.is_visible() and "로그인" in (login_box.inner_text() or ""):
                        return False, "login_required"
                except Exception:
                    pass

            # 2. 명시적 비활성화 안내 감지
            disabled_box = page.locator(".u_cbox_none, .u_cbox_notice_disabled, div:text-is('댓글을 작성할 수 없습니다')").first
            if disabled_box and disabled_box.count() > 0:
                try:
                    if disabled_box.is_visible():
                        return False, "comment_disabled"
                except Exception:
                    pass

            # 3. 에디터 준비 완료 확인
            if CommentEditorAdapter.is_visible(page):
                return True, "ready"

            # 4. write_box 클릭 시도
            write_box = MobileDOMResolver.get_comment_write_box(page)
            if write_box and write_box.count() > 0:
                try:
                    if write_box.is_visible():
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
        ensure_page_alive(page)

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
        editor_context = MobileDOMResolver.get_comment_editor_context(page)
        presence_frame = editor_context["frame"] if editor_context else page
        presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(presence_frame, stop_event=stop_event)
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
        command_bridge: Optional[ClipboardCommandBridge] = None,
        preset: str = "community"
    ) -> UserAction:
        while True:
            if stop_event and stop_event.is_set():
                return UserAction.STOP

            ensure_page_alive(page)

            # 1. UI 스레드로부터 전달된 클립보드 적용 명령 처리
            if command_bridge:
                cmd = command_bridge.pop_command()
                if cmd and cmd.kind == WorkerCommandType.APPLY_CLIPBOARD_COMMENT:
                    from services.comments.community_rhythm import FinalQualityGate
                    gate_res = FinalQualityGate.validate_final_text(cmd.text, preset=preset, source="clipboard")
                    if gate_res.valid:
                        if CommentEditorAdapter.set_text(page, cmd.text):
                            logger.log("  📋 [COMMENT] 클립보드 텍스트를 댓글 에디터에 적용했습니다.")
                    else:
                        logger.log(f"  ⚠️ [COMMENT] 클립보드 텍스트가 품질 게이트를 통과하지 못해 적용을 거부했습니다: [{gate_res.code}] {gate_res.reason} (매칭: {gate_res.matched})", "WARNING")

            # 2. 브라우저 이벤트 상태 확인
            try:
                editor_context = MobileDOMResolver.get_comment_editor_context(page)
                action_frame = editor_context["frame"] if editor_context else page.main_frame
                action_data = action_frame.evaluate("""
                    () => {
                        const act = window.__NAVER_FEED_ACTION__;
                        window.__NAVER_FEED_ACTION__ = null;
                        return act;
                    }
                """)
                if action_data == "SUBMIT":
                    return UserAction.SUBMIT
                elif action_data == "SUBMIT_MANUAL":
                    return UserAction.NATIVE_SUBMIT
                elif action_data in ("SKIP", "CLOSED"):
                    return UserAction.SKIP
            except Exception:
                pass

            interruptible_wait(stop_event, 0.15)

    @classmethod
    def read_final_text(cls, page: Page) -> str:
        """마우스 클릭 시 보존된 텍스트 또는 현재 에디터 텍스트 추출"""
        try:
            context = MobileDOMResolver.get_comment_editor_context(page)
            frame = context["frame"] if context else page.main_frame
            saved_text = frame.evaluate("() => window.__NAVER_COMMENT_FINAL_TEXT__ || ''")
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
        stop_event: Optional[threading.Event] = None,
        preset: str = "community",
        click: bool = True,
    ) -> CommentSubmitState:
        """
        댓글 등록 버튼 클릭 및 Fail-closed 검증 (에디터 클리어 및 서버 목록 내 댓글 등장 확인)
        """
        ensure_page_alive(page)

        from services.comments.community_rhythm import FinalQualityGate
        gate_res = FinalQualityGate.validate_final_text(final_text, preset=preset, source="user_submission")
        if not gate_res.valid:
            logger.log(f"  ❌ [COMMENT] 등록 직전 품질 게이트 실패로 제출을 중단합니다: [{gate_res.code}] {gate_res.reason} (매칭: {gate_res.matched})", "ERROR")
            return CommentSubmitState.FAILED

        editor_context = MobileDOMResolver.get_comment_editor_context(page)
        submit_context = MobileDOMResolver.get_comment_submit_context(page, editor_context["frame"] if editor_context else None)
        if not submit_context:
            logger.log("  ❌ [COMMENT] 등록 버튼을 찾지 못했습니다.", "ERROR")
            return CommentSubmitState.FAILED
        btn = submit_context["button"]
        # Capture server truth before the click so an older own comment cannot
        # be mistaken for the comment submitted in this action.
        comment_frame = submit_context.get("frame") or (editor_context.get("frame") if editor_context else page.main_frame)
        baseline = ServerCommentDuplicateGuard.capture_submission_baseline(comment_frame)
        try:
            if btn.is_disabled():
                logger.log("  ❌ [COMMENT] 등록 버튼이 비활성 상태입니다.", "ERROR")
                return CommentSubmitState.FAILED
            if click:
                try:
                    btn.scroll_into_view_if_needed(timeout=1000)
                    btn.click(timeout=1000)
                except Exception as exc:
                    logger.log(f"  ❌ [COMMENT] 등록 버튼 클릭 실패: {exc}", "ERROR")
                    return CommentSubmitState.FAILED
            else:
                logger.log("  ℹ️ [COMMENT][SUBMIT_NATIVE_CLICK] 네이버 기본 등록 동작을 검증합니다")
        except Exception:
            return CommentSubmitState.FAILED

        try:
            # 에디터가 비워지는 것은 네이버의 로컬 UI 반응일 뿐 등록 증거가 아니다.
            # 서버 목록에서 본인 댓글이 확인될 때만 SUBMITTED를 반환한다.
            unknown_seen = False
            for delay in (0.5, 1.0, 2.0):
                interruptible_wait(stop_event, delay)
                presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(
                    comment_frame,
                    stop_event=stop_event,
                    baseline=baseline,
                    expected_text=final_text,
                )
                if presence.state == CommentPresenceState.PRESENT:
                    logger.log("  ✅ [COMMENT][SERVER_VERIFIED] 본인 댓글이 서버 목록에 확인되었습니다")
                    return CommentSubmitState.SUBMITTED
                if presence.state == CommentPresenceState.UNKNOWN:
                    unknown_seen = True
                    logger.log(
                        "  ⚠️ [COMMENT] 서버 댓글 목록 확인이 불완전합니다. 재확인합니다",
                        "WARNING",
                    )

            logger.log(
                "  ❌ [COMMENT] " + ("server_verification_unavailable" if unknown_seen else "server_comment_not_found") + ": 서버 목록에 본인 댓글이 확인되지 않았습니다",
                "ERROR",
            )
            return CommentSubmitState.FAILED
        except Exception as e:
            logger.log(f"  ❌ [COMMENT] 등록 검증 중 예외: {e}", "ERROR")
            return CommentSubmitState.FAILED
