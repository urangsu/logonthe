from typing import Optional
from playwright.sync_api import Page, Locator
from naver.resolver import MobileDOMResolver
from services.draft import normalize_naver_comment_text
from src.logger import logger


class CommentEditorAdapter:
    """
    네이버 블로그 댓글 에디터(contenteditable div / textarea) 통합 어댑터

    Path A: Focus -> Fill (마우스 hit-test 회피)
    Path B: Overlay Placeholder Click -> Focus -> Fill
            ※ placeholder가 실제로 visible한 경우에만 진입 (불필요한 1초 소비 방지)
    Path C: execCommand insertText + beforeinput/input 이벤트 디스패치 fallback

    readback: normalize_naver_comment_text()로 DOM 직렬화 차이를 정규화한 뒤 비교.
              raw 문자수 불일치라도 정규화 후 동일하면 통과한다.
    """

    # ------------------------------------------------------------------
    # Naver editor DOM-aware text reader
    # ------------------------------------------------------------------

    @classmethod
    def _read_editor_surface(cls, editor: Locator, is_textarea: bool) -> str:
        """
        contenteditable 에디터에서 텍스트를 읽는다.
        빈 DOM 문단(<p><br></p>)이 만드는 연속 개행은 normalize_naver_comment_text에서 처리.
        textarea는 input_value()로 직접 읽는다.
        """
        if is_textarea:
            try:
                return editor.input_value()
            except Exception:
                return ""
        # contenteditable: DOM block 순회로 읽기
        try:
            dom_text = editor.evaluate("""
                (el) => {
                    const blocks = [];
                    const children = el.childNodes;
                    for (let node of children) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            if (node.textContent) blocks.push(node.textContent);
                        } else if (node.nodeName === 'P' || node.nodeName === 'DIV') {
                            const inner = node.innerText !== undefined ? node.innerText : node.textContent;
                            // 빈 문단(<p><br></p> 등)은 수집하지 않음
                            if (inner && inner.trim() !== '') {
                                blocks.push(inner.replace(/\\n$/, ''));
                            }
                        } else if (node.nodeName === 'BR') {
                            blocks.push('');
                        } else {
                            const t = node.innerText !== undefined ? node.innerText : node.textContent;
                            if (t) blocks.push(t);
                        }
                    }
                    return blocks.join('\\n');
                }
            """)
            if dom_text and dom_text.strip():
                return dom_text
        except Exception:
            pass
        # DOM 순회 실패 시 innerText fallback
        try:
            return editor.inner_text()
        except Exception:
            return ""

    @classmethod
    def _readback_diag(
        cls, expected: str, actual_raw: str, norm_expected: str, norm_actual: str, matched: bool
    ) -> None:
        """계획서 §12: 진단 로그 — 개인정보(댓글 전문) 미포함."""
        import re as _re
        def _nl(s):
            return s.count("\n")
        def _zw(s):
            return len(_re.findall(r"[\u200B-\u200D\u2060\uFEFF\u200E\u200F]", s))
        def _nbsp(s):
            return s.count("\u00A0") + s.count("\u2007") + s.count("\u202F")
        first_mismatch = -1
        if not matched:
            min_len = min(len(norm_expected), len(norm_actual))
            for i in range(min_len):
                if norm_expected[i] != norm_actual[i]:
                    first_mismatch = i
                    break
            else:
                first_mismatch = min_len
        logger.log(
            f"[NAVER][EDITOR_READBACK_DIAG] "
            f"expected={len(expected)} actual={len(actual_raw)} "
            f"normExpected={len(norm_expected)} normActual={len(norm_actual)} "
            f"firstMismatch={first_mismatch} "
            f"newlineExpected={_nl(norm_expected)} newlineActual={_nl(norm_actual)} "
            f"zwActual={_zw(actual_raw)} nbspActual={_nbsp(actual_raw)} "
            f"matched={'true' if matched else 'false'}"
        )

    # ------------------------------------------------------------------
    # Public interface (기존 호출자와 동일)
    # ------------------------------------------------------------------

    @classmethod
    def get_editor(cls, page: Page) -> Optional[Locator]:
        context = MobileDOMResolver.get_comment_editor_context(page)
        return context["editor"] if context else None

    @classmethod
    def is_visible(cls, page: Page) -> bool:
        editor = cls.get_editor(page)
        if not editor or editor.count() == 0:
            return False
        try:
            return editor.is_visible()
        except Exception:
            return False

    @classmethod
    def focus(cls, page: Page) -> bool:
        try:
            context = MobileDOMResolver.get_comment_editor_context(page)
            if not context:
                return False
            context["editor"].focus()
            try:
                context["editor"].scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass
            logger.log(
                f"[NAVER][COMMENT_EDITOR_FOUND] frame={context['frame_name'] or 'main'} "
                f"selector={context['selector']} frameUrl={context['frame_url']}"
            )
            logger.log("[NAVER][EDITOR_FOCUS_OK]")
            return True
        except Exception:
            return False

    @classmethod
    def get_text(cls, page: Page) -> str:
        """정규화된 에디터 텍스트 반환 (자동등록 pre-check 등에서 사용)."""
        try:
            context = MobileDOMResolver.get_comment_editor_context(page)
            if not context:
                return ""
            editor = context["editor"]
            try:
                tag_name = editor.evaluate("e => e.tagName.toLowerCase()")
                is_textarea = (tag_name == "textarea")
            except Exception:
                is_textarea = False
            raw = cls._read_editor_surface(editor, is_textarea)
            return normalize_naver_comment_text(raw)
        except Exception:
            return ""

    @classmethod
    def _verify_and_confirm(
        cls, editor: Locator, clean_t: str, is_textarea: bool, frame, page: Page,
        stop_flag=None,
    ) -> bool:
        """
        DOM-aware readback + normalize 후 비교.
        raw ±1 문자 차이라도 정규화 후 동일하면 통과.
        진단 로그(EDITOR_READBACK_DIAG)는 항상 기록.

        readback 안정화 polling:
          - 100ms 간격으로 동일 결과 2회 연속 확인 (stale locator 대비 매번 재연결)
          - 쳙 대기 상한 1.5초

        submit button polling:
          - readback OK 후 100ms 간격 최대 400ms
          - 클릭 안들개 정상 활성화 확인
        """
        import time as _time

        def _wait_step(seconds: float):
            if stop_flag:
                try:
                    from browser.session import interruptible_wait
                    interruptible_wait(stop_flag, seconds)
                    return
                except Exception:
                    pass
            _time.sleep(seconds)

        norm_expected = normalize_naver_comment_text(clean_t)

        # --- readback stability polling ---
        POLL_INTERVAL = 0.10   # 100 ms
        POLL_MAX = 1.5
        STABLE_REQUIRED = 2

        stable_count = 0
        norm_actual = ""
        raw_read = ""
        poll_start = _time.monotonic()

        while True:
            if stop_flag and getattr(stop_flag, "is_set", lambda: False)():
                break

            # Re-resolve editor locator 매번 (콘텐츠에디터블 DOM 교체 대비)
            try:
                fresh_ctx = MobileDOMResolver.get_comment_editor_context(page)
                if fresh_ctx and fresh_ctx.get("editor"):
                    editor = fresh_ctx["editor"]
                    frame = fresh_ctx["frame"]
                    try:
                        tag_name = editor.evaluate("e => e.tagName.toLowerCase()")
                        is_textarea = (tag_name == "textarea")
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                raw_read = cls._read_editor_surface(editor, is_textarea)
            except Exception as e:
                logger.log(f"⚠️ [NAVER][EDITOR_READBACK_ERROR] {e}", "WARNING")
                raw_read = ""

            norm_actual = normalize_naver_comment_text(raw_read)
            if norm_actual == norm_expected:
                stable_count += 1
            else:
                stable_count = 0

            if stable_count >= STABLE_REQUIRED:
                break

            elapsed = _time.monotonic() - poll_start
            if elapsed >= POLL_MAX:
                break

            _wait_step(POLL_INTERVAL)

        matched = (norm_actual == norm_expected)
        cls._readback_diag(clean_t, raw_read, norm_expected, norm_actual, matched)

        if not matched:
            logger.log(
                f"[NAVER][EDITOR_READBACK_MISMATCH] "
                f"expectedChars={len(clean_t)} actualChars={len(raw_read)} "
                f"normExpectedChars={len(norm_expected)} normActualChars={len(norm_actual)}",
                "WARNING",
            )
            return False

        logger.log(f"[NAVER][EDITOR_READBACK_OK] chars={len(raw_read)} normChars={len(norm_actual)} stableRounds={stable_count}")

        # --- submit button enabled polling ---
        BTN_POLL_INTERVAL = 0.10
        BTN_POLL_MAX = 0.40
        btn_start = _time.monotonic()
        while True:
            if stop_flag and getattr(stop_flag, "is_set", lambda: False)():
                break

            submit_context = MobileDOMResolver.get_comment_submit_context(page, frame)
            if not submit_context:
                logger.log("[NAVER][COMMENT_SUBMIT_NOT_FOUND]", "ERROR")
                logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=internal_state", "ERROR")
                return False
            try:
                disabled = submit_context["button"].is_disabled()
            except Exception as e:
                logger.log(f"⚠️ [NAVER][EDITOR_SUBMIT_CHECK_ERROR] {e}", "WARNING")
                logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=internal_state", "ERROR")
                return False

            if not disabled:
                break

            elapsed_btn = _time.monotonic() - btn_start
            if elapsed_btn >= BTN_POLL_MAX:
                logger.log("[NAVER][EDITOR_FRAMEWORK_STATE_NOT_UPDATED] submitEnabled=false", "ERROR")
                logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=internal_state", "ERROR")
                return False

            _wait_step(BTN_POLL_INTERVAL)

        logger.log("[NAVER][EDITOR_INTERNAL_READY] submitEnabled=true")
        return True

    @classmethod
    def set_text(cls, page: Page, text: str, stop_flag=None) -> bool:
        """
        텍스트를 에디터에 주입하고 change/input 이벤트를 디스패치한 뒤 정상 주입 여부를 검증.

        Path A: focus() -> fill()
        Path B: placeholder.click() -> fill()  ※ is_visible() 재확인 후 진입
        Path C: execCommand insertText fallback
        """
        clean_t = text.strip() if text else ""
        if clean_t.startswith("```"):
            clean_t = clean_t.strip("`")
            if clean_t.startswith("text") or clean_t.startswith("markdown"):
                clean_t = clean_t.split("\n", 1)[-1]
        clean_t = clean_t.strip()

        try:
            # 1. Comment DOM Context 획득
            context = MobileDOMResolver.get_comment_editor_context(page)
            if not context:
                logger.log("[NAVER][COMMENT_EDITOR_NOT_FOUND]", "WARNING")
                logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=context", "ERROR")
                return False

            editor = context["editor"]
            frame = context["frame"]
            frame_name = context["frame_name"] or "main"

            try:
                tag_name = editor.evaluate("e => e.tagName.toLowerCase()")
                is_textarea = (tag_name == "textarea")
            except Exception:
                is_textarea = False

            # placeholder 상태: 진단 목적으로만 기록, Path B 진입은 재확인
            placeholder_ctx = MobileDOMResolver.get_comment_placeholder_context(page, preferred_frame=frame)
            placeholder_visible_initial = False
            if placeholder_ctx and placeholder_ctx.get("placeholder"):
                try:
                    placeholder_visible_initial = placeholder_ctx["placeholder"].is_visible()
                except Exception:
                    placeholder_visible_initial = False

            logger.log(
                f"[NAVER][EDITOR_WRITE_DISPATCHED] type={'textarea' if is_textarea else 'contenteditable'} "
                f"frame={frame_name} placeholderVisible={str(placeholder_visible_initial).lower()} "
                f"selector={context['selector']} chars={len(clean_t)}"
            )

            try:
                frame.evaluate("() => { window.__NAVER_PROGRAMMATIC_SET__ = true; }")
            except Exception:
                pass

            try:
                # --- Path A: Direct focus() -> fill() ---
                try:
                    editor.focus()
                    logger.log("[NAVER][EDITOR_FOCUS_OK]")
                    editor.fill(clean_t)
                    logger.log(f"[NAVER][EDITOR_FILL_OK] chars={len(clean_t)}")
                    if cls._verify_and_confirm(editor, clean_t, is_textarea, frame, page, stop_flag=stop_flag):
                        return True
                except Exception as e:
                    logger.log(f"ℹ️ [NAVER][EDITOR_PATH_A_NOTE] focus/fill 시도 중: {e}")

                # --- Path B: Placeholder Click -> fill() ---
                # fill 이후 is_visible() 재확인 — 이미 사라진 placeholder를 누르지 않음
                if placeholder_ctx and placeholder_ctx.get("placeholder"):
                    try:
                        placeholder_now_visible = placeholder_ctx["placeholder"].is_visible()
                    except Exception:
                        placeholder_now_visible = False
                    if placeholder_now_visible:
                        try:
                            logger.log(
                                f"[NAVER][EDITOR_PLACEHOLDER_FALLBACK] "
                                f"selector={placeholder_ctx['selector']} visible=true"
                            )
                            placeholder_ctx["placeholder"].click(timeout=1000)
                            editor.focus()
                            logger.log("[NAVER][EDITOR_FOCUS_OK]")
                            editor.fill(clean_t)
                            logger.log(f"[NAVER][EDITOR_FILL_OK] chars={len(clean_t)}")
                            if cls._verify_and_confirm(editor, clean_t, is_textarea, frame, page, stop_flag=stop_flag):
                                return True
                        except Exception as e:
                            logger.log(f"ℹ️ [NAVER][EDITOR_PATH_B_NOTE] placeholder click/fill 시도 중: {e}")
                    else:
                        logger.log("[NAVER][EDITOR_PLACEHOLDER_SKIP] is_visible=false after fill — skipping Path B")

                # --- Path C: execCommand insertText + event dispatch ---
                try:
                    logger.log("[NAVER][EDITOR_EXEC_COMMAND_FALLBACK] insertText with input events")
                    editor.evaluate("""(el, text) => {
                        el.focus();
                        if (el.tagName.toLowerCase() === 'textarea') {
                            el.value = text;
                            el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                        } else {
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, text);
                            el.dispatchEvent(new InputEvent('beforeinput', {
                                bubbles: true,
                                cancelable: true,
                                inputType: 'insertText',
                                data: text
                            }));
                            el.dispatchEvent(new InputEvent('input', {
                                bubbles: true,
                                cancelable: true,
                                inputType: 'insertText',
                                data: text
                            }));
                        }
                    }""", clean_t)
                    if cls._verify_and_confirm(editor, clean_t, is_textarea, frame, page, stop_flag=stop_flag):
                        return True
                except Exception as e:
                    logger.log(f"ℹ️ [NAVER][EDITOR_PATH_C_NOTE] execCommand 시도 중: {e}")

                logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=fill_or_readback", "ERROR")
                return False
            finally:
                try:
                    frame.evaluate("() => { window.__NAVER_PROGRAMMATIC_SET__ = false; }")
                except Exception:
                    pass

        except Exception as e:
            logger.log(f"[EDITOR] 텍스트 설정 중 예외: {e}", "WARNING")
            logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=exception", "ERROR")
            return False
