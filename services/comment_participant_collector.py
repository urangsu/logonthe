import re
from typing import List, Dict, Any, Tuple, Optional, Literal
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


class CommentParticipantCollector:
    """
    네이버 블로그 게시글의 댓글 작성자 목록을 수집하는 서비스 (v8.0):
    - PostView.naver 페이지에서 댓글 레이어 열기
    - 작성자 blog_id, nickname, 작성 댓글 수(entries) 수집 (댓글 본문은 프라이버시 보호로 제외)
    - 내 댓글(mine:true / u_cbox_type_mine)은 제외
    - 표기된 총 댓글수와 비교하여 COMPLETE / PARTIAL 판정
    """

    @classmethod
    def collect(
        cls,
        page: Page,
        blog_id: str,
        log_no: str,
        stop_event: Optional[Any] = None
    ) -> Tuple[List[Dict[str, Any]], Literal["complete", "partial", "failed"], Optional[int]]:
        url = f"https://m.blog.naver.com/{blog_id}/{log_no}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(stop_event, 1.2)

            # 댓글 버튼 클릭하여 레이어 오픈
            cmt_btn = page.locator("button[data-click-area*='.re'], button.Interact__comment_btn--Wbuoq, button:has(.blind:text-is('댓글'))").first
            if cmt_btn.count() > 0:
                try:
                    cmt_btn.click(timeout=1500)
                    interruptible_wait(stop_event, 1.2)
                except Exception:
                    pass

            # 댓글 더보기 / 스크롤
            for _ in range(10):
                if stop_event and stop_event.is_set():
                    break
                more_btn = page.locator("a.u_cbox_btn_more, button.u_cbox_btn_more").first
                if more_btn.count() > 0 and more_btn.is_visible():
                    try:
                        more_btn.click(timeout=1000)
                        interruptible_wait(stop_event, 0.8)
                    except Exception:
                        break
                else:
                    break

            # 댓글 작성자 파싱
            res_data = page.evaluate("""
                () => {
                    const countEl = document.querySelector(".u_cbox_count, .count, em.point");
                    let displayedCount = null;
                    if (countEl) {
                        const m = countEl.innerText.match(/\\d+/);
                        if (m) displayedCount = parseInt(m[0], 10);
                    }

                    const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                    const userMap = {};

                    for (let el of items) {
                        // 내 댓글 제외
                        const dataInfo = el.getAttribute("data-info") || "";
                        if (el.className.includes("type_mine") || /(?:^|[,{\\s])mine\\s*:\\s*true(?:[,}\\s]|$)/.test(dataInfo)) {
                            continue;
                        }

                        const userLink = el.querySelector(".u_cbox_info a, a.u_cbox_name, .u_cbox_nick a, a[href*='blog.naver.com']");
                        const nickEl = el.querySelector(".u_cbox_nick, .u_cbox_name");

                        let targetId = null;
                        let profileUrl = "";
                        if (userLink) {
                            const href = userLink.href || "";
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
                            profileUrl = targetId ? ("https://m.blog.naver.com/" + targetId) : href;
                        }

                        const nick = (nickEl ? nickEl.innerText : (targetId || '')).trim();

                        if (targetId && !targetId.includes("PostList") && !targetId.includes("PostView") && !targetId.includes("Sympathy")) {
                            if (!userMap[targetId]) {
                                userMap[targetId] = {
                                    blog_id: targetId,
                                    nickname: nick,
                                    profile_url: profileUrl || ("https://m.blog.naver.com/" + targetId),
                                    comment_entry_count: 0
                                };
                            }
                            userMap[targetId].comment_entry_count += 1;
                        }
                    }

                    return {
                        displayedCount: displayedCount,
                        totalLoadedEntries: items.length,
                        items: Object.values(userMap)
                    };
                }
            """)

            commenters = res_data.get("items", [])
            displayed_cnt = res_data.get("displayedCount")
            total_loaded = res_data.get("totalLoadedEntries", 0)

            state: Literal["complete", "partial", "failed"] = "complete"
            if displayed_cnt is not None and total_loaded < displayed_cnt:
                state = "partial"

            return commenters, state, displayed_cnt
        except Exception as e:
            logger.log(f"⚠️ [AUDIT] 댓글 작성자 수집 실패 ({log_no}): {e}", "WARNING")
            return [], "failed", None
