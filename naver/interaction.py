import time
import uuid
import threading
from typing import Any, Optional, Tuple
from playwright.sync_api import Page, Locator
from app.models import (
    LikeState,
    LikeProcessResult,
    CommentSubmitState,
    CommentProcessResult,
    UserAction,
    WorkerCommandType,
    FeedPost,
    SubmitOrigin,
    CommentSubmitOutcome,
)
from app.errors import BrowserDisconnectedError
from naver.resolver import MobileDOMResolver
from naver.editor_adapter import CommentEditorAdapter
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState
from services.draft import normalize_naver_comment_text
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
    def install_keyboard_listener(page: Page, post_key: str = ""):
        """
        Document 레벨 캡처링 키보드 및 Delegated 마우스 등록/닫기 이벤트 리스너 설치
        (표준 CSS 매칭만 사용하여 브라우저 evaluate 내 SyntaxError 완전 방지)
        - 단일 제출 락 (window.tryAcquireSubmitLock)
        - 제출 락 롤백 (window.releaseSubmitLock)
        - 한글 IME 조합 보호 (compositionstart, compositionend, isComposing)
        - 사용자 직접 편집(input, keydown) 시 자동 등록 해제 및 상태 전환
        """
        ensure_page_alive(page)

        context = MobileDOMResolver.get_comment_editor_context(page)
        frame = context["frame"] if context else page.main_frame
        frame.evaluate("""
            (postKey) => {
                window.__NAVER_FEED_POST_KEY__ = postKey;
                window.__NAVER_FEED_ACTION__ = null;
                window.__NAVER_COMMENT_STATE__ = 'DRAFT_READY';
                window.__NAVER_COMMENT_SUBMITTED_FLAG__ = false;
                window.__NAVER_COMMENT_USER_DIRTY__ = false;
                window.__NAVER_COMMENT_LAST_INPUT_AT__ = 0;
                window.__NAVER_IS_COMPOSING__ = false;
                window.__NAVER_SUBMIT_LOCK_ACQUIRED__ = null;
                window.__NAVER_COMMENT_FINAL_TEXT__ = '';
                window.__NAVER_COMMENT_SUBMISSION_BASELINE__ = null;

                // 원자적 단일 제출 락 획득 함수 (Enter, 수동 클릭, 자동 타이머 간 경합 방지)
                window.tryAcquireSubmitLock = (source) => {
                    if (window.__NAVER_SUBMIT_LOCK_ACQUIRED__) {
                        return false;
                    }
                    if (window.__NAVER_COMMENT_STATE__ === 'SUBMITTING' ||
                        window.__NAVER_COMMENT_STATE__ === 'SUBMITTED' ||
                        window.__NAVER_COMMENT_STATE__ === 'CANCELLED') {
                        return false;
                    }
                    window.__NAVER_SUBMIT_LOCK_ACQUIRED__ = source;
                    window.__NAVER_COMMENT_STATE__ = 'SUBMITTING';
                    return true;
                };

                // 제출 전 검증 실패 시 제출 락 롤백 (재시도 허용)
                window.releaseSubmitLock = (source) => {
                    if (!source || window.__NAVER_SUBMIT_LOCK_ACQUIRED__ === source) {
                        window.__NAVER_SUBMIT_LOCK_ACQUIRED__ = null;
                        window.__NAVER_COMMENT_STATE__ = window.__NAVER_COMMENT_USER_DIRTY__ ? 'USER_EDITING' : 'DRAFT_READY';
                        return true;
                    }
                    return false;
                };

                if (window.__NAVER_FEED_KEY_HANDLER__) {
                    document.removeEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
                }
                if (window.__NAVER_FEED_CLICK_HANDLER__) {
                    document.removeEventListener('click', window.__NAVER_FEED_CLICK_HANDLER__, true);
                }
                if (window.__NAVER_FEED_INPUT_HANDLER__) {
                    document.removeEventListener('input', window.__NAVER_FEED_INPUT_HANDLER__, true);
                }
                if (window.__NAVER_COMPOSITION_START_HANDLER__) {
                    document.removeEventListener('compositionstart', window.__NAVER_COMPOSITION_START_HANDLER__, true);
                }
                if (window.__NAVER_COMPOSITION_END_HANDLER__) {
                    document.removeEventListener('compositionend', window.__NAVER_COMPOSITION_END_HANDLER__, true);
                }

                // 한글 IME 조합 상태 추적
                window.__NAVER_COMPOSITION_START_HANDLER__ = (e) => {
                    window.__NAVER_IS_COMPOSING__ = true;
                    window.__NAVER_COMMENT_USER_DIRTY__ = true;
                    window.__NAVER_COMMENT_STATE__ = 'USER_EDITING';
                };
                window.__NAVER_COMPOSITION_END_HANDLER__ = (e) => {
                    window.__NAVER_IS_COMPOSING__ = false;
                    window.__NAVER_COMMENT_USER_DIRTY__ = true;
                    window.__NAVER_COMMENT_STATE__ = 'USER_EDITING';
                    window.__NAVER_COMMENT_LAST_INPUT_AT__ = Date.now();
                };

                // 1. 키보드 단축키 핸들러 (Enter=등록, Shift+Enter=줄바꿈, Esc=건너뛰기)
                window.__NAVER_FEED_KEY_HANDLER__ = (e) => {
                    const isComposing = (e.keyCode === 13 && !e.isComposing) ? false : (e.isComposing || e.keyCode === 229 || window.__NAVER_IS_COMPOSING__ === true);
                    const editor = e.target.closest ? (
                        e.target.closest('#naverComment__write_textarea') || 
                        e.target.closest('.u_cbox_text') ||
                        e.target.closest('[contenteditable="true"]')
                    ) : null;

                    if (editor && e.key !== 'Escape') {
                        if (isComposing || (e.key !== 'Enter' || e.shiftKey)) {
                            window.__NAVER_COMMENT_USER_DIRTY__ = true;
                            window.__NAVER_COMMENT_STATE__ = 'USER_EDITING';
                            window.__NAVER_COMMENT_LAST_INPUT_AT__ = Date.now();
                        }
                    }

                    // 한글 조합 중의 키 입력(조합 확정 Enter 포함)은 제출하지 않음
                    if (isComposing) {
                        return;
                    }

                    if ((e.metaKey || e.ctrlKey) && (e.key === 'v' || e.key === 'V')) {
                        return;
                    }

                    if (e.key === 'Enter' && e.shiftKey) {
                        return;
                    }

                    if (e.key === 'Enter' && !e.shiftKey) {
                        const targetEditor = editor || document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                        if (targetEditor) {
                            e.preventDefault();
                            e.stopPropagation();
                            if (window.tryAcquireSubmitLock && !window.tryAcquireSubmitLock('user_enter')) {
                                return;
                            }
                            try {
                                if (document.activeElement && typeof document.activeElement.blur === 'function') {
                                    document.activeElement.blur();
                                }
                            } catch (err) {}
                            window.__NAVER_IS_COMPOSING__ = false;
                            window.__NAVER_COMMENT_FINAL_TEXT__ = (targetEditor.innerText || targetEditor.value || '').trim();
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
                        if (window.__NAVER_SUBMIT_PERMIT__) {
                            // 단일 소모성 제출 권한(permit) 원자적 소비: 최초 1회의 프로그램 클릭만 허용
                            const permit = window.__NAVER_SUBMIT_PERMIT__;
                            delete window.__NAVER_SUBMIT_PERMIT__;
                            window.__NAVER_LAST_CONSUMED_PERMIT__ = permit;
                            return;
                        }
                        if (window.tryAcquireSubmitLock && !window.tryAcquireSubmitLock('user_click')) {
                            e.preventDefault();
                            e.stopPropagation();
                            return;
                        }
                        window.__NAVER_COMMENT_SUBMISSION_BASELINE__ = Array.from(
                            document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']")
                        ).filter(item => /(?:^|[,{\\\\s])mine\\\\s*:\\\\s*true(?:[,}\\\\s]|$)/i.test(item.getAttribute('data-info') || '') ||
                                         (item.className || '').split(/\\\\s+/).includes('u_cbox_type_mine')).map(item => ({
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

                // 3. 에디터 텍스트 수정(input) 감지 리스너
                window.__NAVER_FEED_INPUT_HANDLER__ = (e) => {
                    if (window.__NAVER_PROGRAMMATIC_SET__) {
                        return;
                    }
                    const editor = e.target.closest ? (
                        e.target.closest('#naverComment__write_textarea') ||
                        e.target.closest('.u_cbox_text') ||
                        e.target.closest('[contenteditable="true"]')
                    ) : null;
                    if (editor) {
                        window.__NAVER_COMMENT_USER_DIRTY__ = true;
                        window.__NAVER_COMMENT_STATE__ = 'USER_EDITING';
                        window.__NAVER_COMMENT_LAST_INPUT_AT__ = Date.now();
                    }
                };

                document.addEventListener('keydown', window.__NAVER_FEED_KEY_HANDLER__, true);
                document.addEventListener('click', window.__NAVER_FEED_CLICK_HANDLER__, true);
                document.addEventListener('input', window.__NAVER_FEED_INPUT_HANDLER__, true);
                document.addEventListener('compositionstart', window.__NAVER_COMPOSITION_START_HANDLER__, true);
                document.addEventListener('compositionend', window.__NAVER_COMPOSITION_END_HANDLER__, true);
            }
        """, post_key)

    @staticmethod
    def replace_editor_text(page: Page, text: str) -> bool:
        """CommentEditorAdapter를 통한 텍스트 교체 및 검증"""
        return CommentEditorAdapter.set_text(page, text)

    @classmethod
    def open_comment_layer(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None,
        skip_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str]:
        """
        댓글 열기 버튼 탐색 및 클릭 후 최대 5초간 Polling하여
        에디터 준비(ready) / 로그인 필요 / 비활성 / 타임아웃 상태 판정 (상태 머신)
        """
        ensure_page_alive(page)

        if stop_event and stop_event.is_set():
            return False, "stop_requested"
        if skip_event and skip_event.is_set():
            logger.log("  ⏭️ [COMMENT_LAYER] skip_event 감지: 댓글 레이어 준비를 중단합니다.")
            return False, "user_skipped"

        # Step A: 에디터가 이미 열려 있는지 사전 검사 (OPEN-004)
        editor_ctx = MobileDOMResolver.get_comment_editor_context(page)
        if (editor_ctx and editor_ctx.get("editor") and editor_ctx["editor"].is_visible()) or CommentEditorAdapter.is_visible(page):
            logger.log("  [COMMENT_LAYER][ALREADY_READY] 에디터가 이미 열려 있어 버튼 클릭 없이 READY")
            return True, "ready"

        # Step B: 댓글 오프너 후보 전수 인벤토리 수집
        candidates = MobileDOMResolver.get_all_comment_opener_candidates(page)
        selector_matches = len(candidates)
        visible_matches = len([c for c in candidates if c.visible and c.enabled])

        # Step C: visible 후보가 없으면 레이지 렌더 영역으로 deterministic 스크롤 후 재탐색 (Section 6)
        if visible_matches == 0:
            try:
                page.evaluate("""() => {
                    const el = document.querySelector('.u_likeit_list_module, .u_likeit, #postViewArea, .se-main-container, footer, article, div[class*="Interact__"]');
                    if (el) {
                        el.scrollIntoView({ behavior: 'instant', block: 'end' });
                    } else {
                        window.scrollTo(0, document.body.scrollHeight * 0.7);
                    }
                }""")
            except Exception as sc_err:
                logger.log(f"  [COMMENT_LAYER][SCROLL_WARN] {sc_err}", "WARNING")

            interruptible_wait(stop_event, 0.3)
            candidates = MobileDOMResolver.get_all_comment_opener_candidates(page)
            selector_matches = len(candidates)
            visible_matches = len([c for c in candidates if c.visible and c.enabled])

        # 후보 판정: candidate 0 vs hidden-only
        if selector_matches == 0:
            logger.log(f"  ❌ [COMMENT_LAYER] candidate 0 -> comment_open_button_not_found (frameCount={len(page.frames) if hasattr(page, 'frames') else 1})", "WARNING")
            return False, "comment_open_button_not_found"

        if visible_matches == 0:
            logger.log(f"  ❌ [COMMENT_LAYER] hidden only -> comment_open_button_hidden_only (matches={selector_matches})", "WARNING")
            return False, "comment_open_button_hidden_only"

        # Step D: 최적의 visible + enabled 후보 선정 및 로깅
        visible_enabled = [c for c in candidates if c.visible and c.enabled]
        with_box = [c for c in visible_enabled if c.bbox and c.bbox.get("width", 0) > 0 and c.bbox.get("height", 0) > 0]
        selected = with_box[0] if with_box else visible_enabled[0]

        frame_id = getattr(selected.frame, "name", "") or getattr(selected.frame, "url", "")
        logger.log(
            f"  [COMMENT_LAYER][CANDIDATES] frameCount={len(page.frames) if hasattr(page, 'frames') else 1} "
            f"selectorMatches={selector_matches} visibleMatches={visible_matches} "
            f"selectedSelector={selected.selector} selectedFrame={frame_id} "
            f"selectedText={selected.text!r} selectedAria={selected.aria_label!r} bbox={selected.bbox}"
        )

        # Step E: 클릭 디스패치 및 DOM 교체 시 1회 재탐색 복구
        logger.log(
            f"  [COMMENT_LAYER][CLICK_ATTEMPT] selector={selected.selector} "
            f"frame={frame_id} visible={selected.visible} enabled={selected.enabled}"
        )
        click_success = False
        click_err_msg = ""
        try:
            selected.button.scroll_into_view_if_needed(timeout=1500)
            selected.button.click(timeout=1500)
            click_success = True
        except Exception as click_err:
            click_err_msg = str(click_err)
            logger.log(
                f"  ❌ [COMMENT_LAYER][CLICK_ERROR] type={type(click_err).__name__} message={click_err_msg}",
                "WARNING"
            )
            # DOM replacement recovery: re-resolve 1 time (OPEN-005)
            try:
                re_cand = MobileDOMResolver.get_comment_open_context(page)
                if re_cand and re_cand.visible and re_cand.enabled:
                    logger.log("  [COMMENT_LAYER][RE_RESOLVE] DOM 교체 감지 -> 재탐색된 오프너 버튼 클릭 1회 재시도")
                    re_cand.button.scroll_into_view_if_needed(timeout=1500)
                    re_cand.button.click(timeout=1500)
                    click_success = True
            except Exception as re_err:
                logger.log(f"  ❌ [COMMENT_LAYER][RE_CLICK_ERROR] {re_err}", "WARNING")

        # Step F: 클릭 후 상태 폴링 (최대 5초)
        started_at = time.monotonic()
        deadline = started_at + 5.0
        while time.monotonic() < deadline:
            if stop_event and stop_event.is_set():
                return False, "stop_requested"
            if skip_event and skip_event.is_set():
                logger.log("  ⏭️ [COMMENT_LAYER] skip_event 감지: 댓글 레이어 준비를 중단합니다.")
                return False, "user_skipped"
            ensure_page_alive(page)

            # 1. 로그인 요구 감지 (OPEN-007)
            frames = MobileDOMResolver._safe_get_frames(page)
            login_detected = False
            disabled_detected = False
            for f in frames:
                try:
                    login_loc = f.locator(".u_cbox_write_box.u_cbox_type_logged_out, .u_cbox_guide:has-text('로그인'), a:has-text('로그인한 사용자만')").first
                    if login_loc.count() > 0 and login_loc.is_visible():
                        txt = login_loc.inner_text() or ""
                        if "로그인" in txt:
                            login_detected = True
                            break
                except Exception:
                    pass
                try:
                    dis_loc = f.locator(".u_cbox_none, .u_cbox_notice_disabled, div:text-is('댓글을 작성할 수 없습니다')").first
                    if dis_loc.count() > 0 and dis_loc.is_visible():
                        disabled_detected = True
                        break
                except Exception:
                    pass

            if login_detected:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.log(f"  [COMMENT_LAYER][POST_CLICK] loginFound=true elapsedMs={elapsed_ms}")
                return False, "comment_login_required"

            if disabled_detected:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.log(f"  [COMMENT_LAYER][POST_CLICK] disabledFound=true elapsedMs={elapsed_ms}")
                return False, "comment_disabled"

            # 2. 에디터 준비 완료 확인 (OPEN-006)
            if CommentEditorAdapter.is_visible(page):
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.log(
                    f"  [COMMENT_LAYER][POST_CLICK] editorFound=true commentRootFound=true "
                    f"commentListFound=true loginFound=false disabledFound=false elapsedMs={elapsed_ms}"
                )
                try:
                    page.evaluate("() => { const el = document.querySelector('.u_cbox_write_box, .u_cbox_area, .u_cbox'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }")
                except Exception:
                    pass
                return True, "ready"

            # 3. 작성 상자 플레이스홀더 클릭 시도
            for f in frames:
                try:
                    write_box = f.locator(".u_cbox_write_box, .u_cbox_guide, .u_cbox_inbox").first
                    if write_box.count() > 0 and write_box.is_visible():
                        write_box.click(timeout=500)
                        break
                except Exception:
                    pass

            interruptible_wait(stop_event, 0.25)

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.log(
            f"  [COMMENT_LAYER][POST_CLICK] editorFound=false commentRootFound=false "
            f"commentListFound=false loginFound=false disabledFound=false elapsedMs={elapsed_ms}"
        )
        if not click_success:
            return False, "comment_open_click_failed"
        return False, "comment_open_clicked_editor_not_ready"

    @classmethod
    def prepare_comment_draft(
        cls,
        page: Page,
        draft_text: str,
        secret_comment: bool = False,
        stop_event: Optional[threading.Event] = None,
        post_key: str = "",
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
        cls.install_keyboard_listener(page, post_key=post_key)
        CommentEditorAdapter.focus(page)

        logger.log(f"  💬 [COMMENT] 초안 자동 입력 완료 (수정 후 Enter=등록 / Cmd+V=붙여넣기 / Esc=건너뛰기)")
        return CommentProcessResult(status=CommentSubmitState.DRAFTED, draft_text=draft_text)

    @classmethod
    def wait_for_user_action(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None,
        preset: str = "community",
        skip_event: Optional[threading.Event] = None,
        post_key: str = "",
        timeout_seconds: Optional[float] = None,
        state_mgr: Optional[object] = None,
        pause_event: Optional[threading.Event] = None,
        run_control = None,
    ) -> UserAction:
        start_time = time.monotonic()
        while True:
            # 일시정지 중 카운트다운 정지 및 재개 시 남은 시간부터 진행
            if run_control and hasattr(run_control, "checkpoint"):
                p_start = time.monotonic()
                run_control.checkpoint("during_wait_user_action")
                p_dur = time.monotonic() - p_start
                if p_dur > 0.05:
                    start_time += p_dur
            elif pause_event and pause_event.is_set():
                p_start = time.monotonic()
                while pause_event.is_set():
                    if stop_event and stop_event.is_set():
                        return UserAction.STOP
                    time.sleep(0.05)
                p_dur = time.monotonic() - p_start
                start_time += p_dur

            # 1. 종료 이벤트
            if stop_event and stop_event.is_set():
                return UserAction.STOP
            if skip_event and skip_event.is_set():
                logger.log("  ⏭️ [USER] skip_event 감지: 현재 글 작성을 건너뛰고 다음 글로 이동합니다.")
                return UserAction.SKIP

            ensure_page_alive(page)

            # 2. 브라우저 사용자 액션 및 편집 상태 먼저 평가 (우선순위 역전 및 이중 제출 방지)
            editor_context = None
            action_frame = None
            try:
                editor_context = MobileDOMResolver.get_comment_editor_context(page)
                action_frame = editor_context["frame"] if editor_context else page.main_frame
                eval_res = action_frame.evaluate("""
                    () => {
                        const act = window.__NAVER_FEED_ACTION__;
                        window.__NAVER_FEED_ACTION__ = null;
                        const dirty = (window.__NAVER_COMMENT_USER_DIRTY__ === true || 
                                       window.__NAVER_COMMENT_STATE__ === 'USER_EDITING' || 
                                       window.__NAVER_IS_COMPOSING__ === true);
                        return [act, dirty];
                    }
                """)
                action_data = eval_res[0] if isinstance(eval_res, (list, tuple)) and len(eval_res) > 0 else None
                is_dirty = eval_res[1] if isinstance(eval_res, (list, tuple)) and len(eval_res) > 1 else False

                if action_data == "SUBMIT":
                    return UserAction.SUBMIT
                elif action_data == "SUBMIT_MANUAL":
                    return UserAction.NATIVE_SUBMIT
                elif action_data in ("SKIP", "CLOSED"):
                    if skip_event:
                        skip_event.set()
                    return UserAction.SKIP

                # P0-2: 사용자가 키 입력/수정을 시작하거나 조합 중인 경우 AUTO_SUBMIT 즉시 해제 (수동 검토 모드로 전환)
                if is_dirty and timeout_seconds is not None:
                    logger.log("  ✍️ [COMMENT][AUTO_SUBMIT_DISARMED] reason=user_edit")
                    timeout_seconds = None
                    if state_mgr and hasattr(state_mgr, "update"):
                        try:
                            state_mgr.update(message="사용자 편집 중 / 자동 등록 해제")
                        except Exception:
                            pass
            except Exception as e:
                # ⑥ 상태 조회 실패는 자동 제출 중단으로 처리 (Fail-Closed)
                if timeout_seconds is not None:
                    logger.log(f"  ⚠️ [COMMENT] 브라우저 상태 조회 실패로 자동 등록을 중단합니다: {e}", "WARNING")
                    timeout_seconds = None
                    if state_mgr and hasattr(state_mgr, "update"):
                        try:
                            state_mgr.update(message="브라우저 상태 조회 실패 / 자동 등록 중단")
                        except Exception:
                            pass

            # 3. UI 스레드로부터 전달된 명령 처리 (글 식별자 유효성 검증)
            if command_bridge:
                cmd = command_bridge.pop_command()
                if cmd:
                    if cmd.post_key and post_key and cmd.post_key != post_key:
                        logger.log(f"  ⚠️ [COMMAND] 이전 글({cmd.post_key}) 명령이 현재 글({post_key})에 도착하여 무시합니다.", "WARNING")
                    elif cmd.kind in (WorkerCommandType.SKIP_POST, WorkerCommandType.GEMINI_SKIP_POST):
                        logger.log("  ⏭️ [USER] 다음 글로 바로 넘어가기 요청을 수신했습니다 (스킵).")
                        if skip_event:
                            skip_event.set()
                        return UserAction.SKIP
                    elif cmd.kind == WorkerCommandType.APPLY_CLIPBOARD_COMMENT:
                        from services.comments.community_rhythm import FinalQualityGate
                        gate_res = FinalQualityGate.validate_final_text(cmd.text, preset=preset, source="clipboard")
                        if gate_res.valid:
                            if CommentEditorAdapter.set_text(page, cmd.text):
                                logger.log("  📋 [COMMENT] 클립보드 텍스트를 댓글 에디터에 적용했습니다.")
                                if timeout_seconds is not None:
                                    logger.log("  ✍️ [COMMENT][AUTO_SUBMIT_DISARMED] reason=user_edit")
                                    timeout_seconds = None
                        else:
                            logger.log(f"  ⚠️ [COMMENT] 클립보드 텍스트가 품질 게이트를 통과하지 못해 적용을 거부했습니다: [{gate_res.code}] {gate_res.reason} (매칭: {gate_res.matched})", "WARNING")

            # 4. 마지막으로 timeout 검사 (모든 브라우저 액션 drain 후 단일 락 획득)
            if timeout_seconds is not None and timeout_seconds >= 0:
                if (time.monotonic() - start_time) >= timeout_seconds:
                    # Timeout 만료 순간 마지막 1회 브라우저 액션 최종 drain
                    try:
                        if action_frame is None:
                            editor_context = MobileDOMResolver.get_comment_editor_context(page)
                            action_frame = editor_context["frame"] if editor_context else page.main_frame
                        final_act = action_frame.evaluate("""
                            () => {
                                if (window.__NAVER_COMMENT_USER_DIRTY__ || window.__NAVER_IS_COMPOSING__ || window.__NAVER_COMMENT_STATE__ === 'USER_EDITING') {
                                    return '__USER_EDITING__';
                                }
                                if (window.tryAcquireSubmitLock && !window.tryAcquireSubmitLock('auto_timer')) {
                                    return '__LOCK_FAILED__';
                                }
                                const act = window.__NAVER_FEED_ACTION__;
                                window.__NAVER_FEED_ACTION__ = null;
                                return act;
                            }
                        """)
                        if final_act in ("__USER_EDITING__", "__LOCK_FAILED__"):
                            logger.log("  ⚠️ [COMMENT] 자동 등록 직전 사용자 편집 또는 제출 잠금 실패로 자동 제출을 취소합니다.")
                            timeout_seconds = None
                            if state_mgr and hasattr(state_mgr, "update"):
                                try:
                                    state_mgr.update(message="사용자 편집 감지 / 자동 등록 취소")
                                except Exception:
                                    pass
                            continue

                        if final_act == "SUBMIT":
                            return UserAction.SUBMIT
                        elif final_act == "SUBMIT_MANUAL":
                            return UserAction.NATIVE_SUBMIT
                        elif final_act in ("SKIP", "CLOSED"):
                            if skip_event:
                                skip_event.set()
                            return UserAction.SKIP
                    except Exception as e:
                        logger.log(f"  ⚠️ [COMMENT] 최종 액션 확인 조회 실패로 자동 제출을 중단합니다: {e}", "WARNING")
                        timeout_seconds = None
                        if state_mgr and hasattr(state_mgr, "update"):
                            try:
                                state_mgr.update(message="상태 확인 실패 / 자동 등록 중단")
                            except Exception:
                                pass
                        continue

                    logger.log(f"  ⏱️ [COMMENT] 자동 등록 대기 시간({timeout_seconds:.1f}초) 만료 - 댓글을 자동 등록합니다.")
                    return UserAction.AUTO_SUBMIT

            interruptible_wait(stop_event, 0.15, skip_event=skip_event)

    @classmethod
    def read_final_text(cls, page: Page) -> str:
        """마우스 클릭 시 보존된 텍스트 또는 현재 에디터 텍스트 추출.
        반환 텍스트는 normalize_naver_comment_text() 정규화 기준을 적용한다.
        CommentEditorAdapter.get_text()도 같은 기준을 사용하므로 두 경로의 계약이 동일하다.
        """
        try:
            context = MobileDOMResolver.get_comment_editor_context(page)
            frame = context["frame"] if context else page.main_frame
            saved_text = frame.evaluate("() => window.__NAVER_COMMENT_FINAL_TEXT__ || ''")
            if saved_text and saved_text.strip():
                return normalize_naver_comment_text(saved_text)
        except Exception:
            pass
        return CommentEditorAdapter.get_text(page)

    @classmethod
    def release_submit_lock(cls, page: Page, source: str = "") -> bool:
        """제출 전 사전 검증 실패 시 제출 락을 롤백하여 재시도를 허용"""
        try:
            context = MobileDOMResolver.get_comment_editor_context(page)
            frame = context["frame"] if context else page.main_frame
            return bool(frame.evaluate("(s) => window.releaseSubmitLock ? window.releaseSubmitLock(s) : false", source))
        except Exception:
            return False

    @staticmethod
    def _was_submit_permit_consumed(frame, permit_id: str) -> bool:
        """
        DOM에 등록 버튼 클릭 이벤트가 전달되어 __NAVER_FEED_CLICK_HANDLER__에 의해
        단일 제출 권한(permit)이 실제로 소비되었는지 확인.
        """
        if not frame or not permit_id:
            return False
        try:
            res = frame.evaluate(
                "(p) => window.__NAVER_LAST_CONSUMED_PERMIT__ === p",
                permit_id,
            )
            return res is True or res == "true"
        except Exception:
            return False

    @staticmethod
    def _get_editor_state(frame) -> dict:
        """
        현재 댓글 에디터의 가시성, 입력 텍스트, 제출 락 상태 조회.

        반환값:
          rawText: DOM innerText 원본
          text:    normalize_naver_comment_text(rawText) — 운영 비교에 사용
          visible: 에디터 가시성
          lockHeld: 제출 락 보유 여부
        """
        if not frame:
            return {"visible": False, "rawText": "", "text": "", "lockHeld": False}
        try:
            res = frame.evaluate("""() => {
                const el = document.querySelector(
                    '#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text'
                );
                const rect = el ? el.getBoundingClientRect() : null;
                const isVis = !!(el && rect && rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none');
                const txt = el ? (el.innerText || el.value || '') : '';
                const lock = !!(window.__NAVER_SUBMIT_LOCK_ACQUIRED__ || window.__NAVER_COMMENT_STATE__ === 'SUBMITTING');
                return { visible: isVis, rawText: txt, lockHeld: lock, dirty: window.__NAVER_COMMENT_USER_DIRTY__ === true };
            }""")
            if isinstance(res, dict):
                vis = res.get("visible")
                raw_val = res.get("rawText")
                if raw_val is None:
                    raw_val = res.get("text") if res.get("text") is not None else res.get("currentText")
                raw_txt = str(raw_val or "")
                norm_txt = normalize_naver_comment_text(raw_txt)
                if vis is None:
                    vis = bool(norm_txt)
                lock = res.get("lockHeld")
                if lock is None:
                    lock = True
                return {
                    "visible": bool(vis),
                    "rawText": raw_txt,
                    "text": norm_txt,
                    "lockHeld": bool(lock),
                }
        except Exception:
            pass
        return {"visible": False, "rawText": "", "text": "", "lockHeld": False}

    @classmethod
    def submit_and_verify(
        cls,
        page: Page,
        final_text: str,
        stop_event: Optional[threading.Event] = None,
        preset: str = "community",
        click: Optional[bool] = None,
        is_auto_submit: Optional[bool] = None,
        origin: Optional[SubmitOrigin] = None,
        post_key: str = "",
        style_profile: Optional[Any] = None,
        style_policy: Optional[Any] = None,
        excerpt: Optional[str] = None,
    ) -> CommentSubmitOutcome:
        """
        댓글 등록 버튼 클릭 및 Fail-closed 검증 (에디터 클리어 및 서버 목록 내 댓글 등장 확인)
        - SubmitOrigin: USER_ENTER (수동 엔터), NATIVE_CLICK (네이버 버튼 클릭), AUTO_TIMER (자동 등록)
        - USER_ENTER: dirty 허용, exact readback 확인, 품질 게이트 후 Python 1회 클릭, 서버 검증
        - NATIVE_CLICK: 네이버 기본 클릭 완료 상태, 추가 클릭 금지, 서버 검증만 수행
        - AUTO_TIMER: dirty/isComposing 감지 시 REVIEW_REQUIRED 반환하여 같은 글 WAITING_USER 유지
        """
        ensure_page_alive(page)

        if origin is None:
            if is_auto_submit is True:
                origin = SubmitOrigin.AUTO_TIMER
            elif click is False:
                origin = SubmitOrigin.NATIVE_CLICK
            else:
                origin = SubmitOrigin.USER_ENTER

        if origin != SubmitOrigin.NATIVE_CLICK:
            from services.comments.community_rhythm import FinalQualityGate
            sub_source = "user_edit" if origin == SubmitOrigin.USER_ENTER else "user_submission"
            gate_res = FinalQualityGate.validate_final_text(
                final_text,
                preset=preset,
                source=sub_source,
                style_profile=style_profile,
                style_policy=style_policy,
                excerpt=excerpt,
            )
            if not gate_res.valid:
                logger.log(f"  ❌ [COMMENT] 등록 직전 품질 게이트 실패로 제출을 중단합니다: [{gate_res.code}] {gate_res.reason} (매칭: {gate_res.matched})", "ERROR")
                retryable = (origin in (SubmitOrigin.USER_ENTER, SubmitOrigin.AUTO_TIMER))
                state = CommentSubmitState.PRECLICK_BLOCKED if retryable else CommentSubmitState.FAILED
                return CommentSubmitOutcome(state=state, reason=gate_res.code, click_dispatched=False, retryable_same_post=retryable)
        else:
            logger.log(
                "  ℹ️ [COMMENT][NATIVE_SUBMIT_VERIFY_ONLY] native click already occurred; final quality gate is not used as a blocker"
            )

        editor_context = MobileDOMResolver.get_comment_editor_context(page)
        comment_frame = editor_context.get("frame") if editor_context else page.main_frame

        click_to_dispatch = (origin in (SubmitOrigin.USER_ENTER, SubmitOrigin.AUTO_TIMER))
        last_click_error = ""
        click_event_confirmed = False
        click_dispatched = False

        if click_to_dispatch:
            submit_context = MobileDOMResolver.get_comment_submit_context(page, editor_context["frame"] if editor_context else None)
            if not submit_context:
                logger.log("  ❌ [COMMENT] 등록 버튼을 찾지 못했습니다.", "ERROR")
                return CommentSubmitOutcome(state=CommentSubmitState.FAILED, reason="submit_button_not_found", click_dispatched=False, retryable_same_post=False)
            btn = submit_context["button"]
            comment_frame = submit_context.get("frame") or comment_frame

            # 클릭 직전 에디터 최종 상태 및 본문 변조 재확인
            try:
                pre_check = comment_frame.evaluate("""() => ({
                    dirty: window.__NAVER_COMMENT_USER_DIRTY__ === true,
                    isComposing: window.__NAVER_IS_COMPOSING__ === true,
                    text: (() => {
                        const el = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                        return el ? (el.innerText || el.value || '').trim() : '';
                    })()
                })""")
                if isinstance(pre_check, dict):
                    if origin == SubmitOrigin.AUTO_TIMER:
                        if pre_check.get("dirty") or pre_check.get("isComposing"):
                            logger.log("  ⚠️ [COMMENT][AUTO_SUBMIT_DISARMED] reason=user_edit")
                            return CommentSubmitOutcome(state=CommentSubmitState.REVIEW_REQUIRED, reason="user_edit", click_dispatched=False, retryable_same_post=True)
                        cur_text = pre_check.get("text", "")
                        cur_norm = normalize_naver_comment_text(cur_text)
                        expected_norm = normalize_naver_comment_text(final_text)
                        if cur_norm and cur_norm != expected_norm:
                            logger.log(
                                f"  ⚠️ [COMMENT][AUTO_SUBMIT_DISARMED] reason=text_mutation "
                                f"(normActual={len(cur_norm)} normExpected={len(expected_norm)})"
                            )
                            return CommentSubmitOutcome(state=CommentSubmitState.REVIEW_REQUIRED, reason="text_mutation", click_dispatched=False, retryable_same_post=True)
                    elif origin == SubmitOrigin.USER_ENTER:
                        if pre_check.get("isComposing"):
                            logger.log("  ⚠️ [COMMENT][IME_COMPOSING] Enter는 한글 조합 확정으로 처리 / 등록 대기 유지", "INFO")
                            return CommentSubmitOutcome(state=CommentSubmitState.PRECLICK_BLOCKED, reason="ime_composing", click_dispatched=False, retryable_same_post=True)
                        cur_text = pre_check.get("text", "")
                        cur_norm = normalize_naver_comment_text(cur_text)
                        expected_norm = normalize_naver_comment_text(final_text)
                        if not cur_norm and not expected_norm:
                            logger.log("  ❌ [COMMENT][MANUAL_SUBMIT_PRECHECK_FAILED] reason=empty_text retryable=true", "ERROR")
                            return CommentSubmitOutcome(state=CommentSubmitState.PRECLICK_BLOCKED, reason="empty_text", click_dispatched=False, retryable_same_post=True)
                        if cur_norm and cur_norm != expected_norm:
                            gate_res_cur = FinalQualityGate.validate_final_text(
                                cur_text,
                                preset=preset,
                                source="user_edit",
                                style_profile=style_profile,
                                style_policy=style_policy,
                                excerpt=excerpt,
                            )
                            if not gate_res_cur.valid:
                                logger.log(f"  ❌ [COMMENT][MANUAL_SUBMIT_PRECHECK_FAILED] reason={gate_res_cur.code} retryable=true", "WARNING")
                                return CommentSubmitOutcome(state=CommentSubmitState.PRECLICK_BLOCKED, reason=gate_res_cur.code, click_dispatched=False, retryable_same_post=True)
                            final_text = cur_text
                        is_dirty = pre_check.get("dirty", False)
                        logger.log(f"  📝 [COMMENT][MANUAL_SUBMIT_READY] edited={is_dirty} chars={len(final_text)}")
            except Exception as chk_err:
                logger.log(f"  ⚠️ [COMMENT] 클릭 직전 에디터 검증 예외: {chk_err}", "WARNING")

            baseline = ServerCommentDuplicateGuard.capture_submission_baseline(comment_frame)
            click_returned = False

            for attempt in (1, 2):
                if attempt == 2:
                    logger.log(f"  🔄 [COMMENT][CLICK_RETRY_RESOLVE] attempt=2 post={post_key or ''}")
                    submit_context = MobileDOMResolver.get_comment_submit_context(
                        page, editor_context["frame"] if editor_context else None
                    )
                    if not submit_context:
                        logger.log("  ❌ [COMMENT] 재시도용 등록 버튼을 찾지 못했습니다.", "ERROR")
                        last_click_error = "retry_submit_button_not_found"
                        break
                    btn = submit_context["button"]
                    comment_frame = submit_context.get("frame") or comment_frame

                logger.log(
                    f"  🎯 [COMMENT][SUBMIT_CANDIDATE] attempt={attempt} "
                    f"frame={submit_context.get('frame_name', '')} "
                    f"selector={submit_context.get('selector', '')} "
                    f"visible={submit_context.get('visible', False)} "
                    f"enabled={submit_context.get('enabled', True)} "
                    f"score={submit_context.get('score', 0)}"
                )

                # 등록 버튼 활성화 검사
                try:
                    if hasattr(btn, "is_disabled") and btn.is_disabled():
                        interruptible_wait(stop_event, 0.3)
                    if hasattr(btn, "is_disabled") and btn.is_disabled():
                        logger.log(f"  ❌ [COMMENT] 등록 버튼이 비활성 상태입니다 (attempt={attempt}).", "ERROR")
                        last_click_error = "button_disabled"
                        if attempt == 1:
                            editor_info = cls._get_editor_state(comment_frame)
                            if (editor_info.get("visible") and
                                normalize_naver_comment_text(editor_info.get("text", "")) == normalize_naver_comment_text(final_text) and
                                editor_info.get("lockHeld")):
                                interruptible_wait(stop_event, 0.4)
                                continue
                        break
                except Exception:
                    pass

                permit_id = f"permit_{uuid.uuid4().hex}"
                try:
                    comment_frame.evaluate("""(p) => {
                        window.__NAVER_SUBMIT_PERMIT__ = p;
                        window.__NAVER_LAST_CONSUMED_PERMIT__ = null;
                    }""", permit_id)
                except Exception:
                    pass

                logger.log(f"  👉 [COMMENT][CLICK_ATTEMPT] attempt={attempt} permitId={permit_id}")

                try:
                    if hasattr(btn, "scroll_into_view_if_needed"):
                        try:
                            btn.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                    btn.click(timeout=2500)
                    click_returned = True
                    logger.log(f"  🚀 [COMMENT][CLICK_RETURNED] attempt={attempt}")
                except Exception as exc:
                    last_click_error = str(exc)
                    logger.log(f"  ⚠️ [COMMENT][CLICK_EXCEPTION] attempt={attempt} error={exc}")
                finally:
                    try:
                        comment_frame.evaluate("() => { delete window.__NAVER_SUBMIT_PERMIT__; }")
                    except Exception:
                        pass

                # Permit 소비 확인
                permit_consumed = cls._was_submit_permit_consumed(comment_frame, permit_id)
                if permit_consumed:
                    click_event_confirmed = True
                    logger.log("  🎟️ [COMMENT][CLICK_EVENT_CONFIRMED] permitConsumed=true")
                else:
                    logger.log("  ⚠️ [COMMENT][CLICK_EVENT_NOT_CONFIRMED] permitConsumed=false")

                # Step A: click이 정상 반환되었거나 permit이 소비된 경우 -> 버튼 도달 확정, 2회차 클릭 절대 금지
                if click_returned or permit_consumed:
                    break

                # click 예외 발생 + permit 미소비 시 상태 확인 대기 (300~500ms)
                interruptible_wait(stop_event, 0.4)

                # 서버 사이드 확인 (이미 등록 완료 여부)
                presence = ServerCommentDuplicateGuard.scan_page_for_my_comment(
                    comment_frame,
                    stop_event=stop_event,
                    baseline=baseline,
                    expected_text=final_text,
                )
                if presence.state == CommentPresenceState.PRESENT:
                    logger.log("  ✅ [COMMENT][SERVER_VERIFIED] 본인 댓글이 서버 목록에 확인되었습니다")
                    return CommentSubmitOutcome(
                        state=CommentSubmitState.SUBMITTED,
                        reason="server_verified",
                        click_dispatched=True,
                        retryable_same_post=False,
                    )

                # Step B: 에디터 상태 확인 (에디터 닫힘 또는 텍스트 비워짐 감지 시 2회차 클릭 금지)
                editor_info = cls._get_editor_state(comment_frame)
                is_editor_vis = editor_info.get("visible", False)
                cur_editor_txt = editor_info.get("text", "")

                if not is_editor_vis or not cur_editor_txt:
                    logger.log(
                        f"  ⚠️ [COMMENT][UI_STATE_CHANGED] editorVisible={is_editor_vis} textLen={len(cur_editor_txt)} "
                        "- submission in progress, strictly forbidding second click"
                    )
                    break

                # Step C: 확실한 사전 실패 조건 만족 시 정확히 1회 재시도 (attempt 1 -> 2)
                if attempt == 1:
                    lock_held = editor_info.get("lockHeld", False)
                    is_exact_match = (
                        normalize_naver_comment_text(cur_editor_txt)
                        == normalize_naver_comment_text(final_text)
                    )
                    if (not permit_consumed and
                        presence.state == CommentPresenceState.ABSENT and
                        is_editor_vis and
                        is_exact_match and
                        lock_held):
                        logger.log(
                            f"  🔍 [COMMENT][PRE_RETRY_SERVER_CHECK] presence={presence.state.value} "
                            f"editorVisible={is_editor_vis} textMatch=true lockHeld={lock_held}"
                        )
                        logger.log("  🔄 [COMMENT][CLICK_RETRY_ATTEMPT] attempt=2")
                        continue

                # 그 외의 경우 추가 클릭 방지
                break

            # 클릭 루프 종료 후 실제 디스패치 여부 판정
            editor_info_after = cls._get_editor_state(comment_frame)
            editor_cleared_or_hidden = (not editor_info_after.get("visible", False)) or (not editor_info_after.get("text", ""))
            click_dispatched = bool(click_returned or click_event_confirmed or editor_cleared_or_hidden)

            if not click_dispatched:
                # 확실한 사전 실패: 이벤트가 DOM에 도달하지 않음 -> 동일 글 재시도 허용
                logger.log(
                    f"  🛑 [COMMENT][PRECLICK_BLOCKED] reason={last_click_error or 'preclick_blocked'} "
                    f"click_dispatched=False retryable_same_post=True"
                )
                return CommentSubmitOutcome(
                    state=CommentSubmitState.PRECLICK_BLOCKED,
                    reason=last_click_error or "preclick_blocked",
                    click_dispatched=False,
                    retryable_same_post=True,
                )
        else:
            logger.log("  ℹ️ [COMMENT][SUBMIT_NATIVE_CLICK] 네이버 기본 등록 동작을 검증합니다")
            baseline = ServerCommentDuplicateGuard.capture_submission_baseline(comment_frame)
            click_dispatched = False

        # 등록 후 서버 목록 확인
        try:
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
                    return CommentSubmitOutcome(
                        state=CommentSubmitState.SUBMITTED,
                        reason="server_verified",
                        click_dispatched=click_to_dispatch,
                        retryable_same_post=False,
                    )
                if presence.state == CommentPresenceState.UNKNOWN:
                    unknown_seen = True
                    logger.log(
                        "  ⚠️ [COMMENT] 서버 댓글 목록 확인이 불완전합니다. 재확인합니다",
                        "WARNING",
                    )

            logger.log(
                f"  ⚠️ [COMMENT][SUBMISSION_UNKNOWN_DIAG] reason=server_unconfirmed unknownSeen={unknown_seen} "
                f"lastClickError={last_click_error} permitConsumed={click_event_confirmed}",
                "WARNING",
            )
            return CommentSubmitOutcome(
                state=CommentSubmitState.SUBMISSION_UNKNOWN,
                reason="server_unconfirmed",
                click_dispatched=click_to_dispatch,
                retryable_same_post=False,
            )
        except Exception as e:
            logger.log(
                f"  ⚠️ [COMMENT][SUBMISSION_UNKNOWN_DIAG] 등록 검증 중 예외 발생: {e} "
                f"(클릭 이후이므로 SUBMISSION_UNKNOWN 처리)",
                "WARNING",
            )
            return CommentSubmitOutcome(
                state=CommentSubmitState.SUBMISSION_UNKNOWN,
                reason=str(e),
                click_dispatched=click_to_dispatch,
                retryable_same_post=False,
            )
