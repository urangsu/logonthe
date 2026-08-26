import re
from typing import Any, Dict, List, Optional, Tuple, Literal
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


@dataclass
class BuddyCollectionResult:
    buddies: Dict[str, BuddyInfo]
    state: Literal["complete", "partial", "failed"]
    expected_total: Optional[int]
    collected_total: int
    pages_visited: int
    page_fingerprints: List[str]
    error: Optional[str] = None


class BuddyListCollector:
    """
    네이버 블로그 관리자 페이지(BuddyListManage.naver)를 순회하여
    전체 이웃 목록(194명 등 전수)을 수집하는 서비스 (v8.0)
    - Frame 내 goPage(N) 페이지네이션 및 DOM 검증
    - 페이지별 지문(Fingerprint) 추적으로 중복/루프 방어
    - Column-index 헤더 매핑으로 견고한 파싱
    - COMPLETE / PARTIAL / FAILED 3-state 보증
    """

    @classmethod
    def collect_all_buddies(
        cls,
        page: Page,
        blog_id: str,
        stop_event: Optional[Any] = None
    ) -> BuddyCollectionResult:
        if not blog_id or not blog_id.strip():
            return BuddyCollectionResult(
                buddies={},
                state="failed",
                expected_total=None,
                collected_total=0,
                pages_visited=0,
                page_fingerprints=[],
                error="blog_id_empty"
            )

        b_id = blog_id.strip()
        logger.log(f"📋 [BUDDY] '{b_id}' 블로그의 전체 이웃 목록 전수 수집 시작...")

        url = f"https://admin.blog.naver.com/BuddyListManage.naver?blogId={b_id}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 1.5)

            frame = page.frame("papermain") or page.main_frame

            all_buddies: Dict[str, BuddyInfo] = {}
            page_no = 1
            page_fingerprints: List[str] = []
            expected_total: Optional[int] = None

            while True:
                if stop_event and stop_event.is_set():
                    return BuddyCollectionResult(
                        buddies=all_buddies,
                        state="partial",
                        expected_total=expected_total,
                        collected_total=len(all_buddies),
                        pages_visited=page_no,
                        page_fingerprints=page_fingerprints,
                        error="stop_requested"
                    )

                # 현재 페이지 데이터 파싱
                page_data = frame.evaluate(r"""
                    () => {
                        const totalEl = document.querySelector(".total, em.point, strong.point, .align_r em");
                        let expected = null;
                        if (totalEl) {
                            const numM = totalEl.innerText.replace(/,/g, '').match(/\d+/);
                            if (numM) expected = parseInt(numM[0], 10);
                        }

                        // 헤더 인덱스 매핑
                        const ths = Array.from(document.querySelectorAll("table.table_style th, table th")).map(th => th.innerText.trim());
                        let groupIdx = 1, typeIdx = 2, nameIdx = 3, addDateIdx = -1, lastDateIdx = -1;

                        ths.forEach((txt, idx) => {
                            if (txt.includes("그룹")) groupIdx = idx;
                            else if (txt.includes("이웃구분") || txt.includes("구분")) typeIdx = idx;
                            else if (txt.includes("이웃") && (txt.includes("블로그") || txt.includes("이름"))) nameIdx = idx;
                            else if (txt.includes("추가일")) addDateIdx = idx;
                            else if (txt.includes("최근") || txt.includes("작성일")) lastDateIdx = idx;
                        });

                        const rows = Array.from(document.querySelectorAll("table.table_style tbody tr, tbody tr")).filter(r => {
                            return r.querySelector("input[name='buddySeq']") || r.querySelector("a[href*='blog.naver.com']");
                        });

                        const items = [];
                        for (let r of rows) {
                            const userLink = r.querySelector("a[href*='blog.naver.com']");
                            if (!userLink) continue;

                            const href = userLink.href || "";
                            let targetId = null;
                            if (href.includes("blog.naver.com/")) {
                                targetId = href.split("blog.naver.com/")[1].split("?")[0].split("/")[0];
                            }
                            if (!targetId || targetId === 'PostList.naver' || targetId === 'BuddyListManage.naver') {
                                continue;
                            }

                            const tds = Array.from(r.querySelectorAll("td"));
                            let groupName = (groupIdx >= 0 && groupIdx < tds.length) ? tds[groupIdx].innerText.trim() : "";
                            let buddyType = (typeIdx >= 0 && typeIdx < tds.length && tds[typeIdx].innerText.includes("서로이웃")) ? "서로이웃" : "이웃";
                            
                            let rawName = (nameIdx >= 0 && nameIdx < tds.length) ? tds[nameIdx].innerText.trim() : userLink.innerText.trim();
                            let nick = rawName.split("|")[0].trim();
                            let title = rawName.includes("|") ? rawName.split("|")[1].trim() : "";

                            let addedDate = "";
                            let lastPostDate = "";
                            const dateTds = tds.map(td => td.innerText.trim()).filter(t => /\d{2}\.\d{2}\.\d{2}/.test(t));
                            if (dateTds.length >= 2) {
                                addedDate = dateTds[0];
                                lastPostDate = dateTds[1];
                            } else if (dateTds.length === 1) {
                                addedDate = dateTds[0];
                            }

                            items.push({
                                blog_id: targetId,
                                nickname: nick || targetId,
                                blog_title: title,
                                group_name: groupName,
                                buddy_type: buddyType,
                                added_date: addedDate,
                                last_post_date: lastPostDate
                            });
                        }

                        // 페이지네이션 링크 추출
                        const nextLinks = Array.from(document.querySelectorAll(".paginate a, .pagination a, .paging a")).map(a => ({
                            text: a.innerText.trim(),
                            onclick: a.getAttribute("onclick") || a.href
                        }));

                        return {
                            expectedTotal: expected,
                            items: items,
                            nextLinks: nextLinks,
                            firstId: items.length > 0 ? items[0].blog_id : null
                        };
                    }
                """)

                if expected_total is None and page_data.get("expectedTotal"):
                    expected_total = page_data["expectedTotal"]
                    logger.log(f"📊 [BUDDY] 관리자 페이지 기준 총 이웃 수: {expected_total}명")

                items = page_data.get("items", [])
                if not items:
                    logger.log(f"⚠️ [BUDDY] {page_no}페이지에서 행을 찾지 못했습니다.", "WARNING")
                    break

                first_id = page_data.get("firstId", "")
                fingerprint = f"p{page_no}_{first_id}_{len(items)}"

                if fingerprint in page_fingerprints:
                    logger.log(f"⚠️ [BUDDY] 중복 페이지 지문 감지 ({fingerprint}) - 페이지네이션 종료", "WARNING")
                    break

                page_fingerprints.append(fingerprint)

                new_on_page = 0
                for it in items:
                    t_id = it["blog_id"]
                    if t_id not in all_buddies:
                        all_buddies[t_id] = BuddyInfo(
                            blog_id=t_id,
                            nickname=it["nickname"],
                            blog_title=it["blog_title"],
                            group_name=it["group_name"],
                            buddy_type=it["buddy_type"],
                            last_post_date=it["last_post_date"] or None,
                            added_date=it["added_date"]
                        )
                        new_on_page += 1

                logger.log(f"   📄 [Page {page_no}] {len(items)}명 수집 (누적 {len(all_buddies)}명 / 신규 {new_on_page}명)")

                # 다음 페이지 존재 여부 확인
                next_page_no = page_no + 1
                has_next = any(nl["text"] == str(next_page_no) for nl in page_data["nextLinks"])
                if not has_next:
                    has_next_btn = any(nl["text"] in (">", "다음", "Next") for nl in page_data["nextLinks"])
                    if not has_next_btn:
                        logger.log("🏁 [BUDDY] 마지막 페이지에 도달했습니다.")
                        break

                # goPage(next_page_no) 실행
                navigated = frame.evaluate(f"""
                    () => {{
                        if (typeof goPage === 'function') {{
                            goPage({next_page_no});
                            return true;
                        }}
                        const btn = Array.from(document.querySelectorAll('.paginate a, .pagination a, .paging a')).find(a => a.innerText.trim() === '{next_page_no}');
                        if (btn) {{
                            btn.click();
                            return true;
                        }}
                        return false;
                    }}
                """)

                if not navigated:
                    logger.log(f"⚠️ [BUDDY] {next_page_no}페이지 이동 실패", "WARNING")
                    break

                interruptible_wait(stop_event, 1.2)
                page_no = next_page_no

            collected_count = len(all_buddies)
            state: Literal["complete", "partial", "failed"] = "complete"

            if expected_total is not None and collected_count < expected_total:
                state = "partial"
                logger.log(f"⚠️ [BUDDY] 기대 이웃 수({expected_total}명) 대비 수집 수({collected_count}명) 부족 -> PARTIAL", "WARNING")
            elif collected_count == 0:
                state = "failed"
            else:
                logger.log(f"✅ [BUDDY] 전체 이웃 {collected_count}명 전수 수집 완료 (COMPLETE)!")

            return BuddyCollectionResult(
                buddies=all_buddies,
                state=state,
                expected_total=expected_total,
                collected_total=collected_count,
                pages_visited=page_no,
                page_fingerprints=page_fingerprints,
                error=None if state == "complete" else "collection_incomplete"
            )

        except Exception as e:
            logger.log(f"❌ [BUDDY] 이웃 전수 수집 실패: {e}", "ERROR")
            return BuddyCollectionResult(
                buddies={},
                state="failed",
                expected_total=None,
                collected_total=0,
                pages_visited=0,
                page_fingerprints=[],
                error=str(e)
            )
