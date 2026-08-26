import re
from typing import List, Dict, Any, Tuple, Optional
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


class CommentParticipantCollector:
    """
    네이버 블로그 게시글의 댓글 작성자 목록을 수집하는 서비스:
    - PostView.naver 페이지에서 댓글 레이어 열기
    - 작성자 blog_id, nickname, profile_url, comment_sample 수집
    - 내 댓글(mine:true / u_cbox_type_mine)은 수집 제외
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
        url = f"https://m.blog.naver.com/{blog_id}/{log_no}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(stop_event, 1.2)

            # 댓글 버튼 클릭
            cmt_btn = page.locator("button[data-click-area*='.re'], button.Interact__comment_btn--Wbuoq, button:has(.blind:text-is('댓글'))").first
            if cmt_btn.count() > 0:
                try:
                    cmt_btn.click(timeout=1500)
                    interruptible_wait(stop_event, 1.2)
                except Exception:
                    pass

            # 댓글 더보기 / 스크롤
            for _ in range(5):
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
            commenters = page.evaluate("""
                () => {
                    const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                    const seen = new Set();
                    const results = [];

                    for (let el of items) {
                        // 내 댓글 제외
                        const dataInfo = el.getAttribute("data-info") || "";
                        if (el.className.includes("type_mine") || /(?:^|[,{\\s])mine\\s*:\\s*true(?:[,}\\s]|$)/.test(dataInfo)) {
                            continue;
                        }

                        const userLink = el.querySelector(".u_cbox_info a, a.u_cbox_name, .u_cbox_nick a, a[href*='blog.naver.com']");
                        const nickEl = el.querySelector(".u_cbox_nick, .u_cbox_name");
                        const textEl = el.querySelector(".u_cbox_contents, .u_cbox_text_mention, p.text");

                        let targetId = null;
                        let profileUrl = "";
                        if (userLink) {
                            const href = userLink.href || "";
                            if (href.includes("m.blog.naver.com/")) {
                                targetId = href.split("m.blog.naver.com/")[1].split("?")[0].split("/")[0];
                            } else if (href.includes("blog.naver.com/")) {
                                targetId = href.split("blog.naver.com/")[1].split("?")[0].split("/")[0];
                            }
                            profileUrl = href;
                        }

                        const nick = (nickEl ? nickEl.innerText : (targetId || '')).trim();
                        const commentText = (textEl ? textEl.innerText : '').trim().replace(/\\n/g, ' ');

                        if (targetId && !seen.has(targetId) && !targetId.includes("Post") && !targetId.includes("Sympathy")) {
                            seen.add(targetId);
                            results.push({
                                blog_id: targetId,
                                nickname: nick,
                                profile_url: profileUrl || ("https://m.blog.naver.com/" + targetId),
                                comment_sample: commentText.substring(0, 100)
                            });
                        }
                    }
                    return results;
                }
            """)

            return commenters, "complete"
        except Exception as e:
            logger.log(f"⚠️ [AUDIT] 댓글 작성자 수집 실패 ({log_no}): {e}", "WARNING")
            return [], "failed"
