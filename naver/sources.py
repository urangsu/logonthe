from typing import Any, Callable, Dict, List, Optional, Set
import threading
from playwright.sync_api import Page
from app.models import FeedPost, FeedSourceType
from app.errors import classify_playwright_failure, BrowserFailureKind, BrowserDisconnectedError
from naver.resolver import MobileDOMResolver, NeighborCountSource
from naver.url_utils import extract_canonical_post
from services.pacing import interruptible_wait
from src.logger import logger


class FeedSource:
    """피드 소스 인터페이스"""
    def open(self):
        raise NotImplementedError

    def discover_posts(self) -> List[FeedPost]:
        raise NotImplementedError

    def load_more(self) -> bool:
        raise NotImplementedError

    def is_exhausted(self) -> bool:
        raise NotImplementedError


class NeighborFeedSource(FeedSource):
    """모바일 이웃 새글 피드 소스"""
    URL = "https://m.blog.naver.com/FeedList.naver"

    def __init__(
        self,
        page: Page,
        max_items: int = 20,
        stop_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        run_control = None,
    ):
        self.page = page
        self.max_items = max_items
        self.run_control = run_control
        self.stop_event = stop_event or getattr(run_control, "stop_event", None)
        self.pause_event = pause_event or getattr(run_control, "pause_event", None)
        self.seen_keys: Set[str] = set()
        self._exhausted = False

    def open(self):
        logger.log(f"[SOURCE] 이웃 새글 피드 접속: {self.URL}")
        try:
            self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            if self.run_control and hasattr(self.run_control, "interruptible_wait"):
                self.run_control.interruptible_wait(1.5, stage="open_neighbor_source")
            else:
                interruptible_wait(self.stop_event, 1.5, pause_event=self.pause_event)
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"이웃 피드 접속 중 브라우저 종료 감지: {e}")
            logger.log(f"[SOURCE] 피드 페이지 로드 안내: {e}", "WARNING")

    def discover_posts(self) -> List[FeedPost]:
        discovered = []
        if self.run_control and hasattr(self.run_control, "checkpoint"):
            self.run_control.checkpoint("before_feed_discovery")
        try:
            cards = MobileDOMResolver.get_feed_cards(self.page)
            card_count = cards.count()
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"이웃 피드 탐색 중 브라우저 종료 감지: {e}")
            return discovered

        for idx in range(card_count):
            if self.stop_event and self.stop_event.is_set():
                break

            try:
                card = cards.nth(idx)
                link_el = MobileDOMResolver.get_card_post_link(card)
                if not link_el or link_el.count() == 0:
                    continue

                raw_href = link_el.get_attribute("href")
                if not raw_href:
                    continue

                title = MobileDOMResolver.get_card_title(card)
                author = MobileDOMResolver.get_card_author(card)

                post = extract_canonical_post(raw_href, FeedSourceType.NEIGHBOR, title=title, author=author)
                if post and post.key not in self.seen_keys:
                    self.seen_keys.add(post.key)
                    discovered.append(post)
            except Exception as e:
                kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
                if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                    raise BrowserDisconnectedError(f"이웃 피드 카드 파싱 중 브라우저 종료 감지: {e}")
                continue

        return discovered

    def load_more(self) -> bool:
        """아래로 스크롤하여 추가 피드 로드"""
        if self.is_exhausted():
            return False

        if self.run_control and hasattr(self.run_control, "checkpoint"):
            self.run_control.checkpoint("before_scroll")

        try:
            self.page.mouse.wheel(0, 900)
            if self.run_control and hasattr(self.run_control, "interruptible_wait"):
                self.run_control.interruptible_wait(1.0, stage="after_scroll")
            else:
                interruptible_wait(self.stop_event, 1.0, pause_event=self.pause_event)
            return True
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"이웃 피드 스크롤 중 브라우저 종료 감지: {e}")
            return False

    def is_exhausted(self) -> bool:
        return self._exhausted or len(self.seen_keys) >= self.max_items


class RecommendationFeedSource(FeedSource):
    """모바일 탐색 추천 피드 (Recommendation.naver) 탐색 소스 - 맛집 우선(1순위) 및 푸드 fallback 선택/검증"""
    URL = "https://m.blog.naver.com/Recommendation.naver"

    def __init__(
        self,
        page: Page,
        max_items: int = 20,
        stop_event: Optional[threading.Event] = None,
        preferred_category: str = "맛집",
        fallback_category: str = "푸드",
        pause_event: Optional[threading.Event] = None,
        run_control = None,
        neighbor_count_max: Optional[int] = None,
        neighbor_count_resolver: Optional[Callable[[str], Any]] = None,
    ):
        self.page = page
        self.max_items = max_items
        self.run_control = run_control
        self.stop_event = stop_event or getattr(run_control, "stop_event", None)
        self.pause_event = pause_event or getattr(run_control, "pause_event", None)
        self.preferred_category = preferred_category
        self.fallback_category = fallback_category
        self.neighbor_count_max = neighbor_count_max
        self.neighbor_count_resolver = neighbor_count_resolver
        self.seen_keys: Set[str] = set()
        self.seen_blogs: Set[str] = set()
        self.examined_keys: Set[str] = set()  # 같은 run 에서 재검사 금지
        self._exhausted = False

        # 이웃수 필터 (프로필 조회 기반) 상태
        self.neighbor_probe_results: Dict[str, Any] = {}  # blog_id -> NeighborCountResult (negative cache 포함)
        self.consecutive_neighbor_unknown = 0
        self.resolver_broken = False
        self.neighbor_stats = {"lookups": 0, "known": 0, "allowed": 0, "blocked": 0, "unknown": 0}

    NEIGHBOR_BREAKER_ATTEMPTS = 5

    @property
    def neighbor_filter_active(self) -> bool:
        return bool(self.neighbor_count_max and self.neighbor_count_max > 0)

    def open(self):
        logger.log(f"[SOURCE] 탐색 추천 피드 접속: {self.URL}")
        try:
            self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(self.stop_event, 1.5)

            # 1. 클릭 전 카드 지문 채취
            before_cards = self.page.evaluate(
                "() => Array.from(document.querySelectorAll('.view_wrap, .fds-comps-feed-item, .bx')).map(e => (e.textContent || '').slice(0, 30))"
            )

            # 2. 카테고리 탭 탐색: 1순위 맛집 -> 2순위 푸드 fallback
            tab_selected = False
            for cat_target, is_fallback in [(self.preferred_category, False), (self.fallback_category, True)]:
                if self.stop_event and self.stop_event.is_set():
                    break

                click_result = self.page.evaluate("""(targetCategory) => {
                    const shown = el => !!el && !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
                    const interactiveCandidates = Array.from(document.querySelectorAll('button, a, [role=tab], li[role=tab], div[role=button]')).filter(shown);

                    // 1차: exact text match 우선
                    let target = interactiveCandidates.find(el => el.textContent.trim() === targetCategory);
                    // 2차: 포함 관계 (전체 제외, 길이 15자 이하)
                    if (!target) {
                        target = interactiveCandidates.find(el => {
                            const text = el.textContent.trim();
                            return new RegExp(targetCategory).test(text) && !/전체/.test(text) && text.length <= 15;
                        });
                    }

                    if (!target) return { status: "not_found" };
                    target.click();
                    return {
                        status: "clicked",
                        text: target.textContent.trim()
                    };
                }""", cat_target)

                if click_result and click_result.get("status") == "clicked":
                    interruptible_wait(self.stop_event, 1.2)
                    # 3. 클릭 후 실제 active/aria-selected 상태 또는 카드 목록 변화 검증
                    verification = self.page.evaluate("""(args) => {
                        const { targetCategory, beforeCards } = args;
                        const shown = el => !!el && !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
                        const interactiveCandidates = Array.from(document.querySelectorAll('button, a, [role=tab], li[role=tab], div[role=button]')).filter(shown);
                        let target = interactiveCandidates.find(el => el.textContent.trim() === targetCategory);
                        if (!target) {
                            target = interactiveCandidates.find(el => {
                                const text = el.textContent.trim();
                                return new RegExp(targetCategory).test(text) && !/전체/.test(text) && text.length <= 15;
                            });
                        }
                        const afterActive = target ? (target.getAttribute('aria-selected') === 'true' || /active|on|selected/.test(target.className)) : false;
                        const afterCards = Array.from(document.querySelectorAll('.view_wrap, .fds-comps-feed-item, .bx')).map(e => (e.textContent || '').slice(0, 30));
                        const cardsChanged = JSON.stringify(beforeCards) !== JSON.stringify(afterCards);
                        return {
                            active: afterActive,
                            cardsChanged: cardsChanged,
                            verified: afterActive || cardsChanged
                        };
                    }""", {"targetCategory": cat_target, "beforeCards": before_cards})

                    if verification and verification.get("verified"):
                        mode_label = f"category={cat_target}" if not is_fallback else f"fallback={cat_target}"
                        logger.log(f"✅ [SOURCE] 탐색 탭에서 '[{click_result.get('text')}]' 필터 버튼 클릭 및 활성화 확인 완료 ({mode_label})")
                        tab_selected = True
                        break
                    else:
                        logger.log(f"⚠️ [SOURCE] 탐색 탭 카테고리 버튼('{cat_target}') 클릭되었으나 선택 상태 미검증 -> fallback 시도", "WARNING")
                else:
                    logger.log(f"ℹ️ [SOURCE] 탐색 탭 카테고리 '{cat_target}' 미발견 -> 다음 후보 시도")

            if not tab_selected:
                logger.log(
                    f"⚠️ [SOURCE] 탐색 탭 카테고리('{self.preferred_category}', fallback='{self.fallback_category}') 모두 미발견 또는 검증 실패 (안전 종료: category_tab_not_found)",
                    "WARNING",
                )
                self._exhausted = True
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"추천 피드 접속 중 브라우저 종료 감지: {e}")
            logger.log(f"[SOURCE] 추천 페이지 로드 안내: {e}", "WARNING")

    def discover_posts(self) -> List[FeedPost]:
        discovered = []
        if self.run_control and hasattr(self.run_control, "checkpoint"):
            self.run_control.checkpoint("before_feed_discovery")
        try:
            cards = MobileDOMResolver.get_feed_cards(self.page)
            card_count = cards.count()
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"추천 피드 카드 탐색 중 브라우저 종료 감지: {e}")
            return discovered

        cards_seen = card_count
        cards_parsed = 0
        cards_already_examined = 0
        cards_topic_blocked = 0
        cards_topic_eligible = 0
        cards_same_blog = 0
        card_dom_errors = 0
        lookups_before = self.neighbor_stats["lookups"]
        known_before = self.neighbor_stats["known"]
        allowed_before = self.neighbor_stats["allowed"]
        blocked_before = self.neighbor_stats["blocked"]
        unknown_before = self.neighbor_stats["unknown"]

        from naver.discovery.topic_filter import DiscoveryTopicFilter

        for idx in range(card_count):
            if self.stop_event and self.stop_event.is_set():
                break
            if self.resolver_broken:
                break

            try:
                card = cards.nth(idx)
                link_el = MobileDOMResolver.get_card_post_link(card)
                if not link_el or link_el.count() == 0:
                    continue

                raw_href = link_el.get_attribute("href")
                if not raw_href:
                    continue

                title = MobileDOMResolver.get_card_title(card)
                author = MobileDOMResolver.get_card_author(card)

                post = extract_canonical_post(raw_href, FeedSourceType.RECOMMENDATION, title=title, author=author)
                if not post:
                    continue

                # 이미 판정(허용/차단/UNKNOWN)한 카드는 같은 run 에서 재검사하지 않는다
                if post.key in self.examined_keys:
                    cards_already_examined += 1
                    continue
                self.examined_keys.add(post.key)
                cards_parsed += 1

                # 1) 동일 블로그 1세션 1글 제한 (프로필 조회 전에 최대한 거른다)
                if post.blog_id in self.seen_blogs:
                    cards_same_blog += 1
                    logger.log(f"  ⏭️ [SOURCE] 동일 블로그 1세션 1글 제한에 따라 스킵: {post.blog_id}")
                    continue

                # 2) Topic filter (버릴 글에 프로필 조회 비용을 쓰지 않는다)
                try:
                    snippet = card.inner_text().strip()
                except Exception:
                    snippet = ""
                decision = DiscoveryTopicFilter.evaluate(title or "", snippet, stage="card")
                if not decision.allowed:
                    cards_topic_blocked += 1
                    logger.log(
                        f"  [TOPIC_FILTER] card/{decision.blocked_category or decision.reason_code} "
                        f"evidence={list(decision.evidence)} title=\"{title}\""
                    )
                    continue
                cards_topic_eligible += 1

                # 3) 이웃수 필터: 프로필 페이지 조회 (카드 DOM 비의존)
                if self.neighbor_filter_active:
                    verdict = self._check_neighbor_count(post.blog_id)
                    if verdict == "stopped":
                        break
                    if verdict != "allow":
                        if self.resolver_broken:
                            break
                        continue

                if post.key not in self.seen_keys:
                    self.seen_keys.add(post.key)
                    self.seen_blogs.add(post.blog_id)
                    discovered.append(post)
            except Exception as e:
                kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
                if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                    raise BrowserDisconnectedError(f"추천 피드 카드 파싱 중 브라우저 종료 감지: {e}")
                card_dom_errors += 1
                continue

        st = self.neighbor_stats
        logger.log(
            f"[DISCOVERY_SUMMARY]\nrecommendation\n"
            f"seen={cards_seen}\nparsed={cards_parsed}\nalreadyExamined={cards_already_examined}\n"
            f"topicEligible={cards_topic_eligible}\ntopicBlocked={cards_topic_blocked}\n"
            f"sameBlog={cards_same_blog}\n"
            f"neighborLookups={st['lookups'] - lookups_before}\n"
            f"neighborKnown={st['known'] - known_before}\n"
            f"neighborAllowed={st['allowed'] - allowed_before}\n"
            f"neighborBlocked={st['blocked'] - blocked_before}\n"
            f"neighborUnknown={st['unknown'] - unknown_before}\n"
            f"allowed={len(discovered)}\ndomError={card_dom_errors}"
        )

        return discovered

    def _check_neighbor_count(self, blog_id: str) -> str:
        """
        프로필 조회로 이웃수를 판정한다.
        returns: "allow" | "block" | "unknown" | "stopped"
        UNKNOWN 은 해당 블로그만 skip(fail-safe). 시스템 전체 UNKNOWN 이면 resolver_broken 설정.
        """
        if self.neighbor_count_resolver is None:
            self._trip_breaker(reason="resolver_not_configured")
            return "unknown"

        result = self.neighbor_probe_results.get(blog_id)
        if result is None:
            result = self.neighbor_count_resolver(blog_id)
            if getattr(result, "error", None) == "stopped":
                return "stopped"
            self.neighbor_probe_results[blog_id] = result
            self.neighbor_stats["lookups"] += 1

            if result.value is None:
                self.neighbor_stats["unknown"] += 1
                self.consecutive_neighbor_unknown += 1
            else:
                self.neighbor_stats["known"] += 1
                self.consecutive_neighbor_unknown = 0

        value = result.value
        if value is None:
            logger.log(
                f"  ⏭️ [NEIGHBOR_COUNT_UNKNOWN] blog={blog_id} error={result.error} -> 개별 skip (fail-safe)"
            )
            st = self.neighbor_stats
            if st["known"] == 0 and st["lookups"] >= self.NEIGHBOR_BREAKER_ATTEMPTS:
                self._trip_breaker(reason="no_known_after_attempts")
            return "unknown"

        if value > self.neighbor_count_max:
            self.neighbor_stats["blocked"] += 1
            logger.log(
                f"  ⏭️ [NEIGHBOR_COUNT_FILTER] blog={blog_id} count={value} "
                f"max={self.neighbor_count_max} -> skip"
            )
            return "block"

        self.neighbor_stats["allowed"] += 1
        logger.log(
            f"  ✅ [NEIGHBOR_COUNT_FILTER] blog={blog_id} count={value} max={self.neighbor_count_max} -> allow"
        )
        return "allow"

    def _trip_breaker(self, reason: str) -> None:
        if self.resolver_broken:
            return
        self.resolver_broken = True
        st = self.neighbor_stats
        logger.log(
            f"🚨 [NEIGHBOR_PROFILE_RESOLVER_BROKEN]\n"
            f"attempts={st['lookups']}\nknown={st['known']}\nunknown={st['unknown']}\nreason={reason}",
            "ERROR",
        )


    def load_more(self) -> bool:
        if self.is_exhausted():
            return False

        if self.run_control and hasattr(self.run_control, "checkpoint"):
            self.run_control.checkpoint("before_scroll")

        try:
            self.page.mouse.wheel(0, 900)
            if self.run_control and hasattr(self.run_control, "interruptible_wait"):
                self.run_control.interruptible_wait(1.0, stage="after_scroll")
            else:
                interruptible_wait(self.stop_event, 1.0, pause_event=self.pause_event)
            return True
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"추천 피드 스크롤 중 브라우저 종료 감지: {e}")
            return False


    def is_exhausted(self) -> bool:
        return self._exhausted or len(self.seen_keys) >= self.max_items


class DirectUrlSource(FeedSource):
    """사용자가 직접 입력한 URL 목록 소스"""
    def __init__(self, raw_urls: List[str]):
        self.raw_urls = raw_urls
        self.posts: List[FeedPost] = []
        self._index = 0
        self._prepare()

    def _prepare(self):
        seen = set()
        for raw in self.raw_urls:
            raw_s = raw.strip()
            if not raw_s:
                continue
            post = extract_canonical_post(raw_s, FeedSourceType.DIRECT)
            if post and post.key not in seen:
                seen.add(post.key)
                self.posts.append(post)

    def open(self):
        logger.log(f"[SOURCE] URL 직접 입력 목록 {len(self.posts)}개 준비 완료.")

    def discover_posts(self) -> List[FeedPost]:
        return self.posts

    def load_more(self) -> bool:
        return False

    def is_exhausted(self) -> bool:
        return True


# Re-export TargetedSearchFeedSource
from naver.discovery.search_source import TargetedSearchFeedSource
