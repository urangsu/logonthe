import re
from typing import List, Dict, Any, Optional
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


class MyBlogRecentPostService:
    """
    내 네이버 블로그의 최근 공개 포스트 목록(최대 N개)을 수집하는 서비스
    """

    @classmethod
    def fetch_recent_posts(
        cls,
        page: Page,
        blog_id: str,
        max_count: int = 5,
        stop_event: Optional[Any] = None
    ) -> List[Dict[str, str]]:
        if not blog_id or not blog_id.strip():
            logger.log("⚠️ [AUDIT] 블로그 ID가 지정되지 않았습니다.", "WARNING")
            return []

        b_id = blog_id.strip()
        url = f"https://m.blog.naver.com/PostList.naver?blogId={b_id}"
        logger.log(f"🔎 [AUDIT] 내 블로그 최근 글 목록 조회 중: {url} (최대 {max_count}개)")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 1.5)

            # 모바일 PostList 페이지에서 글 링크 및 제목 추출
            posts = page.evaluate(r"""
                (arg) => {
                    const blogId = arg.blogId;
                    const maxCnt = arg.maxCount;
                    const links = Array.from(document.querySelectorAll("a[href*='PostView.naver'], a[href*='/" + blogId + "/']"));
                    const items = [];
                    const seenLogNo = new Set();

                    for (let a of links) {
                        const href = a.href;
                        let logNo = null;
                        if (href.includes("logNo=")) {
                            logNo = href.split("logNo=")[1].split("&")[0];
                        } else if (href.includes("/" + blogId + "/")) {
                            logNo = href.split("/" + blogId + "/")[1].split("?")[0];
                        }

                        if (logNo && !seenLogNo.has(logNo) && /^\d+$/.test(logNo)) {
                            seenLogNo.add(logNo);
                            const titleEl = a.querySelector(".title, strong, .tit, h3, .post_title") || a;
                            const tText = (titleEl ? titleEl.innerText : '').trim().replace(/\n/g, ' ');
                            items.push({
                                log_no: logNo,
                                url: "https://m.blog.naver.com/" + blogId + "/" + logNo,
                                title: tText || ("포스트 " + logNo)
                            });
                            if (items.length >= maxCnt) {
                                break;
                            }
                        }
                    }
                    return items;
                }
            """, {"blogId": b_id, "maxCount": max_count})

            logger.log(f"✅ [AUDIT] 최근 글 {len(posts)}개 확보 완료")
            for p in posts:
                logger.log(f"   📄 [{p['log_no']}] {p['title'][:35]}...")
            return posts
        except Exception as e:
            logger.log(f"❌ [AUDIT] 최근 글 목록 조회 실패: {e}", "ERROR")
            return []
