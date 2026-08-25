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
        return MobileDOMResolver.get_comment_editor(page)

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
            return page.evaluate("""
                () => {
                    const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                    if (!editor) return false;
                    editor.focus();
                    return true;
                }
            """)
        except Exception:
            return False

    @classmethod
    def get_text(cls, page: Page) -> str:
        try:
            return page.evaluate("""
                () => {
                    const editor = document.querySelector('#naverComment__write_textarea, div.u_cbox_text[contenteditable="true"], textarea.u_cbox_text');
                    if (!editor) return '';
                    if (editor.tagName.toLowerCase() === 'textarea') {
                        return (editor.value || '').trim();
                    }
                    return (editor.innerText || editor.textContent || '').trim();
                }
            """) or ""
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
            page.evaluate("""
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

            # 2. Read-back verification (주입된 내용과 일치하는지 검증)
            read_t = cls.get_text(page)
            # 줄바꿈 정규화 비교
            if read_t.replace("\r", "").strip() == clean_t.replace("\r", "").strip():
                return True

            # 3. Fallback: locator fill
            editor = cls.get_editor(page)
            if editor and editor.count() > 0:
                editor.click(timeout=1000)
                editor.fill(clean_t)
                editor.focus()
                return True

            return False
        except Exception as e:
            logger.log(f"[EDITOR] 텍스트 설정 중 예외: {e}", "WARNING")
            return False
