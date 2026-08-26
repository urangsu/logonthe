import re
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


@dataclass
class BuddyInfo:
    blog_id: str
    nickname: str
    blog_title: str
    group_name: str
    buddy_type: str        # "서로이웃" | "이웃"
    last_post_date: Optional[str]
    added_date: str


class BuddyListCollector:
    """
    네이버 블로그 관리자 페이지(BuddyListManage.naver)를 순회하여
    전체 이웃 목록(194명 등 전수)을 수집하는 서비스
    """

    @classmethod
    def collect_all_buddies(
        cls,
        page: Page,
        blog_id: str,
        stop_event: Optional[Any] = None
    ) -> Dict[str, BuddyInfo]:
        if not blog_id or not blog_id.strip():
            return {}

        b_id = blog_id.strip()
        logger.log(f"📋 [BUDDY] '{b_id}' 블로그의 전체 이웃 목록 전수 수집 시작...")

        buddies: Dict[str, BuddyInfo] = {}
        curr_page = 1
        total_target_count = None

        while True:
            if stop_event and stop_event.is_set():
                break

            url = f"https://admin.blog.naver.com/BuddyListManage.naver?blogId={b_id}&page={curr_page}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                interruptible_wait(stop_event, 1.2)

                frame = page.frame("papermain") or page.main_frame

                parsed_data = frame.evaluate(r"""
                    () => {
                        const totalEl = document.querySelector(".total, em.point, strong.point, span.num, .align_r em");
                        const totalText = totalEl ? totalEl.innerText.trim().replace(/,/g, '') : null;

                        const rows = Array.from(document.querySelectorAll("table.table_style tbody tr, tbody tr, tr._buddyRow"));
                        const items = [];

                        for (let r of rows) {
                            const chk = r.querySelector("input[name='buddySeq'], input[type='checkbox']");
                            const userLink = r.querySelector("a[href*='blog.naver.com']");
                            if (!userLink && !chk) continue;

                            const rawText = r.innerText.replace(/\s+/g, ' ').trim();
                            const href = userLink ? userLink.href : '';

                            // 1. blog_id 추출
                            let targetId = null;
                            if (href.includes("blog.naver.com/")) {
                                const part = href.split("blog.naver.com/")[1];
                                targetId = part.split("?")[0].split("/")[0];
                            } else if (chk && chk.value) {
                                targetId = chk.value;
                            }

                            if (!targetId || targetId === 'PostList.naver' || targetId === 'BuddyListManage.naver') {
                                continue;
                            }

                            // 2. 그룹명, 이웃구분, 닉네임, 블로그명 파싱
                            // rawText 예: '사기꾼 서로이웃 포레니티|뽈뽈거리는엔티제의블로그 ON 26.08.25. 26.08.26.'
                            // 또는 td 셀 파싱
                            const tds = Array.from(r.querySelectorAll("td"));
                            let groupName = "";
                            let buddyType = "이웃";
                            let nick = "";
                            let bTitle = "";
                            let addedDate = "";
                            let lastPostDate = "";

                            if (tds.length >= 5) {
                                groupName = tds[1].innerText.trim();
                                buddyType = tds[2].innerText.trim().includes("서로이웃") ? "서로이웃" : "이웃";
                                
                                const nameCell = tds[3].innerText.trim();
                                if (nameCell.includes("|")) {
                                    const parts = nameCell.split("|");
                                    nick = parts[0].trim();
                                    bTitle = parts[1].trim();
                                } else {
                                    nick = nameCell;
                                }

                                // 날짜 추출 (YY.MM.DD)
                                const dates = Array.from(r.querySelectorAll("td")).map(t => t.innerText.trim()).filter(t => /\d{2}\.\d{2}\.\d{2}/.test(t));
                                if (dates.length >= 2) {
                                    addedDate = dates[0];
                                    lastPostDate = dates[1];
                                } else if (dates.length === 1) {
                                    addedDate = dates[0];
                                }
                            } else {
                                // fallback text regex
                                if (rawText.includes("서로이웃")) buddyType = "서로이웃";
                                const mDates = rawText.match(/\d{2}\.\d{2}\.\d{2}\.?/g) || [];
                                if (mDates.length >= 2) {
                                    addedDate = mDates[0].replace(/\.$/, '');
                                    lastPostDate = mDates[1].replace(/\.$/, '');
                                } else if (mDates.length === 1) {
                                    addedDate = mDates[0].replace(/\.$/, '');
                                }
                                nick = userLink ? userLink.innerText.trim() : targetId;
                            }

                            items.push({
                                blog_id: targetId,
                                nickname: nick || targetId,
                                blog_title: bTitle,
                                group_name: groupName,
                                buddy_type: buddyType,
                                added_date: addedDate,
                                last_post_date: lastPostDate
                            });
                        }

                        return {
                            totalCount: totalText ? parseInt(totalText, 10) : null,
                            items: items
                        };
                    }
                """)

                if total_target_count is None and parsed_data.get("totalCount"):
                    total_target_count = parsed_data["totalCount"]
                    logger.log(f"📊 [BUDDY] 확인된 총 이웃 수: {total_target_count}명")

                page_items = parsed_data.get("items", [])
                if not page_items:
                    break

                new_in_page = 0
                for item in page_items:
                    t_id = item["blog_id"]
                    if t_id not in buddies:
                        buddies[t_id] = BuddyInfo(
                            blog_id=t_id,
                            nickname=item["nickname"],
                            blog_title=item["blog_title"],
                            group_name=item["group_name"],
                            buddy_type=item["buddy_type"],
                            last_post_date=item["last_post_date"] or None,
                            added_date=item["added_date"]
                        )
                        new_in_page += 1

                logger.log(f"   📄 [Page {curr_page}] {len(page_items)}명 수집 완료 (누적 {len(buddies)}명)")

                if new_in_page == 0:
                    break

                if total_target_count and len(buddies) >= total_target_count:
                    break

                curr_page += 1
                interruptible_wait(stop_event, 0.5)

            except Exception as e:
                logger.log(f"⚠️ [BUDDY] 이웃 목록 수집 중 오류 ({curr_page}페이지): {e}", "WARNING")
                break

        logger.log(f"✅ [BUDDY] 이웃 목록 전수 수집 완료! (총 {len(buddies)}명)")
        return buddies


class Any:
    pass
