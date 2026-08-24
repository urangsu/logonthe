import os
import json
import threading
from typing import Optional, Set, Tuple
from playwright.sync_api import Page, Frame
from src.logger import logger
from src.browser import random_sleep, interruptible_wait
from src.spintax import parse_spintax
from src.collector import normalize_blog_post_url
from src.types import TaskStatus

HISTORY_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history.json"))


class AutoCommenter:
    def __init__(self, min_delay: float = 0.0, max_delay: float = 8.0, stop_event: Optional[threading.Event] = None, history_file: str = HISTORY_FILE):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.stop_event = stop_event
        self.history_file = history_file
        self.history: Set[str] = self._load_history()

    def is_stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _load_history(self) -> Set[str]:
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(list(self.history), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.log(f"히스토리 저장 실패: {e}", "WARNING")

    def post_comment(self, page: Page, blog_url: str, comment_template: str, secret_comment: bool = False) -> Tuple[bool, TaskStatus]:
        """
        [2번 기능] 네이버 블로그 포스트 댓글 작성
        (연속 보기로 인한 최하단 지나침 방지: 해당 글 본문 하단 댓글 영역을 직접 타겟팅)
        """
        if self.is_stopped():
            return False, TaskStatus.STOPPED

        direct_url = normalize_blog_post_url(blog_url)

        if direct_url in self.history or blog_url in self.history:
            logger.log(f"[2번 기능] 이미 작성 완료된 글입니다 (건너뜀): {blog_url}", "WARNING")
            return False, TaskStatus.COMPLETED

        logger.log(f"[2번 기능] 대상 포스트 이동: {direct_url}")
        try:
            page.goto(direct_url, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            logger.log(f"[2번 기능] 페이지 로드 안내: {e}", "WARNING")

        if interruptible_wait(self.stop_event, 1.5):
            return False, TaskStatus.STOPPED

        frame = self._get_main_frame(page)
        context = frame if frame else page

        # 1. 연속보기 방지: 전체 document.body 최하단이 아닌 해당 포스트의 본문/푸터 위치를 찾아 스크롤
        try:
            post_footer = context.query_selector(
                "div.wrap_postcomment, div.post_footer_contents, div.area_comment, div#comment_module, div.post_btn_comment, div.box_reply, a.btn_comment"
            ) or page.query_selector(
                "div.wrap_postcomment, div.post_footer_contents, div.area_comment, div#comment_module, div.post_btn_comment, div.box_reply, a.btn_comment"
            )

            if post_footer:
                post_footer.scroll_into_view_if_needed(timeout=2000)
            else:
                # 본문 뷰어 하단 위치로 스크롤
                body_elem = context.query_selector(".se-viewer, #postViewArea, .post_content") or page.query_selector(".se-viewer, #postViewArea, .post_content")
                if body_elem:
                    body_elem.scroll_into_view_if_needed(timeout=2000)
                else:
                    # 중간 위치까지만 스크롤 (연속보기 진입 방지)
                    page.evaluate("window.scrollTo(0, 1500)")
        except Exception:
            pass

        if interruptible_wait(self.stop_event, 1.0):
            return False, TaskStatus.STOPPED

        try:
            # 2. 닫혀 있는 댓글 열기 버튼 탐색 및 클릭
            open_selectors = [
                "a.btn_comment",
                "a._commentCount",
                "div.post_btn_comment a",
                "a.u_cbox_btn_comment",
                "button.btn_comment",
                "span.btn_comment",
                "a._floating_bottom_btn_comment",
                "#btn_comment_2",
                "a[onclick*='replyopen']",
                "#comment_module a"
            ]

            for sel in open_selectors:
                try:
                    btn = context.query_selector(sel) or page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.scroll_into_view_if_needed(timeout=1500)
                        btn.click(timeout=1500)
                        logger.log("  📂 댓글 열기 버튼 클릭 완료")
                        interruptible_wait(self.stop_event, 1.2)
                        break
                except Exception:
                    continue

            # 3. 로그인 상태 확인 (Cbox 비로그인 알림 영역)
            login_box = context.query_selector(".u_cbox_login, .u_cbox_empty_text, .u_cbox_guide_text") or page.query_selector(".u_cbox_login, .u_cbox_empty_text, .u_cbox_guide_text")
            if login_box:
                txt = login_box.inner_text() or ""
                if "로그인" in txt:
                    logger.log("⚠️ [2번 기능] 네이버 로그인이 되어있지 않습니다. [🔑 세션 관리] 탭에서 먼저 로그인해 주세요.", "ERROR")
                    return False, TaskStatus.FAILED

            # 4. 댓글 입력창 활성화 (플레이스홀더 박스 영역 클릭)
            write_area_selectors = [
                "div.u_cbox_write_box",
                "div.u_cbox_write_inner",
                "div.u_cbox_write_area",
                "div.u_cbox_write",
                "textarea.u_cbox_text",
                "textarea.u_cbox_type_text"
            ]

            for sel in write_area_selectors:
                try:
                    area = context.query_selector(sel) or page.query_selector(sel)
                    if area and area.is_visible():
                        area.scroll_into_view_if_needed(timeout=1500)
                        area.click(timeout=1500)
                        interruptible_wait(self.stop_event, 0.5)
                        break
                except Exception:
                    continue

            # 5. 활성화된 textarea에 댓글 입력
            textarea_selectors = [
                "textarea.u_cbox_text",
                "textarea.u_cbox_type_text",
                "textarea[name='comment']",
                "textarea#comment_text",
                "div.u_cbox_write textarea",
                "textarea"
            ]

            textarea = None
            for sel in textarea_selectors:
                try:
                    t = context.query_selector(sel) or page.query_selector(sel)
                    if t and t.is_visible():
                        textarea = t
                        break
                except Exception:
                    continue

            if not textarea:
                logger.log("⚠️ [2번 기능] 댓글 입력창(textarea)을 찾을 수 없습니다. (댓글 비활성화 글이거나 차단됨)", "WARNING")
                return False, TaskStatus.FAILED

            final_text = parse_spintax(comment_template)
            logger.log(f"  💬 작성할 댓글 문구: '{final_text}'")

            textarea.scroll_into_view_if_needed(timeout=1500)
            textarea.click(timeout=1500)
            try:
                textarea.fill(final_text, timeout=2000)
            except Exception:
                textarea.press_sequentially(final_text, delay=50)

            if interruptible_wait(self.stop_event, 0.5):
                return False, TaskStatus.STOPPED

            # 6. 비밀댓글 옵션
            if secret_comment:
                secret_selectors = [
                    "input#secret_chk",
                    "input.u_cbox_secret_chk",
                    "label[for='secret_chk']",
                    "div.u_cbox_secret_tag label",
                    "span.u_cbox_secret_tag"
                ]
                for sel in secret_selectors:
                    try:
                        chk = context.query_selector(sel) or page.query_selector(sel)
                        if chk and chk.is_visible():
                            chk.click(timeout=1500)
                            logger.log("  🔒 비밀댓글 설정 완료")
                            break
                    except Exception:
                        continue

            if self.is_stopped():
                return False, TaskStatus.STOPPED

            # 7. 댓글 등록 버튼 클릭
            submit_selectors = [
                "button.u_cbox_btn_upload",
                "a.u_cbox_btn_upload",
                "button[data-action='upload']",
                ".u_cbox_btn_upload",
                "button.btn_upload"
            ]

            submit_btn = None
            for sel in submit_selectors:
                try:
                    btn = context.query_selector(sel) or page.query_selector(sel)
                    if btn and btn.is_visible():
                        submit_btn = btn
                        break
                except Exception:
                    continue

            if submit_btn:
                submit_btn.scroll_into_view_if_needed(timeout=1500)
                try:
                    submit_btn.click(timeout=1500)
                except Exception:
                    submit_btn.dispatch_event("click")

                # 작성 후 난수 지연 (0~8초)
                delay = random_sleep(self.min_delay, self.max_delay, self.stop_event)
                logger.log(f"  ✅ 댓글 등록 완료! (난수 지연: {delay:.2f}초 적용)")

                self.history.add(blog_url)
                self.history.add(direct_url)
                self._save_history()
                return True, TaskStatus.COMPLETED
            else:
                logger.log("⚠️ [2번 기능] 댓글 등록 버튼을 찾지 못했습니다.", "WARNING")
                return False, TaskStatus.FAILED

        except Exception as e:
            logger.log(f"❌ [2번 기능] 댓글 작성 중 오류: {e}", "ERROR")
            return False, TaskStatus.FAILED

    def _get_main_frame(self, page: Page) -> Optional[Frame]:
        try:
            return page.frame(name="mainFrame")
        except Exception:
            return None
