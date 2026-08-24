import threading
from typing import Optional, List, Tuple
from playwright.sync_api import Page, Frame, ElementHandle
from src.logger import logger
from src.browser import random_sleep, interruptible_wait
from src.types import TaskStatus

LIKE_SELECTORS_PRIMARY = [
    "button.u_likeit_list_module_link",
    "a.u_likeit_list_module_link",
    "div.u_likeit_list_module button",
    "button[data-like-article]",
    "button.u_likeit_button",
    "a.u_likeit_button",
    "span.u_likeit_icon",
    "span.__reaction__zeroface",
    "a._sympathyButton",
    "button._sympathyButton",
    "a.btn_like"
]

LIKE_SELECTORS_FALLBACK = [
    "div.desc_area button[class*='like']",
    "li.list_item button[class*='like']",
    "div.info_post button[class*='like']",
    "div.info_post a[class*='like']"
]


class AutoLiker:
    def __init__(self, min_delay: float = 0.0, max_delay: float = 8.0, stop_event: Optional[threading.Event] = None):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.stop_event = stop_event

    def is_stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def process_url_for_likes(self, page: Page, target_url: str, max_pages: int = 5) -> Tuple[int, TaskStatus]:
        """
        네이버 블로그 섹션/피드/게시글 페이지로 이동하여 위에서 아래로 1회 순차 탐색하며
        공감 버튼을 클릭하고, 완료 후 즉시 다음 페이지로 이동합니다.
        """
        if self.is_stopped():
            return 0, TaskStatus.STOPPED

        current_url = page.url
        if target_url in current_url or (("BlogHome.naver" in target_url) and ("BlogHome.naver" in current_url)):
            logger.log(f"[1번 기능] 현재 열려 있는 탭 화면을 그대로 활용합니다: {current_url}")
        else:
            logger.log(f"[1번 기능] 대상 페이지 이동: {target_url}")
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                logger.log(f"[1번 기능] 페이지 로드 안내: {e}", "WARNING")

        if interruptible_wait(self.stop_event, 1.5):
            return 0, TaskStatus.STOPPED

        total_liked = 0

        for current_page_idx in range(1, max_pages + 1):
            if self.is_stopped():
                logger.log("[1번 기능] 🛑 작업 중지 요청을 감지하여 즉시 탐색을 종료합니다.", "WARNING")
                return total_liked, TaskStatus.STOPPED

            logger.log(f"==========================================")
            logger.log(f"[1번 기능] ▶ {current_page_idx}번째 페이지 공감 탐색 시작")

            frame = self._get_main_frame(page)
            target_context = frame if frame else page

            # 단일 순차 하향 스크롤로 게시물별 1회만 공감 처리
            likes_count = self._scroll_and_click_hearts(page, target_context)
            total_liked += likes_count
            logger.log(f"[1번 기능] {current_page_idx}페이지 완료: {likes_count}개 공감 클릭 (누적: {total_liked}개)")

            if self.is_stopped():
                return total_liked, TaskStatus.STOPPED

            if current_page_idx >= max_pages:
                logger.log(f"[1번 기능] 설정된 최대 탐색 페이지({max_pages}페이지)에 도달하여 작업을 마칩니다.")
                break

            # 다음 페이지 이동
            has_next = self._navigate_next_page(page, target_context, current_page_idx)
            if not has_next:
                logger.log("[1번 기능] 더 이상 이동할 다음 페이지가 없어 작업을 마칩니다.")
                break

            delay = random_sleep(self.min_delay, self.max_delay, self.stop_event)
            if interruptible_wait(self.stop_event, 1.5):
                return total_liked, TaskStatus.STOPPED

            # 새 페이지 상단으로 위치 복귀 및 렌더링 대기
            try:
                page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

            if interruptible_wait(self.stop_event, 1.0):
                return total_liked, TaskStatus.STOPPED

            logger.log(f"[1번 기능] 다음 페이지 로드 완료 (난수 지연 {delay:.2f}초 적용)")

        return total_liked, TaskStatus.COMPLETED

    def _get_main_frame(self, page: Page) -> Optional[Frame]:
        try:
            return page.frame(name="mainFrame")
        except Exception:
            return None

    def _check_already_liked(self, el: ElementHandle) -> bool:
        """
        1. 아이콘에 __reaction__like 포함 여부
        2. 숫자 폰트 색상이 초록색(#03C75A / rgb(3, 199, 90))인지 여부
        3. aria-pressed="true" 또는 _on 클래스 여부
        """
        try:
            is_liked = el.evaluate("""
                el => {
                    const outer = el.outerHTML || '';
                    const inner = el.innerHTML || '';
                    if (outer.includes('__reaction__like') || inner.includes('__reaction__like')) return true;

                    if (el.classList.contains('_on') || el.classList.contains('on') || el.classList.contains('active')) return true;
                    if (el.getAttribute('aria-pressed') === 'true') return true;

                    const parent = el.parentElement;
                    if (parent && (parent.classList.contains('_on') || parent.classList.contains('on'))) return true;

                    const icon = el.querySelector('.u_likeit_icon, [class*="__reaction__"]');
                    if (icon && (icon.className.includes('__reaction__like') || icon.className.includes('_on'))) return true;

                    const countEl = el.querySelector('._count, .u_likeit_text, .num');
                    if (countEl) {
                        const style = window.getComputedStyle(countEl);
                        const color = style.color || '';
                        if (color.includes('3, 199, 90') || color.includes('03C75A') || color.includes('03c75a')) return true;
                    }

                    if (outer.includes('__reaction__zeroface') || inner.includes('__reaction__zeroface')) return false;

                    return false;
                }
            """)
            return bool(is_liked)
        except Exception:
            return False

    def _scroll_and_click_hearts(self, page: Page, context) -> int:
        """
        현재 페이지의 포스트 카드들을 위에서 아래로 순서대로 딱 1번씩만 스크롤하며 공감 클릭
        (중복 반복 탐색 및 위아래 흔들림 원천 제거)
        """
        liked_count = 0
        already_liked_count = 0
        failed_count = 0

        # 1. 페이지를 아래로 부드럽게 1회 내려서 동적 포스트들을 모두 DOM에 로드
        try:
            for s in range(1, 6):
                page.evaluate(f"window.scrollTo(0, {s * 600})")
                if interruptible_wait(self.stop_event, 0.3):
                    return 0
        except Exception:
            pass

        # 2. 현재 페이지에 있는 모든 공감 버튼 요소들을 단 1회만 수집
        buttons: List[ElementHandle] = []
        for sel in LIKE_SELECTORS_PRIMARY:
            try:
                found = context.query_selector_all(sel)
                if found:
                    buttons.extend(found)
            except Exception:
                continue

        if not buttons:
            for sel in LIKE_SELECTORS_FALLBACK:
                try:
                    found = context.query_selector_all(sel)
                    if found:
                        buttons.extend(found)
                except Exception:
                    continue

        # 중복 엘리먼트 핸들 필터링
        unique_buttons = []
        seen_keys = set()
        for b in buttons:
            try:
                # 요소의 Y 좌표 위치를 기준으로 고유 키 생성
                box = b.bounding_box()
                pos_key = round(box['y']) if box else id(b)
                if pos_key not in seen_keys:
                    seen_keys.add(pos_key)
                    unique_buttons.append(b)
            except Exception:
                unique_buttons.append(b)

        total_found = len(unique_buttons)
        visible_count = 0

        logger.log(f"  📋 현재 페이지에서 총 {total_found}개의 공감 대상을 발견했습니다. 순차 처리 시작...")

        # 3. 위에서부터 아래로 1개씩 순차 처리
        for idx, btn in enumerate(unique_buttons, 1):
            if self.is_stopped():
                break

            try:
                if not btn.is_visible():
                    btn.scroll_into_view_if_needed(timeout=1000)
                    if not btn.is_visible():
                        continue

                visible_count += 1

                # 기 공감(눌린 하트) 여부 판별
                if self._check_already_liked(btn):
                    already_liked_count += 1
                    continue

                btn.scroll_into_view_if_needed(timeout=1000)
                if interruptible_wait(self.stop_event, 0.1):
                    break

                click_success = False
                try:
                    btn.click(timeout=1500)
                    click_success = True
                except Exception:
                    try:
                        btn.click(force=True, timeout=1500)
                        click_success = True
                    except Exception:
                        try:
                            btn.dispatch_event("click")
                            click_success = True
                        except Exception as e_disp:
                            failed_count += 1
                            logger.log(f"  ⚠️ [{idx}/{total_found}] 하트 클릭 실패: {e_disp}", "WARNING")

                if click_success:
                    liked_count += 1
                    delay = random_sleep(self.min_delay, self.max_delay, self.stop_event)
                    logger.log(f"  ❤️ [{idx}/{total_found}] 공감 클릭 성공! (난수 지연: {delay:.2f}초)")

            except Exception:
                continue

        logger.log(
            f"🔍 [공감 진단] 총 포스트: {total_found}개 | 표시됨: {visible_count}개 | "
            f"이미 눌림(제외): {already_liked_count}개 | 신규 클릭 성공: {liked_count}개 | 실패: {failed_count}개"
        )

        return liked_count

    def _navigate_next_page(self, page: Page, context, current_page_idx: int) -> bool:
        if self.is_stopped():
            return False

        next_page_num = current_page_idx + 1
        logger.log(f"  ⏩ {next_page_num}페이지로 이동 시도...")

        # 최하단으로 스크롤하여 페이징 바 노출
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

        if interruptible_wait(self.stop_event, 0.8):
            return False

        # 1. 숫자 페이지 버튼 탐색
        page_num_selectors = [
            f"a[aria-label='{next_page_num}페이지']",
            f"a.item[aria-label='{next_page_num}페이지']",
            f"a[ng-click*='loadPage({next_page_num})']",
            f".pagination a:has-text('{next_page_num}')",
            f"a.item:has-text('{next_page_num}')",
            f"a:text-is('{next_page_num}')"
        ]

        for sel in page_num_selectors:
            if self.is_stopped():
                return False
            try:
                btn = context.query_selector(sel) or page.query_selector(sel)
                if btn and btn.is_visible():
                    logger.log(f"  ▶ {next_page_num}페이지 버튼 발견, 클릭합니다.")
                    btn.scroll_into_view_if_needed(timeout=1500)
                    btn.click(timeout=1500)
                    return True
            except Exception:
                continue

        # JS 직접 순회 탐색 (Angular pagination 처리)
        try:
            clicked_js = page.evaluate(f"""
                nextNum => {{
                    const items = document.querySelectorAll('.pagination a, a[ng-click*="paginationCtrl"], a.item');
                    for (const a of items) {{
                        const txt = a.textContent.trim();
                        const aria = a.getAttribute('aria-label') || '';
                        const ng = a.getAttribute('ng-click') || '';
                        if (txt === String(nextNum) || aria.includes(nextNum + '페이지') || ng.includes('loadPage(' + nextNum + ')')) {{
                            a.scrollIntoView();
                            a.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """, next_page_num)
            if clicked_js:
                logger.log(f"  ▶ JS로 {next_page_num}페이지 버튼 클릭 완료.")
                return True
        except Exception:
            pass

        # 2. 다음 그룹(button_next / goNextGroup) 버튼 탐색
        next_group_selectors = [
            "a.button_next",
            "a[ng-click*='goNextGroup()']",
            "a.button_next:has(i.icon_arrow_right)",
            "a.button_next:has(i.sp_common)",
            ".pagination a.next",
            "a.btn_next"
        ]

        for sel in next_group_selectors:
            if self.is_stopped():
                return False
            try:
                btn = context.query_selector(sel) or page.query_selector(sel)
                if btn and btn.is_visible():
                    logger.log("  ⏩ '다음 그룹' 버튼(goNextGroup) 클릭 중...")
                    btn.scroll_into_view_if_needed(timeout=1500)
                    btn.click(timeout=1500)
                    return True
            except Exception:
                continue

        return False
