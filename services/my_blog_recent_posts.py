import re
from typing import List, Dict, Any, Optional, Tuple
from playwright.sync_api import Page
from browser.session import interruptible_wait
from src.logger import logger


class MyBlogRecentPostService:
    """
    내 네이버 블로그의 최근 일반 공개 포스트 목록을 수집한다.

    핵심 계약:
    - DOM에서 먼저 발견된 N개를 그대로 쓰지 않는다.
    - 공지/고정글을 제외한다.
    - 전체 후보를 먼저 수집한 뒤 최신순으로 정렬하고 마지막에 max_count를 적용한다.
    - 카드에서 발행시각을 충분히 확보하면 발행시각을 우선하고, 그렇지 않으면
      Naver logNo의 최신순을 fallback으로 사용한다.
    """

    @classmethod
    def _rank_candidates(
        cls,
        candidates: List[Dict[str, Any]],
        max_count: int,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """중복 후보를 정리하고 최신순으로 정렬한다."""
        limit = max(1, int(max_count or 1))
        by_log_no: Dict[str, Dict[str, Any]] = {}

        for raw in candidates or []:
            log_no = str(raw.get("log_no", "")).strip()
            if not re.fullmatch(r"\d+", log_no):
                continue

            item = {
                "log_no": log_no,
                "url": str(raw.get("url", "")).strip(),
                "title": str(raw.get("title", "")).strip() or f"포스트 {log_no}",
                "published_text": str(raw.get("published_text", "")).strip(),
                "published_at_ms": int(raw.get("published_at_ms") or 0),
                "dom_index": int(raw.get("dom_index") or 0),
            }

            prev = by_log_no.get(log_no)
            if prev is None:
                by_log_no[log_no] = item
                continue

            # 같은 글의 이미지 링크/제목 링크가 여러 번 잡히는 경우,
            # 발행시각과 제목 정보가 더 풍부한 후보를 남긴다.
            prev_score = (
                1 if prev.get("published_at_ms") else 0,
                len(prev.get("title", "")),
            )
            item_score = (
                1 if item.get("published_at_ms") else 0,
                len(item.get("title", "")),
            )
            if item_score > prev_score:
                by_log_no[log_no] = item

        unique = list(by_log_no.values())
        if not unique:
            return [], "none"

        dated_count = sum(1 for item in unique if item.get("published_at_ms"))
        required_dated = min(limit, len(unique))

        if dated_count >= required_dated:
            # 실제 게시일이 max_count만큼 확보된 경우 게시일이 있는 카드만으로
            # 최신 N개를 확정한다. 상단 추천/대표 영역처럼 날짜가 없는 링크가
            # 최신글로 섞이는 것을 방지한다.
            ranked = [item for item in unique if item.get("published_at_ms")]
            ranked.sort(
                key=lambda item: (
                    int(item.get("published_at_ms") or 0),
                    int(item["log_no"]),
                ),
                reverse=True,
            )
            ordering = "published_at"
        else:
            # Naver PostList DOM에서 날짜 selector를 충분히 확인하지 못했을 때는
            # 첫 DOM 순서를 신뢰하지 않고 logNo 최신순으로 fallback한다.
            ranked = sorted(unique, key=lambda item: int(item["log_no"]), reverse=True)
            ordering = "log_no_fallback"

        return ranked[:limit], ordering

    @classmethod
    def fetch_recent_posts(
        cls,
        page: Page,
        blog_id: str,
        max_count: int = 10,
        stop_event: Optional[Any] = None,
    ) -> List[Dict[str, str]]:
        if not blog_id or not blog_id.strip():
            logger.log("⚠️ [AUDIT] 블로그 ID가 지정되지 않았습니다.", "WARNING")
            return []

        b_id = blog_id.strip()
        max_count = max(1, int(max_count or 10))
        url = f"https://m.blog.naver.com/PostList.naver?blogId={b_id}"
        logger.log(f"🔎 [AUDIT] 내 블로그 최근 공개 글 목록 조회 중: {url} (최대 {max_count}개)")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(stop_event, 1.5)

            candidates = page.evaluate(r"""
                (arg) => {
                    const blogId = arg.blogId;
                    const links = Array.from(
                        document.querySelectorAll("a[href*='PostView.naver'], a[href*='/" + blogId + "/']")
                    );
                    const items = [];

                    const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();

                    const parsePublishedMs = (text) => {
                        const raw = clean(text);
                        if (!raw) return 0;
                        const now = new Date();
                        let m = raw.match(/(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})/);
                        if (m) {
                            const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
                            return Number.isNaN(dt.getTime()) ? 0 : dt.getTime();
                        }
                        m = raw.match(/(?:^|\s)(\d{1,2})[.]\s*(\d{1,2})[.](?:\s|$)/);
                        if (m) {
                            const dt = new Date(now.getFullYear(), Number(m[1]) - 1, Number(m[2]));
                            return Number.isNaN(dt.getTime()) ? 0 : dt.getTime();
                        }
                        m = raw.match(/(\d+)\s*분\s*전/);
                        if (m) return now.getTime() - Number(m[1]) * 60 * 1000;
                        m = raw.match(/(\d+)\s*시간\s*전/);
                        if (m) return now.getTime() - Number(m[1]) * 60 * 60 * 1000;
                        if (raw.includes('어제')) return now.getTime() - 24 * 60 * 60 * 1000;
                        return 0;
                    };

                    const extractPublished = (card) => {
                        if (!card) return { text: '', ms: 0 };
                        const candidates = [];
                        const timeEl = card.querySelector('time');
                        if (timeEl) {
                            candidates.push(timeEl.getAttribute('datetime'));
                            candidates.push(timeEl.textContent);
                        }
                        for (const attr of ['data-publish-date', 'data-post-date', 'data-date', 'datetime']) {
                            const holder = card.querySelector('[' + attr + ']');
                            if (holder) candidates.push(holder.getAttribute(attr));
                        }
                        const dateEls = card.querySelectorAll(
                            "[class*='date'], [class*='time'], [class*='publish'], [class*='created']"
                        );
                        for (const el of dateEls) candidates.push(el.textContent);

                        for (const value of candidates) {
                            const text = clean(value);
                            const ms = parsePublishedMs(text);
                            if (ms) return { text, ms };
                        }
                        return { text: '', ms: 0 };
                    };

                    const isNoticeCard = (a, card) => {
                        const classBlob = clean(
                            `${a.className || ''} ${card && card.className ? card.className : ''}`
                        ).toLowerCase();
                        if (/(^|[\s_-])(notice|pinned|pin|fixed)([\s_-]|$)/.test(classBlob)) {
                            return true;
                        }
                        if (!card) return false;
                        const badgeEls = card.querySelectorAll(
                            "[class*='notice'], [class*='pin'], [class*='badge'], [class*='label']"
                        );
                        return Array.from(badgeEls).some((el) => {
                            const text = clean(el.textContent);
                            return /^(공지|공지글|고정|고정글)$/.test(text);
                        });
                    };

                    links.forEach((a, domIndex) => {
                        let parsed;
                        try {
                            parsed = new URL(a.getAttribute('href') || a.href, location.href);
                        } catch (_) {
                            return;
                        }

                        let logNo = parsed.searchParams.get('logNo');
                        const queryBlogId = parsed.searchParams.get('blogId');
                        if (queryBlogId && queryBlogId !== blogId) return;

                        const pathMatch = parsed.pathname.match(/^\/([^/]+)\/(\d+)\/?$/);
                        if (!logNo && pathMatch && decodeURIComponent(pathMatch[1]) === blogId) {
                            logNo = pathMatch[2];
                        }
                        if (!logNo || !/^\d+$/.test(logNo)) return;

                        const card = a.closest(
                            "li, article, [class*='post'], [class*='item'], [class*='list']"
                        ) || a.parentElement || a;
                        if (isNoticeCard(a, card)) return;

                        const titleEl = card.querySelector(
                            "[class*='title'], [class*='tit'], h2, h3, strong"
                        );
                        const title = clean(
                            (titleEl && titleEl.textContent) || a.textContent || a.getAttribute('aria-label')
                        );
                        const published = extractPublished(card);

                        items.push({
                            log_no: logNo,
                            url: "https://m.blog.naver.com/" + blogId + "/" + logNo,
                            title: title || ("포스트 " + logNo),
                            published_text: published.text,
                            published_at_ms: published.ms,
                            dom_index: domIndex,
                        });
                    });

                    return items;
                }
            """, {"blogId": b_id})

            posts, ordering = cls._rank_candidates(candidates or [], max_count=max_count)
            logger.log(
                f"✅ [AUDIT] 최근 일반 공개 글 {len(posts)}개 확보 완료 "
                f"(DOM 후보 {len(candidates or [])}개, 정렬={ordering})"
            )
            for p in posts:
                published = p.get("published_text") or "날짜 미확인"
                logger.log(f"   📄 [{p['log_no']}] [{published}] {p['title'][:45]}...")
            return posts
        except Exception as e:
            logger.log(f"❌ [AUDIT] 최근 글 목록 조회 실패: {e}", "ERROR")
            return []
