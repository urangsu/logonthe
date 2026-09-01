from typing import Optional
from playwright.sync_api import Page, Locator
from naver.resolver import MobileDOMResolver
from src.logger import logger


class CommentEditorAdapter:
    """
    네이버 블로그 댓글 에디터(contenteditable div / textarea) 통합 어댑터
    - Path A: Focus -> Fill (마우스 hit-test 회피)
    - Path B: Overlay Placeholder Click -> Focus -> Fill (fallback)
    - Path C: execCommand insertText + beforeinput/input 이벤트 디스패치 (framework state 갱신 fallback)
    - 동일 Frame/Locator 내 Exact Read-back 및 Submit Button 활성화 검증
    """

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
            logger.log(f"[NAVER][COMMENT_EDITOR_FOUND] frame={context['frame_name'] or 'main'} selector={context['selector']} frameUrl={context['frame_url']}")
            logger.log("[NAVER][EDITOR_FOCUS_OK]")
            return True
        except Exception:
            return False

    @classmethod
    def get_text(cls, page: Page) -> str:
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
            return (editor.input_value() if is_textarea else editor.inner_text()).strip()
        except Exception:
            return ""

    @classmethod
    def _verify_and_confirm(cls, editor: Locator, clean_t: str, is_textarea: bool, frame, page: Page) -> bool:
        """동일 editor locator 및 frame 내에서 exact read-back 및 submit button 활성화 검증"""
        try:
            if is_textarea:
                read_t = editor.input_value().strip()
            else:
                read_t = editor.inner_text().strip()
        except Exception as e:
            logger.log(f"⚠️ [NAVER][EDITOR_READBACK_ERROR] {e}", "WARNING")
            return False

        if read_t.replace("\r", "").strip() != clean_t.replace("\r", "").strip():
            logger.log(f"[NAVER][EDITOR_READBACK_MISMATCH] expectedChars={len(clean_t)} actualChars={len(read_t)}", "WARNING")
            return False

        logger.log(f"[NAVER][EDITOR_READBACK_OK] chars={len(read_t)}")

        submit_context = MobileDOMResolver.get_comment_submit_context(page, frame)
        if not submit_context:
            logger.log("[NAVER][COMMENT_SUBMIT_NOT_FOUND]", "ERROR")
            logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=internal_state", "ERROR")
            return False

        try:
            disabled = submit_context["button"].is_disabled()
        except Exception:
            disabled = False

        if disabled:
            logger.log("[NAVER][EDITOR_FRAMEWORK_STATE_NOT_UPDATED] submitEnabled=false", "ERROR")
            logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=internal_state", "ERROR")
            return False

        logger.log("[NAVER][EDITOR_INTERNAL_READY] submitEnabled=true")
        return True

    @classmethod
    def set_text(cls, page: Page, text: str) -> bool:
        """텍스트를 주입하고 change/input 이벤트를 디스패치하며, 정상 주입 여부를 검증"""
        clean_t = text.strip() if text else ""
        if clean_t.startswith("```"):
            clean_t = clean_t.strip("`")
            if clean_t.startswith("text") or clean_t.startswith("markdown"):
                clean_t = clean_t.split("\n", 1)[-1]
        clean_t = clean_t.strip()

        try:
            # 1. Comment DOM Context 획득 (트랜잭션 동안 유지)
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

            placeholder_ctx = MobileDOMResolver.get_comment_placeholder_context(page, preferred_frame=frame)
            placeholder_visible = False
            if placeholder_ctx and placeholder_ctx.get("placeholder"):
                try:
                    placeholder_visible = placeholder_ctx["placeholder"].is_visible()
                except Exception:
                    placeholder_visible = False

            logger.log(
                f"[NAVER][EDITOR_INPUT] type={'textarea' if is_textarea else 'contenteditable'} "
                f"frame={frame_name} placeholderVisible={str(placeholder_visible).lower()} "
                f"selector={context['selector']}"
            )

            # --- Path A: Direct focus() -> fill() (Pointer Event Interception 방지) ---
            try:
                editor.focus()
                logger.log("[NAVER][EDITOR_FOCUS_OK]")
                editor.fill(clean_t)
                logger.log(f"[NAVER][EDITOR_FILL_OK] chars={len(clean_t)}")
                if cls._verify_and_confirm(editor, clean_t, is_textarea, frame, page):
                    return True
            except Exception as e:
                logger.log(f"ℹ️ [NAVER][EDITOR_PATH_A_NOTE] focus/fill 시도 중: {e}")

            # --- Path B: Placeholder Click -> Focus -> Fill (Overlay 제거 후 재시도) ---
            if placeholder_ctx and placeholder_ctx.get("placeholder"):
                try:
                    logger.log(f"[NAVER][EDITOR_PLACEHOLDER_FALLBACK] selector={placeholder_ctx['selector']}")
                    placeholder_ctx["placeholder"].click(timeout=1000)
                    editor.focus()
                    logger.log("[NAVER][EDITOR_FOCUS_OK]")
                    editor.fill(clean_t)
                    logger.log(f"[NAVER][EDITOR_FILL_OK] chars={len(clean_t)}")
                    if cls._verify_and_confirm(editor, clean_t, is_textarea, frame, page):
                        return True
                except Exception as e:
                    logger.log(f"ℹ️ [NAVER][EDITOR_PATH_B_NOTE] placeholder click/fill 시도 중: {e}")

            # --- Path C: execCommand insertText + beforeinput/input dispatch fallback ---
            try:
                logger.log("[NAVER][EDITOR_EXEC_COMMAND_FALLBACK] insertText with input events")
                editor.evaluate("""(el, text) => {
                    el.focus();
                    if (el.tagName.toLowerCase() === 'textarea') {
                        el.value = text;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    } else {
                        document.execCommand("selectAll", false, null);
                        document.execCommand("insertText", false, text);
                        el.dispatchEvent(new InputEvent("beforeinput", {
                            bubbles: true,
                            inputType: "insertText",
                            data: text
                        }));
                        el.dispatchEvent(new InputEvent("input", {
                            bubbles: true,
                            inputType: "insertText",
                            data: text
                        }));
                    }
                }""", clean_t)
                if cls._verify_and_confirm(editor, clean_t, is_textarea, frame, page):
                    return True
            except Exception as e:
                logger.log(f"ℹ️ [NAVER][EDITOR_PATH_C_NOTE] execCommand 시도 중: {e}")

            logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=fill_or_readback", "ERROR")
            return False

        except Exception as e:
            logger.log(f"[EDITOR] 텍스트 설정 중 예외: {e}", "WARNING")
            logger.log("[NAVER][EDITOR_INPUT_FAIL] stage=exception", "ERROR")
            return False
