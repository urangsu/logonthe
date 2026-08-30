from typing import Optional
from playwright.sync_api import Page, Locator
from naver.resolver import MobileDOMResolver
from src.logger import logger


class CommentEditorAdapter:
    """
    네이버 블로그 댓글 에디터(contenteditable div / textarea) 통합 어댑터
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
            return True
        except Exception:
            return False

    @classmethod
    def get_text(cls, page: Page) -> str:
        try:
            context = MobileDOMResolver.get_comment_editor_context(page)
            if not context:
                return ""
            return (context["editor"].input_value() if context["editor"].evaluate("e => e.tagName.toLowerCase() === 'textarea'") else context["editor"].inner_text()).strip()
        except Exception:
            return ""

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
            # 1. JS evaluate로 안전하게 주입
            context = MobileDOMResolver.get_comment_editor_context(page)
            if not context:
                logger.log("[NAVER][COMMENT_EDITOR_NOT_FOUND]", "WARNING")
                return False
            editor = context["editor"]
            logger.log(f"[NAVER][COMMENT_EDITOR_FOUND] frame={context['frame_name'] or 'main'} selector={context['selector']} frameUrl={context['frame_url']}")
            editor.click(timeout=1000)
            editor.fill(clean_t)

            # 2. Read-back verification (주입된 내용과 일치하는지 검증)
            read_t = cls.get_text(page)
            # 줄바꿈 정규화 비교
            if read_t.replace("\r", "").strip() == clean_t.replace("\r", "").strip():
                logger.log(f"[NAVER][EDITOR_READBACK_OK] chars={len(read_t)}")
                submit_context = MobileDOMResolver.get_comment_submit_context(page, context["frame"])
                if not submit_context:
                    logger.log("[NAVER][COMMENT_SUBMIT_NOT_FOUND]", "ERROR")
                    return False
                disabled = submit_context["button"].is_disabled()
                if disabled:
                    logger.log("[NAVER][EDITOR_FRAMEWORK_STATE_NOT_UPDATED] submitEnabled=false", "ERROR")
                    return False
                logger.log("[NAVER][EDITOR_INTERNAL_READY] submitEnabled=true")
                return True
            logger.log(f"[NAVER][EDITOR_READBACK_MISMATCH] expectedChars={len(clean_t)} actualChars={len(read_t)}", "ERROR")
            return False
        except Exception as e:
            logger.log(f"[EDITOR] 텍스트 설정 중 예외: {e}", "WARNING")
            return False
