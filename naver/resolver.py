from typing import Optional, List
from playwright.sync_api import Page, Locator


class MobileDOMResolver:
    """
    모바일 네이버 블로그 페이지의 인터랙티브 엘리먼트 Resolver (v3.1 Verified DOM)
    - 다중 리액션(Reaction Module)과 실제 data-type="like" 옵션의 명확한 분리
    - 안정적인 댓글 버튼(pst.re) 및 에디터 셀렉터 우선순위 적용
    """

    # --- 피드 목록 (FeedList / Recommendation) ---
    @staticmethod
    def get_feed_cards(page: Page) -> Optional[Locator]:
        if not page:
            return None
        return page.locator("li[class*='card_wrapper'], li[class*='item__'], div[class*='card_wrapper']")

    @staticmethod
    def get_card_post_link(card: Locator) -> Optional[Locator]:
        if not card:
            return None
        return card.locator("a[data-click-area*='card'], a[class*='link__'], a[href*='m.blog.naver.com/']").first

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

    # --- 포스트 상세 본문 및 제목 추출 ---
    @staticmethod
    def get_post_title(page: Page) -> Optional[str]:
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

    # --- 포스트 상세 리액션 (Reaction Module & Like Options) ---
    @staticmethod
    def get_reaction_module(page: Page) -> Optional[Locator]:
        """다중 리액션 전체 컨테이너 모듈 반환"""
        if not page:
            return None
        selectors = [
            ".u_likeit_list_module",
            ".u_likeit._reactionModule",
            "div[data-sid='BLOG'][data-cid]",
            "div[class*='Interact__']"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator(".u_likeit_list_module, .u_likeit").first

    @staticmethod
    def get_reaction_summary_button(page: Page) -> Optional[Locator]:
        """공감 요약/오프너 버튼 (총 숫자 표시 버튼, 단독 클릭 대상 아님) 반환"""
        if not page:
            return None
        selectors = [
            ".u_likeit_list_module > a.u_likeit_button",
            ".u_likeit_list_module > button.u_likeit_button",
            "a.u_likeit_button[data-like-click-area]",
            "a.u_likeit_button",
            "button.u_likeit_button"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("a.u_likeit_button, button.u_likeit_button").first

    @staticmethod
    def get_reaction_options(page: Page) -> Optional[Locator]:
        """리액션 레이어 내부의 모든 개별 리액션 버튼(공감, 칭찬, 감사, 웃김, 놀람, 슬픔) Locators 반환"""
        if not page:
            return None
        return page.locator("a.u_likeit_list_button[data-type], button.u_likeit_list_button[data-type], a.u_likeit_list_btn[data-type]")

    @staticmethod
    def get_reaction_like_option(page: Page) -> Optional[Locator]:
        """실제 클릭 대상인 좋아요(공감) 옵션 버튼 반환"""
        if not page:
            return None
        selectors = [
            "a.u_likeit_list_button[data-type='like']",
            "button.u_likeit_list_button[data-type='like']",
            "a.u_likeit_list_btn[data-type='like']",
            "button.u_likeit_list_btn[data-type='like']",
            "[data-type='like'][role='menuitem']",
            "[data-type='like'][role='radio']"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("a.u_likeit_list_button[data-type='like'], a.u_likeit_list_btn[data-type='like']").first

    @staticmethod
    def get_reaction_total_count_text(page: Page) -> Optional[str]:
        """공감 요약 버튼 또는 카운트 엘리먼트에서 숫자 텍스트 추출"""
        if not page:
            return None
        selectors = [
            "a.u_likeit_button ._count",
            "button.u_likeit_button ._count",
            "a.u_likeit_button .u_likeit_text",
            "a.u_likeit_list_button[data-type='like'] ._count",
            ".u_likeit_text._count"
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    txt = el.inner_text().strip()
                    if txt:
                        return txt
            except Exception:
                pass
        return None

    # 하위 호환성 메서드 (기존 LikeButton 호출 대체)
    @staticmethod
    def get_like_button(page: Page) -> Optional[Locator]:
        """기존 코드 호환용: like_option이 있으면 like_option을, 없으면 summary_button 반환"""
        opt = MobileDOMResolver.get_reaction_like_option(page)
        if opt and opt.count() > 0:
            return opt
        return MobileDOMResolver.get_reaction_summary_button(page)

    @staticmethod
    def get_like_count_text(page: Optional[Page], like_btn: Optional[Locator] = None) -> Optional[str]:
        if not page:
            return None
        return MobileDOMResolver.get_reaction_total_count_text(page)

    # --- 포스트 상세 댓글창 및 에디터 ---
    @staticmethod
    def get_comment_button(page: Page) -> Optional[Locator]:
        """게시글 하단 댓글 열기 버튼 (안정적 selector 우선)"""
        if not page:
            return None
        selectors = [
            "button[data-click-area='pst.re']",
            "button[data-click-area*='pst.re']",
            "button:has(.blind:text-is('댓글'))",
            "button.Interact__comment_btn--Wbuoq",
            "button[class^='Interact__comment_btn--']",
            "button[class*='Interact__comment_btn--']",
            "a.btn_comment",
            "a.u_cbox_btn_reply",
            "a:has(.blind:text-is('댓글'))"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("button[data-click-area*='pst.re'], a.btn_comment").first

    @staticmethod
    def get_comment_write_box(page: Page) -> Optional[Locator]:
        """댓글 작성 영역 컨테이너"""
        if not page:
            return None
        selectors = [
            ".u_cbox_write_box",
            ".u_cbox_inbox",
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator(".u_cbox_write_box, .u_cbox_inbox").first

    @staticmethod
    def get_comment_editor(page: Page) -> Optional[Locator]:
        """실제 댓글 입력 에디터"""
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
        return page.locator("#naverComment__write_textarea, div.u_cbox_text[contenteditable='true']").first

    @staticmethod
    def get_comment_editor_context(page: Page):
        """Resolve the editor in the main document or a child frame."""
        if not page:
            return None
        selectors = [
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']",
            "div.u_cbox_write_box div[contenteditable='true']",
            "div.u_cbox_inbox textarea.u_cbox_text",
            "textarea.u_cbox_text",
        ]
        for frame in page.frames:
            for selector in selectors:
                try:
                    loc = frame.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        return {"frame": frame, "editor": loc, "selector": selector, "frame_name": frame.name, "frame_url": frame.url}
                except Exception:
                    continue
        return None

    @staticmethod
    def get_secret_comment_checkbox(page: Page) -> Optional[Locator]:
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
        if not page:
            return None
        selectors = [
            "button[data-action='comment#upload']",
            "button.u_cbox_btn_upload",
            "button:has-text('등록')",
            "input[type='submit'].u_cbox_btn_upload"
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        return page.locator("button.u_cbox_btn_upload").first

    @staticmethod
    def get_comment_submit_context(page: Page, preferred_frame=None):
        frames = [preferred_frame] if preferred_frame else list(page.frames)
        selectors = [
            "button[data-action='comment#upload']",
            "button.u_cbox_btn_upload",
            "button:has-text('등록')",
            "input[type='submit'].u_cbox_btn_upload",
        ]
        for frame in frames:
            if frame is None:
                continue
            for selector in selectors:
                try:
                    loc = frame.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        return {"frame": frame, "button": loc, "selector": selector, "frame_name": frame.name, "frame_url": frame.url}
                except Exception:
                    continue
    @staticmethod
    def get_comment_placeholder_context(page: Page, preferred_frame=None):
        """댓글 입력창 플레이스홀더 (.u_cbox_guide 등) 탐색"""
        frames = [preferred_frame] if preferred_frame else list(page.frames)
        selectors = [
            ".u_cbox_guide[data-action='write#placeholder']",
            ".u_cbox_guide",
            ".u_cbox_write_box .u_cbox_guide",
            ".u_cbox_inbox .u_cbox_guide",
        ]
        for frame in frames:
            if frame is None:
                continue
            for selector in selectors:
                try:
                    loc = frame.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        return {"frame": frame, "placeholder": loc, "selector": selector, "frame_name": frame.name, "frame_url": frame.url}
                except Exception:
                    continue
        return None


# 하위 호환성 별칭 (Aliases)
MobileDOMResolver.get_comment_open_button = MobileDOMResolver.get_comment_button
MobileDOMResolver.get_secret_checkbox = MobileDOMResolver.get_secret_comment_checkbox
