from typing import List, Optional, Set
import urllib.parse
from playwright.sync_api import Page
import threading
from app.models import FeedPost, FeedSourceType
from naver.url_utils import extract_canonical_post
from naver.discovery.query_pool import QueryRotator
from naver.discovery.topic_filter import DiscoveryTopicFilter
from services.pacing import interruptible_wait
from services.config import logger


class TargetedSearchFeedSource:
    """
    모바일 통합검색 기반 관심주제 탐색 소스 (v9-lite / V13.3)
    - QueryRotator에 정의된 생활형 검색어 풀을 순환하며 네이버 블로그 검색 결과를 수집
    - 동일 블로거 중복 방지 (1세션 1블로그 1글)
    - Positive Category + Contextual Negative DiscoveryTopicFilter 통과 글만 선별
    """
    SEARCH_BASE_URL = "https://m.search.naver.com/search.naver?where=m_blog&sm=mtb_jum&query="

    def __init__(
        self,
        page: Page,
        rotator: QueryRotator,
        max_items: int = 20,
        stop_event: Optional[threading.Event] = None
    ):
        self.page = page
        self.rotator = rotator
        self.max_items = max_items
        self.stop_event = stop_event

        self.seen_keys: Set[str] = set()
        self.seen_blogs: Set[str] = set()
        self._exhausted = False
        self._current_query_url: str = ""

    def open(self):
        """초기 검색 페이지 진입"""
        first_query = self.rotator.current_query
        self._navigate_to_query(first_query)

    def _navigate_to_query(self, query: str):
        encoded_query = urllib.parse.quote(query)
        self._current_query_url = f"{self.SEARCH_BASE_URL}{encoded_query}"
        logger.log(f"🎯 [DISCOVERY] 검색어로 관심주제 탐색 시작: '{query}' (카테고리: {self.rotator.current_category})")
        try:
            self.page.goto(self._current_query_url, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(self.stop_event, 1.5)
        except Exception as e:
            logger.log(f"[DISCOVERY] 검색 페이지 로드 안내: {e}", "WARNING")

    def _switch_to_next_query(self):
        """할당량 도달 시 다음 검색어로 자동 이동"""
        next_q = self.rotator.next_query()
        logger.log(f"🔄 [DISCOVERY] 다음 검색어로 순환 이동: '{next_q}' (카테고리: {self.rotator.current_category})")
        self._navigate_to_query(next_q)

    def discover_posts(self) -> List[FeedPost]:
        discovered = []
        if self._exhausted:
            return discovered

        cards_seen = 0
        cards_parsed = 0
        cards_topic_blocked = 0
        cards_same_blog = 0
        card_dom_errors = 0

        # 네이버 모바일 블로그 검색 결과 카드 셀렉터
        card_selectors = [
            ".api_subject_bx .bx",
            "li.bx",
            ".view_wrap",
            ".total_wrap",
            ".fds-comps-feed-item",
            ".detail_box"
        ]

        cards = None
        for sel in card_selectors:
            loc = self.page.locator(sel)
            if loc.count() > 0:
                cards = loc
                break

        if not cards or cards.count() == 0:
            logger.log("ℹ️ [DISCOVERY] 검색 결과 카드 없음 -> 다음 검색어로 전환")
            self._switch_to_next_query()
            return discovered

        card_count = cards.count()
        cards_seen = card_count

        for idx in range(card_count):
            if self.stop_event and self.stop_event.is_set():
                break

            card = cards.nth(idx)
            try:
                # 1. 링크 엘리먼트 찾기
                link_el = card.locator("a[href*='blog.naver.com/']").first
                if link_el.count() == 0:
                    link_el = card

                raw_href = link_el.get_attribute("href")
                if not raw_href or "blog.naver.com" not in raw_href:
                    continue

                # 2. 제목 추출
                title_el = card.locator(".title_link, strong, .title, .tit, h3").first
                title = title_el.inner_text().strip().replace("\n", " ") if title_el.count() > 0 else ""

                # 3. 작성자 추출
                author_el = card.locator(".name, .sub_txt, .nick, .author").first
                author = author_el.inner_text().strip() if author_el.count() > 0 else ""

                # 4. 정규 포스트 모델 파싱
                post = extract_canonical_post(raw_href, FeedSourceType.TARGETED_SEARCH, title=title, author=author)
                if not post:
                    continue

                cards_parsed += 1

                # 5. 세션 중복 체크 (포스트 및 동일 블로거 중복 방지)
                if post.key in self.seen_keys:
                    continue
                if post.blog_id in self.seen_blogs:
                    cards_same_blog += 1
                    logger.log(f"  ⏭️ [DISCOVERY] 동일 블로그 1세션 1글 제한에 따라 스킵: {post.blog_id}")
                    continue

                # 6. Whitelist 카테고리 필터 검증 (expected_category 전달)
                try:
                    snippet = card.inner_text().strip()
                except Exception:
                    snippet = ""

                decision = DiscoveryTopicFilter.evaluate(
                    post.title or title,
                    snippet,
                    stage="card",
                    expected_category=self.rotator.current_category
                )
                if not decision.allowed:
                    cards_topic_blocked += 1
                    logger.log(
                        f"  [TOPIC_FILTER] card/{decision.blocked_category or decision.reason_code} "
                        f"evidence={list(decision.evidence)} title=\"{post.title[:30]}\""
                    )
                    continue

                # 통과: 수집 및 상태 등록
                self.seen_keys.add(post.key)
                self.seen_blogs.add(post.blog_id)
                discovered.append(post)
                logger.log(f"  [DISCOVERY] 추천 글 발굴: '{post.title[:32]}...' ({post.blog_id})")

                # 검색어별 포스트 할당량 도달 시 쿼리 순환
                should_switch = self.rotator.record_post_found()
                if should_switch:
                    self._switch_to_next_query()
                    break

            except Exception:
                card_dom_errors += 1
                continue

        logger.log(
            f"[DISCOVERY_SUMMARY] query=\"{self.rotator.current_query}\" "
            f"seen={cards_seen} parsed={cards_parsed} allowed={len(discovered)} "
            f"topicBlocked={cards_topic_blocked} sameBlog={cards_same_blog} domError={card_dom_errors}"
        )

        return discovered

    def scroll_and_load_more(self) -> int:
        """더보기 스크롤 또는 다음 검색어 순환"""
        try:
            self.page.evaluate("window.scrollBy(0, 1000)")
            interruptible_wait(self.stop_event, 1.2)
            new_cards = self.page.locator(".api_subject_bx .bx, li.bx, .view_wrap").count()
            if new_cards == 0:
                self._switch_to_next_query()
            return new_cards
        except Exception:
            self._switch_to_next_query()
            return 0
