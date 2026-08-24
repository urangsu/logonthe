from typing import Optional, List
from playwright.sync_api import Page, Locator


class MobileDOMResolver:
    """
    모바일 네이버 블로그 페이지의 인터랙티브 엘리먼트 Resolver
    우선순위:
    1. Accessible Role/Name -> 2. Stable ID -> 3. Data Attribute -> 4. Blind Text -> 5. Class Prefix -> 6. Hashed Class
    """

    # --- 피드 목록 (FeedList / Recommendation) ---
    @staticmethod
    def get_feed_cards(page: Page) -> Locator:
        """피드 목록 내 게시글 카드 Locator 반환"""
        cards = page.locator("li[class*='card_wrapper'], li[class*='item__'], div[class*='card_wrapper']")
        return cards

    @staticmethod
    def get_card_post_link(card: Locator) -> Locator:
        """피드 카드 내부의 게시글 상세 링크 Locator 반환"""
        link = card.locator("a[data-click-area*='card'], a[class*='link__'], a[href*='m.blog.naver.com/']").first
        return link

    @staticmethod
    def get_card_author(card: Locator) -> Optional[str]:
        try:
            author_el = card.locator("span[class*='name__'], span.author").first
            if author_el.count() > 0:
                return author_el.inner_text().strip()
        except Exception:
            pass
        return None

    @staticmethod
    def get_card_title(card: Locator) -> Optional[str]:
        try:
            title_el = card.locator("strong[class*='title__'], .title").first
            if title_el.count() > 0:
                return title_el.inner_text().strip()
        except Exception:
            pass
        return None

    # --- 포스트 상세 본문 및 제목 추출 (Context Extraction) ---
    @staticmethod
    def get_post_title(page: Page) -> Optional[str]:
        """게시글 상세 페이지의 제목 추출"""
        title_selectors = [
            ".se-title-text",
            "div.tit_area h3",
            "div.post_title",
            "div.tit_area",
            "h3.tit_h3",
            "title"
        ]
        for sel in title_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                try:
                    txt = loc.inner_text().strip()
                    if txt:
                        return txt
                except Exception:
                    continue
        return None

    @staticmethod
    def get_post_content_locator(page: Page) -> Locator:
        """게시글 본문 컨테이너 Locator 반환"""
        content_selectors = [
            ".se-main-container",
            ".se-viewer",
            "#postViewArea",
            ".post_ct",
            ".post_view",
            "div.post_content",
            "article"
        ]
        for sel in content_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator(".se-viewer, #postViewArea").first

    # --- 포스트 상세 (Detail Page) 공감(하트) ---
    @staticmethod
    def get_like_button(page: Page) -> Locator:
        """게시글 상세 페이지의 공감(하트) 버튼 Locator 반환"""
        selectors = [
            "button.u_likeit_button",
            "button[data-click-area='pst.like']",
            "div[class*='Interact__'] button:has(.blind:text-is('공감'))",
            "a.u_likeit_button",
            "a._sympathyButton"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc

        return page.locator("button.u_likeit_button").first

    # --- 포스트 상세 (Detail Page) 댓글 ---
    @staticmethod
    def get_comment_open_button(page: Page) -> Locator:
        """게시글 상세 페이지의 댓글창 열기 버튼 Locator 반환"""
        selectors = [
            "button[data-click-area='pst.re']",
            "button:has(.blind:text-is('댓글'))",
            "button[class*='Interact__comment_btn']",
            "a._floating_bottom_btn_comment",
            "a.btn_comment",
            "#btn_comment_2"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc

        return page.locator("button[data-click-area='pst.re']").first

    @staticmethod
    def get_comment_editor(page: Page) -> Locator:
        """댓글 ContentEditable 입력창 Locator 반환"""
        loc = page.locator("#naverComment__write_textarea")
        if loc.count() > 0:
            return loc

        fallback = page.locator("div.u_cbox_text[contenteditable='true'], textarea.u_cbox_text").first
        return fallback

    @staticmethod
    def get_comment_write_box(page: Page) -> Locator:
        """비활성화된 댓글 입력창 영역(클릭하여 포커스/확장 트리거) Locator 반환"""
        return page.locator("div.u_cbox_write_box, div.u_cbox_write_inner, div.u_cbox_write_area").first

    @staticmethod
    def get_secret_checkbox(page: Page) -> Locator:
        """비밀댓글 체크박스 및 라벨 Locator 반환"""
        chk = page.locator("input#naverComment__write_textarea_secret_check, input.u_cbox_secret_check").first
        if chk.count() > 0:
            return chk
        return page.locator("label[for='naverComment__write_textarea_secret_check'], label.u_cbox_secret_label").first

    @staticmethod
    def get_comment_submit_button(page: Page) -> Locator:
        """댓글 등록 버튼 Locator 반환"""
        selectors = [
            "button.u_cbox_btn_upload",
            "button[data-action='write#request']",
            "button.__uis_naverComment_writeButton",
            ".u_cbox_btn_upload"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc

        return page.locator("button.u_cbox_btn_upload").first
