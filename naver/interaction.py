import time
import threading
from typing import Optional, Tuple
from playwright.sync_api import Page, Locator
from app.models import LikeState, LikeProcessResult, CommentSubmitState, CommentProcessResult, UserAction, FailureReason, WorkerCommandType
from naver.resolver import MobileDOMResolver
from services.clipboard_bridge import ClipboardCommandBridge
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
                    // Cmd+V / Ctrl+V 붙여넣기는 정상 통과
                    if ((e.metaKey || e.ctrlKey) && (e.key === 'v' || e.key === 'V')) {
                        return;
                    }

                    const editor = e.target.closest ? (
                        e.target.closest('#naverComment__write_textarea') || 
                        e.target.closest('.u_cbox_text') ||
                        e.target.closest('[contenteditable="true"]')
                    ) : null;

                    // Shift + Enter = 줄바꿈 허용
                    if (e.key === 'Enter' && e.shiftKey) {
                        return;
                    }

                    // Enter = 댓글 등록 승인
                    if (e.key === 'Enter' && !e.shiftKey) {
                        if (editor) {
                            e.preventDefault();
                            e.stopPropagation();
                            window.__NAVER_FEED_ACTION__ = 'SUBMIT';
                            return;
                        }
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

                // 2. 마우스로 '등록' 버튼을 클릭한 경우도 감지
                const submitBtns = document.querySelectorAll('.u_cbox_btn_upload, button[data-action="write#request"], button.__uis_naverComment_writeButton');
                submitBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
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
        """댓글 에디터 내용을 새로운 텍스트(클립보드/AI 결과)로 교체 및 이벤트 디스패치"""
        if not text or not text.strip():
            return False

        clean_t = text.strip()
        if clean_t.startswith("```"):
            clean_t = clean_t.strip("`")
            if clean_t.startswith("text") or clean_t.startswith("markdown"):
                clean_t = clean_t.split("\n", 1)[-1]
        clean_t = clean_t.strip()

        try:
            # 1. JavaScript evaluate로 ContentEditable innerText 주입 및 input 이벤트 발송
            success = page.evaluate("""
                (t) => {
                    const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                    if (!editor) return false;

                    editor.focus();
                    if (editor.tagName.toLowerCase() === 'textarea') {
                        editor.value = t;
                    } else {
                        editor.innerText = t;
                    }

                    editor.dispatchEvent(new Event('input', { bubbles: true }));
                    editor.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            """, clean_t)

            if success:
                return True

            # 2. Fallback: locator fill
            editor = MobileDOMResolver.get_comment_editor(page)
            if editor.count() > 0:
                editor.click(timeout=1000)
                editor.fill(clean_t)
                editor.focus()
                return True

            return False
        except Exception:
            return False

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
        4. Document 키보드 및 클릭 리스너 설치 및 포커스
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
            cls.replace_editor_text(page, draft_text)

            # 비밀댓글 설정
            if secret_comment:
                secret_chk = MobileDOMResolver.get_secret_checkbox(page)
                if secret_chk.count() > 0:
                    try:
                        secret_chk.click(timeout=1000)
                        logger.log("  🔒 [COMMENT] 비밀댓글 설정 완료")
                    except Exception:
                        pass

            # 리스너 설치 및 포커스
            cls.install_keyboard_listener(page)
            editor.focus()

            logger.log(f"  💬 [COMMENT] 초안 자동 입력 완료 (수정 후 Enter=등록 / Cmd+V=붙여넣기 / Esc=건너뛰기)")
            return CommentProcessResult(status=CommentSubmitState.DRAFTED, draft_text=draft_text)
        except Exception as e:
            logger.log(f"  ❌ [COMMENT] 초안 입력 실패: {e}", "ERROR")
            return CommentProcessResult(status=CommentSubmitState.FAILED, error=str(e))

    @classmethod
    def wait_for_user_action(
        cls,
        page: Page,
        stop_event: Optional[threading.Event] = None,
        command_bridge: Optional[ClipboardCommandBridge] = None
    ) -> UserAction:
        """
        사용자의 키보드 입력(Enter=SUBMIT, Esc=SKIP), 마우스 등록/닫기 클릭,
        에디터 상태 변화 및 UI 스레드 명령(APPLY_CLIPBOARD_COMMENT)을 다각도로 감지
        """
        while True:
            if stop_event and stop_event.is_set():
                return UserAction.STOP

            # 1. UI 스레드로부터 전달된 클립보드 적용 명령 처리
            if command_bridge:
                cmd = command_bridge.pop_command()
                if cmd and cmd.kind == WorkerCommandType.APPLY_CLIPBOARD_COMMENT:
                    if cls.replace_editor_text(page, cmd.text):
                        logger.log("  📋 [COMMENT] 클립보드 텍스트를 댓글 에디터에 적용했습니다.")

            # 2. 브라우저 이벤트 및 상태 확인
            try:
                action_str = page.evaluate("() => window.__NAVER_FEED_ACTION__")
                manual_submitted = page.evaluate("() => window.__NAVER_COMMENT_SUBMITTED_FLAG__")

                if manual_submitted or action_str == "SUBMIT_MANUAL":
                    # 마우스로 등록 버튼 클릭 감지
                    logger.log("  🖱️ [COMMENT] 마우스로 등록 버튼 클릭이 감지되었습니다.")
                    return UserAction.SUBMIT

                if action_str == "SUBMIT":
                    final_t = cls.read_final_text(page)
                    if not final_t:
                        logger.log("  ⚠️ [COMMENT] 댓글 내용이 비어 있어 등록하지 않았습니다. 내용을 입력한 뒤 Enter를 눌러주세요.", "WARNING")
                        page.evaluate("() => { window.__NAVER_FEED_ACTION__ = null; }")
                    else:
                        return UserAction.SUBMIT

                elif action_str == "SKIP":
                    return UserAction.SKIP

                elif action_str == "CLOSED":
                    logger.log("  🚪 [COMMENT] 댓글창이 닫혔습니다. 다음 글로 이동합니다.")
                    return UserAction.SKIP

                # 3. 에디터가 화면에서 닫히거나 사라졌는지 점검 (멈춤 방지)
                editor_visible = page.evaluate("""
                    () => {
                        const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"]');
                        if (!editor) return false;
                        const rect = editor.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    }
                """)
                if not editor_visible:
                    # 에디터가 안 보임 (댓글창 닫힘 혹은 페이지 전환)
                    time.sleep(0.5)
                    # 다시 확인
                    re_check = page.evaluate("""
                        () => {
                            const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"]');
                            return !!editor;
                        }
                    """)
                    if not re_check:
                        logger.log("  ℹ️ [COMMENT] 댓글창이 닫힌 상태를 감지하여 다음 글로 진행합니다.")
                        return UserAction.SKIP

            except Exception:
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
        # 만약 사용자가 이미 마우스로 등록 버튼을 눌렀다면 추가 클릭 없이 검증만 진행
        submit_btn = MobileDOMResolver.get_comment_submit_button(page)

        try:
            # 에디터에 아직 텍스트가 남아있고 등록 버튼이 활성화되어 있다면 클릭
            editor_txt = CommentInteractionService.read_final_text(page)
            if editor_txt and submit_btn.count() > 0:
                logger.log("  🚀 [COMMENT] 댓글 등록 버튼을 클릭합니다...")
                submit_btn.scroll_into_view_if_needed(timeout=1500)
                submit_btn.click(timeout=1500)

            # 성공 신호 검증 (최대 3.5초간 폴링)
            verified = False
            start_t = time.time()
            while time.time() - start_t < 3.5:
                if stop_event and stop_event.is_set():
                    break

                try:
                    editor = MobileDOMResolver.get_comment_editor(page)
                    txt = editor.inner_text().strip() if editor.count() > 0 else ""
                    if not txt:
                        verified = True
                        break

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
                logger.log("  ℹ️ [COMMENT] 댓글 등록 완료 신호 수신됨")
                return CommentSubmitState.SUBMITTED

        except Exception as e:
            logger.log(f"  ❌ [COMMENT] 댓글 등록 중 오류: {e}", "ERROR")
            return CommentSubmitState.FAILED
