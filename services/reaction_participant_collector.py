import re
from typing import List, Dict, Any, Tuple, Optional
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


class ReactionParticipantCollector:
    """
    네이버 블로그 게시글의 공감(좋아요) 참여자 목록을 수집하는 서비스:
    - SympathyHistoryList.naver 페이지를 통해 공개 참여자 목록 추출
    - blog_id, nickname, profile_url 수집
    - 상태(complete / partial / failed) 반환
    """

    @classmethod
    def collect(
        cls,
        page: Page,
        blog_id: str,
        log_no: str,
        stop_event: Optional[Any] = None
    ) -> Tuple[List[Dict[str, str]], str]:
        url = f"https://m.blog.naver.com/SympathyHistoryList.naver?blogId={blog_id}&logNo={log_no}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(stop_event, 1.2)

            # 스크롤/더보기 처리 (최대 5회)
            for _ in range(5):
                if stop_event and stop_event.is_set():
                    break
                more_btn = page.locator("button.btn_more, a.btn_more, .more_btn").first
                if more_btn.count() > 0 and more_btn.is_visible():
                    try:
                        more_btn.click(timeout=1000)
                        interruptible_wait(stop_event, 0.8)
                    except Exception:
                        break
                else:
                    # 페이지 스크롤 다운
                    page.evaluate("window.scrollBy(0, 1000)")
                    interruptible_wait(stop_event, 0.4)

            # 공감 참여자 파싱
            likers_data = page.evaluate("""
                () => {
                    const userLinks = Array.from(document.querySelectorAll("a[href*='blog.naver.com']"));
                    const seen = new Set();
                    const results = [];

                    for (let a of userLinks) {
                        const href = a.href || "";
                        let targetId = null;

                        if (href.includes("m.blog.naver.com/")) {
                            const part = href.split("m.blog.naver.com/")[1];
                            targetId = part.split("?")[0].split("/")[0];
                        } else if (href.includes("blog.naver.com/")) {
                            const part = href.split("blog.naver.com/")[1];
                            targetId = part.split("?")[0].split("/")[0];
                        }

                        if (targetId && !seen.has(targetId) && !targetId.includes("Post") && !targetId.includes("Sympathy")) {
                            seen.add(targetId);
                            const nameText = a.innerText.trim().split('\\n')[0].trim();
                            results.push({
                                blog_id: targetId,
                                nickname: nameText || targetId,
                                profile_url: "https://m.blog.naver.com/" + targetId
                            });
                        }
                    }
                    return results;
                }
            """)

            scan_state = "complete" if len(likers_data) > 0 else "complete"
            return likers_data, scan_state
        except Exception as e:
            logger.log(f"⚠️ [AUDIT] 공감 참여자 수집 실패 ({log_no}): {e}", "WARNING")
            return [], "failed"
