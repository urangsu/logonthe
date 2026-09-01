from typing import List, Optional, Set
import threading
from playwright.sync_api import Page
from app.models import FeedPost, FeedSourceType
from app.errors import classify_playwright_failure, BrowserFailureKind, BrowserDisconnectedError
from naver.resolver import MobileDOMResolver
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

    def __init__(self, page: Page, max_items: int = 20, stop_event: Optional[threading.Event] = None):
        self.page = page
        self.max_items = max_items
        self.stop_event = stop_event
        self.seen_keys: Set[str] = set()
        self._exhausted = False

    def open(self):
        logger.log(f"[SOURCE] 이웃 새글 피드 접속: {self.URL}")
        try:
            self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(self.stop_event, 1.5)
        except Exception as e:
            kind = classify_playwright_failure(e, page=self.page, context=getattr(self.page, "context", None))
            if kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED):
                raise BrowserDisconnectedError(f"이웃 피드 접속 중 브라우저 종료 감지: {e}")
            logger.log(f"[SOURCE] 피드 페이지 로드 안내: {e}", "WARNING")

    def discover_posts(self) -> List[FeedPost]:
        discovered = []
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

        try:
            self.page.mouse.wheel(0, 900)
            interruptible_wait(self.stop_event, 1.0)
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
    ):
        self.page = page
        self.max_items = max_items
        self.stop_event = stop_event
        self.preferred_category = preferred_category
        self.fallback_category = fallback_category
        self.seen_keys: Set[str] = set()
        self.seen_blogs: Set[str] = set()
        self._exhausted = False

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
        cards_topic_blocked = 0
        cards_same_blog = 0
        card_dom_errors = 0

        from naver.discovery.topic_filter import DiscoveryTopicFilter

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
                try:
                    snippet = card.inner_text().strip()
                except Exception:
                    snippet = ""

                post = extract_canonical_post(raw_href, FeedSourceType.RECOMMENDATION, title=title, author=author)
                if not post:
                    continue

                cards_parsed += 1

                # 동일 블로그 1세션 1글 제한
                if post.blog_id in self.seen_blogs:
                    cards_same_blog += 1
                    logger.log(f"  ⏭️ [SOURCE] 동일 블로그 1세션 1글 제한에 따라 스킵: {post.blog_id}")
                    continue

                # Positive category + Contextual negative gate
                decision = DiscoveryTopicFilter.evaluate(title or "", snippet, stage="card")
                if not decision.allowed:
                    cards_topic_blocked += 1
                    logger.log(
                        f"  [TOPIC_FILTER] card/{decision.blocked_category or decision.reason_code} "
                        f"evidence={list(decision.evidence)} title=\"{title}\""
                    )
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

        logger.log(
            f"[DISCOVERY_SUMMARY] recommendation seen={cards_seen} parsed={cards_parsed} "
            f"allowed={len(discovered)} topicBlocked={cards_topic_blocked} sameBlog={cards_same_blog} domError={card_dom_errors}"
        )

        return discovered

    def load_more(self) -> bool:
        if self.is_exhausted():
            return False

        try:
            self.page.mouse.wheel(0, 900)
            interruptible_wait(self.stop_event, 1.0)
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
