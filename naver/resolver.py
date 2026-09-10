from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict
from playwright.sync_api import Page, Locator


@dataclass
class CommentOpenContext:
    frame: Any
    button: Any
    selector: str
    index: int
    frame_url: str
    visible: bool
    enabled: bool
    bbox: Optional[Dict[str, float]] = None
    text: str = ""
    aria_label: str = ""


@dataclass
class CommentDOMContext:
    frame: Any
    open_button: Optional[Any] = None
    comment_root: Optional[Any] = None
    editor: Optional[Any] = None
    submit_button: Optional[Any] = None
    write_box: Optional[Any] = None
    login_box: Optional[Any] = None
    disabled_box: Optional[Any] = None
    comment_list_root: Optional[Any] = None
    selector: str = ""
    frame_name: str = ""
    frame_url: str = ""


class MobileDOMResolver:
    """
    모바일 네이버 블로그 페이지의 인터랙티브 엘리먼트 Resolver (v3.1 Verified DOM)
    - 다중 리액션(Reaction Module)과 실제 data-type="like" 옵션의 명확한 분리
    - 안정적인 댓글 버튼(pst.re) 및 에디터 셀렉터 우선순위 적용
    - CommentOpenContext / CommentDOMContext 기반의 프레임 인식형 통합 리졸버
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

    # --- 프레임 안전 접근 헬퍼 ---
    @staticmethod
    def _safe_get_frames(page: Page) -> List[Any]:
        if not page:
            return []
        frames = []
        try:
            if hasattr(page, "frames"):
                f_attr = page.frames
                if callable(f_attr):
                    frames = list(f_attr())
                elif isinstance(f_attr, (list, tuple)):
                    frames = list(f_attr)
                elif hasattr(f_attr, "__iter__"):
                    frames = list(f_attr)
        except Exception:
            pass
        if not frames:
            frames = [page]
        return frames

    # --- 포스트 상세 댓글창 및 오프너 후보 탐색 ---
    @staticmethod
    def get_all_comment_opener_candidates(page: Page) -> List[CommentOpenContext]:
        """
        모든 프레임 및 셀렉터의 댓글 오프너 후보를 전수 조사하여 반환.
        첫 번째 후보가 hidden이더라도 탐색을 멈추지 않고 전수 인벤토리를 수집함.
        """
        candidates: List[CommentOpenContext] = []
        if not page:
            return candidates

        selectors = [
            "button[data-click-area='pst.re']",
            "button[data-click-area*='pst.re']",
            "button.Interact__comment_btn--Wbuoq",
            "button[class^='Interact__comment_btn--']",
            "button[class*='Interact__comment_btn--']",
            "button:has(.blind:text-is('댓글'))",
            "a:has(.blind:text-is('댓글'))",
            "a.btn_comment",
            "a.u_cbox_btn_reply",
            ".u_cbox_btn_total",
            "button:has-text('댓글')",
            "a:has-text('댓글')",
            "[role='button']:has-text('댓글')",
        ]

        frames = MobileDOMResolver._safe_get_frames(page)
        for frame in frames:
            if not frame:
                continue
            frame_url = getattr(frame, "url", "")
            for sel in selectors:
                try:
                    loc = frame.locator(sel)
                    cnt = loc.count() if hasattr(loc, "count") else 0
                    for idx in range(cnt):
                        try:
                            cand_loc = loc.nth(idx) if hasattr(loc, "nth") else loc
                            is_vis = False
                            is_en = True
                            bbox = None
                            txt = ""
                            aria = ""
                            try:
                                is_vis = cand_loc.is_visible() if hasattr(cand_loc, "is_visible") else False
                            except Exception:
                                is_vis = False
                            try:
                                is_en = cand_loc.is_enabled() if hasattr(cand_loc, "is_enabled") else True
                            except Exception:
                                is_en = True
                            if is_vis:
                                try:
                                    bbox = cand_loc.bounding_box() if hasattr(cand_loc, "bounding_box") else None
                                except Exception:
                                    bbox = None
                            try:
                                txt = (cand_loc.inner_text() or "").strip() if hasattr(cand_loc, "inner_text") else ""
                            except Exception:
                                txt = ""
                            try:
                                aria = (cand_loc.get_attribute("aria-label") or "") if hasattr(cand_loc, "get_attribute") else ""
                            except Exception:
                                aria = ""
                            candidates.append(CommentOpenContext(
                                frame=frame,
                                button=cand_loc,
                                selector=sel,
                                index=idx,
                                frame_url=frame_url,
                                visible=bool(is_vis),
                                enabled=bool(is_en),
                                bbox=bbox,
                                text=txt,
                                aria_label=aria,
                            ))
                        except Exception:
                            continue
                except Exception:
                    continue
        return candidates

    @staticmethod
    def get_comment_open_context(page: Page) -> Optional[CommentOpenContext]:
        """
        모든 프레임 및 셀렉터 후보 중 visible + enabled인 최적의 오프너 반환.
        첫 번째 후보가 hidden이더라도 탐색을 중단하지 않고 visible 후보를 찾음.
        """
        candidates = MobileDOMResolver.get_all_comment_opener_candidates(page)
        visible_enabled = [c for c in candidates if c.visible and c.enabled]
        if visible_enabled:
            with_box = [c for c in visible_enabled if c.bbox and c.bbox.get("width", 0) > 0 and c.bbox.get("height", 0) > 0]
            if with_box:
                return with_box[0]
            return visible_enabled[0]
        return None

    @staticmethod
    def get_comment_button(page: Page) -> Optional[Locator]:
        """
        게시글 하단 댓글 열기 버튼 Locator 반환 (하위 호환성 유지).
        visible 후보 우선 반환, 없을 경우 첫 번째 후보 반환.
        """
        ctx = MobileDOMResolver.get_comment_open_context(page)
        if ctx and ctx.button:
            return ctx.button
        candidates = MobileDOMResolver.get_all_comment_opener_candidates(page)
        if candidates and candidates[0].button:
            return candidates[0].button
        if page:
            try:
                return page.locator("button[data-click-area*='pst.re'], a.btn_comment").first
            except Exception:
                pass
        return None

    @staticmethod
    def get_comment_dom_context(page: Page, preferred_frame=None) -> Optional[CommentDOMContext]:
        """
        댓글 관련 모든 요소(opener, root, editor, submit, write_box, login, disabled, list)를
        동일한 frame 맥락에서 통합 해결하는 Context Resolver
        """
        if not page:
            return None
        frames = [preferred_frame] if preferred_frame else MobileDOMResolver._safe_get_frames(page)

        editor_selectors = [
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']",
            "div.u_cbox_write_box div[contenteditable='true']",
            "div.u_cbox_inbox textarea.u_cbox_text",
            "textarea.u_cbox_text",
        ]
        submit_selectors = [
            "button[data-action='comment#upload']",
            "button.u_cbox_btn_upload",
            "button:has-text('등록')",
            "input[type='submit'].u_cbox_btn_upload",
        ]
        root_selectors = [
            ".u_cbox_wrap",
            ".u_cbox_area",
            ".u_cbox",
            "#naverComment",
            "div[class*='CommentLayer']",
        ]
        write_box_selectors = [
            ".u_cbox_write_box",
            ".u_cbox_inbox",
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']",
        ]
        login_selectors = [
            ".u_cbox_write_box.u_cbox_type_logged_out",
            ".u_cbox_guide:has-text('로그인')",
            "a:has-text('로그인한 사용자만')",
        ]
        disabled_selectors = [
            ".u_cbox_none",
            ".u_cbox_notice_disabled",
            "div:text-is('댓글을 작성할 수 없습니다')",
        ]
        list_selectors = [
            ".u_cbox_list",
            "ul.u_cbox_list",
            "div.u_cbox_content_wrap",
        ]

        for frame in frames:
            if not frame:
                continue
            frame_url = getattr(frame, "url", "")
            frame_name = getattr(frame, "name", "")

            resolved_editor = None
            resolved_selector = ""
            for sel in editor_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        resolved_editor = el
                        resolved_selector = sel
                        break
                except Exception:
                    continue

            resolved_submit = None
            for sel in submit_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        resolved_submit = el
                        break
                except Exception:
                    continue

            resolved_root = None
            for sel in root_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0:
                        resolved_root = el
                        break
                except Exception:
                    continue

            resolved_write_box = None
            for sel in write_box_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0:
                        resolved_write_box = el
                        break
                except Exception:
                    continue

            resolved_login = None
            for sel in login_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        resolved_login = el
                        break
                except Exception:
                    continue

            resolved_disabled = None
            for sel in disabled_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        resolved_disabled = el
                        break
                except Exception:
                    continue

            resolved_list = None
            for sel in list_selectors:
                try:
                    el = frame.locator(sel).first
                    if el.count() > 0:
                        resolved_list = el
                        break
                except Exception:
                    continue

            if resolved_editor or resolved_write_box or resolved_root or resolved_login or resolved_disabled:
                return CommentDOMContext(
                    frame=frame,
                    open_button=None,
                    comment_root=resolved_root,
                    editor=resolved_editor,
                    submit_button=resolved_submit,
                    write_box=resolved_write_box,
                    login_box=resolved_login,
                    disabled_box=resolved_disabled,
                    comment_list_root=resolved_list,
                    selector=resolved_selector,
                    frame_name=frame_name,
                    frame_url=frame_url,
                )

        return None

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
        frames = MobileDOMResolver._safe_get_frames(page)
        for frame in frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0:
                        return loc
                except Exception:
                    continue
        return page.locator(".u_cbox_write_box, .u_cbox_inbox").first

    @staticmethod
    def get_comment_editor(page: Page) -> Optional[Locator]:
        """실제 댓글 입력 에디터"""
        if not page:
            return None
        ctx = MobileDOMResolver.get_comment_editor_context(page)
        if ctx and ctx.get("editor"):
            return ctx["editor"]
        selectors = [
            "#naverComment__write_textarea",
            "div.u_cbox_text[contenteditable='true']",
            "div.u_cbox_write_box div[contenteditable='true']",
            "div.u_cbox_inbox textarea.u_cbox_text",
            "textarea.u_cbox_text"
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
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
        frames = MobileDOMResolver._safe_get_frames(page)
        for frame in frames:
            for selector in selectors:
                try:
                    loc = frame.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        return {"frame": frame, "editor": loc, "selector": selector, "frame_name": getattr(frame, "name", ""), "frame_url": getattr(frame, "url", "")}
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
        frames = MobileDOMResolver._safe_get_frames(page)
        for frame in frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0:
                        return loc
                except Exception:
                    continue
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
        frames = MobileDOMResolver._safe_get_frames(page)
        for frame in frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0:
                        return loc
                except Exception:
                    continue
        return page.locator("button.u_cbox_btn_upload").first

    @staticmethod
    def get_comment_submit_context(page: Page, preferred_frame=None):
        frames = [preferred_frame] if preferred_frame else MobileDOMResolver._safe_get_frames(page)
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
                        return {"frame": frame, "button": loc, "selector": selector, "frame_name": getattr(frame, "name", ""), "frame_url": getattr(frame, "url", "")}
                except Exception:
                    continue
        return None

    @staticmethod
    def get_comment_placeholder_context(page: Page, preferred_frame=None):
        """댓글 입력창 플레이스홀더 (.u_cbox_guide 등) 탐색"""
        frames = [preferred_frame] if preferred_frame else MobileDOMResolver._safe_get_frames(page)
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
                        return {"frame": frame, "placeholder": loc, "selector": selector, "frame_name": getattr(frame, "name", ""), "frame_url": getattr(frame, "url", "")}
                except Exception:
                    continue
        return None


# 하위 호환성 별칭 (Aliases)
MobileDOMResolver.get_comment_open_button = MobileDOMResolver.get_comment_button
MobileDOMResolver.get_secret_checkbox = MobileDOMResolver.get_secret_comment_checkbox
