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
                        const m = countEl.innerText.match(/\\d+/);
                        if (m) displayedCount = parseInt(m[0], 10);
                    }

                    const userLinks = Array.from(document.querySelectorAll("ul.list_sympathy a[href*='blog.naver.com'], .user_list a[href*='blog.naver.com'], a[href*='trackingCode=blog_sympathy'], a[href*='blog.naver.com']"));
                    const seen = new Set();
                    const results = [];

                    for (let a of userLinks) {
                        const href = a.href || "";
                        let targetId = null;

                        if (href.includes("blogId=")) {
                            const m = href.match(/blogId=([a-zA-Z0-9_-]+)/);
                            if (m) targetId = m[1];
                        } else if (href.includes("m.blog.naver.com/")) {
                            const part = href.split("m.blog.naver.com/")[1];
                            targetId = part.split("?")[0].split("/")[0];
                        } else if (href.includes("blog.naver.com/")) {
                            const part = href.split("blog.naver.com/")[1];
                            targetId = part.split("?")[0].split("/")[0];
                        }

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
                    return {
                        displayedCount: displayedCount,
                        items: results
                    };
                }
            """)

            likers_data = res_data.get("items", [])
            displayed_cnt = res_data.get("displayedCount")

            state: Literal["complete", "partial", "failed"] = "complete"
            if displayed_cnt is not None and len(likers_data) < displayed_cnt:
                state = "partial"

            return likers_data, state, displayed_cnt
        except Exception as e:
            logger.log(f"⚠️ [AUDIT] 공감 참여자 수집 실패 ({log_no}): {e}", "WARNING")
            return [], "failed", None
