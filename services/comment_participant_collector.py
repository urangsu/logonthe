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
                    let displayedCount = null;
                    const countSelectors = [".u_cbox_count", ".u_cbox_comment_count", "[class*='cbox_count']", "[class*='comment_count']", "button[data-click-area*='.re']", "button.Interact__comment_btn--Wbuoq"];
                    for (const sel of countSelectors) {
                        const el0 = document.querySelector(sel);
                        if (!el0) continue;
                        const m = (el0.innerText || "").replace(/,/g, '').match(/\\d+/);
                        if (m) { displayedCount = parseInt(m[0], 10); break; }
                    }
                    const moreEl = document.querySelector("a.u_cbox_btn_more, button.u_cbox_btn_more");
                    const moreVisible = !!(moreEl && moreEl.offsetParent !== null);

                    const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                    const userMap = {};
                    let unresolvedEntries = 0;

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
                            try {
                                const u = new URL(href, location.href);
                                if (['http:', 'https:'].includes(u.protocol) && ['blog.naver.com', 'm.blog.naver.com'].includes(u.hostname)) {
                                    const candidate = u.searchParams.get('blogId') || (u.pathname === '/GoBlog.naver' ? u.searchParams.get('userId') : u.pathname.split('/').filter(Boolean)[0]);
                                    if (/^[a-zA-Z0-9_-]{1,50}$/.test(candidate || '')) targetId = candidate.toLowerCase();
                                }
                            } catch (_) {}
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
                        } else {
                            unresolvedEntries += 1;
                        }
                    }

                    return {
                        displayedCount: displayedCount,
                        moreVisible: moreVisible,
                        totalLoadedEntries: items.length,
                        unresolvedEntries: unresolvedEntries,
                        items: Object.values(userMap)
                    };
                }
            """)

            commenters = res_data.get("items", [])
            displayed_cnt = res_data.get("displayedCount")
            total_loaded = res_data.get("totalLoadedEntries", 0)

            state: Literal["complete", "partial", "failed"] = "complete"
            reasons = []
            if displayed_cnt is None:
                # 표시 댓글 수를 읽지 못해도 더보기 버튼이 더 없으면 로드된 목록이 전체임
                if res_data.get("moreVisible") or not total_loaded:
                    reasons.append("displayed_count_unavailable")
            elif total_loaded < displayed_cnt:
                reasons.append("entries_not_fully_loaded")
            if res_data.get("unresolvedEntries", 0):
                reasons.append("author_identity_unresolved")
            if stop_event and stop_event.is_set():
                reasons.append("stop_requested")
            if reasons:
                state = "partial"
                logger.log(
                    f"[AUDIT][COMMENT_INCOMPLETE] post={blog_id}:{log_no} "
                    f"reasons={','.join(reasons)} displayed={displayed_cnt} loaded={total_loaded} "
                    f"unresolved={res_data.get('unresolvedEntries', 0)}", "WARNING"
                )

            return commenters, state, displayed_cnt
        except Exception as e:
            logger.log(f"⚠️ [AUDIT] 댓글 작성자 수집 실패 ({log_no}): {e}", "WARNING")
            return [], "failed", None
