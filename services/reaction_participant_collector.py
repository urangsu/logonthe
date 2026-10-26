import re
from typing import List, Dict, Any, Tuple, Optional, Literal
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


class ReactionParticipantCollector:
    """
    네이버 블로그 게시글의 공감(좋아요) 참여자 목록을 수집하는 서비스 (v8.0):
    - SympathyHistoryList.naver 페이지를 통해 공개 참여자 목록 추출
    - 표기된 총 공감수와 수집수를 비교하여 COMPLETE / PARTIAL 판정
    """

    @classmethod
    def collect(
        cls,
        page: Page,
        blog_id: str,
        log_no: str,
        stop_event: Optional[Any] = None
    ) -> Tuple[List[Dict[str, str]], Literal["complete", "partial", "failed"], Optional[int]]:
        url = f"https://m.blog.naver.com/SympathyHistoryList.naver?blogId={blog_id}&logNo={log_no}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(stop_event, 1.2)

            # 스크롤/더보기 처리 (최대 10회)
            for _ in range(10):
                if stop_event and stop_event.is_set():
                    break
                more_btn = page.locator("button.btn_more, a.btn_more, .more_btn, .u_likeit_list_btn_more").first
                if more_btn.count() > 0 and more_btn.is_visible():
                    try:
                        more_btn.click(timeout=1000)
                        interruptible_wait(stop_event, 0.8)
                    except Exception:
                        break
                else:
                    page.evaluate("window.scrollBy(0, 1500)")
                    interruptible_wait(stop_event, 0.4)

            # 공감 참여자 및 표시 총수 파싱
            res_data = page.evaluate("""
                () => {
                    const countEl = document.querySelector(".count, .num, em.point, strong.point, .u_likeit_list_count");
                    let displayedCount = null;
                    if (countEl) {
                        const m = countEl.innerText.replace(/,/g, '').match(/\\d+/);
                        if (m) displayedCount = parseInt(m[0], 10);
                    }

                    const userLinks = Array.from(document.querySelectorAll("ul.list_sympathy a[href*='blog.naver.com'], .user_list a[href*='blog.naver.com'], a[href*='trackingCode=blog_sympathy'], a[href*='blog.naver.com']"));
                    const seen = new Set();
                    const results = [];

                    for (let a of userLinks) {
                        const href = a.href || "";
                        let targetId = null;

                        try {
                            const u = new URL(href, location.href);
                            if (['http:', 'https:'].includes(u.protocol) && ['blog.naver.com', 'm.blog.naver.com'].includes(u.hostname)) {
                                const candidate = u.searchParams.get('blogId') || (u.pathname === '/GoBlog.naver' ? u.searchParams.get('userId') : u.pathname.split('/').filter(Boolean)[0]);
                                if (/^[a-zA-Z0-9_-]{1,50}$/.test(candidate || '')) targetId = candidate.toLowerCase();
                            }
                        } catch (_) {}

                        if (targetId && !seen.has(targetId) && !targetId.includes("Post") && !targetId.includes("Sympathy") && !targetId.includes("Buddy")) {
                            seen.add(targetId);
                            const nameText = a.innerText.trim().split('\\n')[0].trim();
                            results.push({
                                blog_id: targetId,
                                nickname: nameText || targetId,
                                profile_url: "https://m.blog.naver.com/" + targetId
                            });
                        }
                    }
                    const moreEl = document.querySelector("button.btn_more, a.btn_more, .more_btn, .u_likeit_list_btn_more");
                    const moreVisible = !!(moreEl && moreEl.offsetParent !== null);
                    return {
                        displayedCount: displayedCount,
                        moreVisible: moreVisible,
                        items: results
                    };
                }
            """)

            likers_data = res_data.get("items", [])
            displayed_cnt = res_data.get("displayedCount")

            state: Literal["complete", "partial", "failed"] = "complete"
            if stop_event and stop_event.is_set():
                state = "partial"
            elif displayed_cnt is None:
                # 표시 수를 모를 때는 더보기가 없고 1명 이상 로드된 경우에만 전체로 본다 (0명은 확정 금지)
                if res_data.get("moreVisible") or not likers_data:
                    state = "partial"
            elif len(likers_data) < displayed_cnt:
                state = "partial"

            return likers_data, state, displayed_cnt
        except Exception as e:
            logger.log(f"⚠️ [AUDIT] 공감 참여자 수집 실패 ({log_no}): {e}", "WARNING")
            return [], "failed", None
