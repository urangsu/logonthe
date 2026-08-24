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
    def get_feed_cards(page: Page) -> Optional[Locator]:
        """피드 목록 내 게시글 카드 Locator 반환"""
        if not page:
            return None
        cards = page.locator("li[class*='card_wrapper'], li[class*='item__'], div[class*='card_wrapper']")
        return cards

    @staticmethod
    def get_card_post_link(card: Locator) -> Optional[Locator]:
        """피드 카드 내부의 게시글 상세 링크 Locator 반환"""
        if not card:
            return None
        link = card.locator("a[data-click-area*='card'], a[class*='link__'], a[href*='m.blog.naver.com/']").first
        return link

    @staticmethod
    def get_card_author(card: Locator) -> Optional[str]:
        if not card:
            return None
        try:
            author_el = card.locator("span[class*='name__'], span.author").first
            if author_el.count() > 0:
                return author_el.inner_text().strip()
        except Exception:
            pass
        return None

    @staticmethod
    def get_card_title(card: Locator) -> Optional[str]:
        if not card:
            return None
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
        if not page:
            return None
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
    def get_post_content_locator(page: Page) -> Optional[Locator]:
        """게시글 본문 컨테이너 Locator 반환"""
        if not page:
            return None
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

    # --- 포스트 상세 (Detail Page) 공감(하트) 및 공감수 ---
    @staticmethod
    def get_like_button(page: Page) -> Optional[Locator]:
        """게시글 상세 페이지의 공감(하트) 버튼 Locator 반환"""
        if not page:
            return None
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
        return page.locator("button.u_likeit_button, a.u_likeit_button").first

    @staticmethod
    def get_like_count_text(page: Optional[Page], like_btn: Optional[Locator] = None) -> Optional[str]:
        """공감 버튼 주변 또는 공감수 엘리먼트에서 숫자 텍스트 추출"""
        if not page and not like_btn:
            return None

        btn = like_btn or MobileDOMResolver.get_like_button(page)
        if btn and btn.count() > 0:
            sub_selectors = [
                ".u_likeit_text",
                "._count",
                "em[class*='count']",
                "span[class*='count']",
                "span[class*='num']",
                "span[class*='text']"
            ]
            for sel in sub_selectors:
                try:
                    el = btn.locator(sel).first
                    if el.count() > 0:
                        txt = el.inner_text().strip()
                        if txt:
                            return txt
                except Exception:
                    pass

            try:
                aria = btn.get_attribute("aria-label")
                if aria and ("공감" in aria or any(c.isdigit() for c in aria)):
                    return aria
            except Exception:
                pass

            try:
                txt = btn.inner_text().strip()
                if txt:
                    return txt
            except Exception:
                pass

        if page:
            fallback_selectors = [
                ".u_likeit_text",
                "a.u_likeit_button em",
                "button.u_likeit_button em",
                "span[class*='count__']"
            ]
            for sel in fallback_selectors:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        txt = el.inner_text().strip()
                        if txt:
                            return txt
                except Exception:
                    pass

        return None

    # --- 포스트 상세 (Detail Page) 댓글창 및 입력 에디터 ---
    @staticmethod
    def get_comment_button(page: Page) -> Optional[Locator]:
        """게시글 하단 댓글 열기 버튼 Locator 반환"""
        if not page:
            return None
        selectors = [
            "a.u_cbox_btn_reply",
            "button[data-click-area='pst.reply']",
            "a[data-action='comment#open']",
            "a.btn_comment",
            "a:has(.blind:text-is('댓글'))"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("a.u_cbox_btn_reply, button[data-click-area='pst.reply']").first

    @staticmethod
    def get_comment_write_box(page: Page) -> Optional[Locator]:
        """댓글 작성 영역(write_box 컨테이너) Locator 반환"""
        if not page:
            return None
        selectors = [
            ".u_cbox_write_box",
            ".u_cbox_inbox",
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']",
            "textarea.u_cbox_text"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator(".u_cbox_write_box, .u_cbox_inbox").first

    @staticmethod
    def get_comment_editor(page: Page) -> Optional[Locator]:
        """댓글 입력 에디터 Locator 반환"""
        if not page:
            return None
        selectors = [
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']",
            "div.u_cbox_write_box div[contenteditable='true']",
            "div.u_cbox_inbox textarea.u_cbox_text",
            "textarea.u_cbox_text"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("div.u_cbox_text[contenteditable='true'], textarea.u_cbox_text").first

    @staticmethod
    def get_secret_comment_checkbox(page: Page) -> Optional[Locator]:
        """비밀댓글 토글 체크박스 Locator 반환"""
        if not page:
            return None
        selectors = [
            "input.u_cbox_secret_checkbox",
            "input[type='checkbox'][name='secret']",
            "label.u_cbox_secret_label"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("input.u_cbox_secret_checkbox").first

    @staticmethod
    def get_comment_submit_button(page: Page) -> Optional[Locator]:
        """댓글 등록 버튼 Locator 반환"""
        if not page:
            return None
        selectors = [
            "button.u_cbox_btn_upload",
            "button[data-action='comment#upload']",
            "button:has-text('등록')",
            "input[type='submit'].u_cbox_btn_upload"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("button.u_cbox_btn_upload").first


# 하위 호환성 별칭 (Compatibility Aliases)
MobileDOMResolver.get_comment_open_button = MobileDOMResolver.get_comment_button
MobileDOMResolver.get_secret_checkbox = MobileDOMResolver.get_secret_comment_checkbox
