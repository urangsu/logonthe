import urllib.parse
import threading
from typing import List, Set, Optional, Dict
from playwright.sync_api import Page, Locator

from app.models import FeedPost, FeedSourceType
from naver.url_utils import extract_canonical_post
from browser.session import interruptible_wait
from src.logger import logger
from naver.discovery.query_pool import QueryRotator
from naver.discovery.topic_filter import DiscoveryTopicFilter


class TargetedSearchFeedSource:
    """
    모바일 네이버 블로그 검색 기반 관심주제 순환 탐색 소스 (v9-lite)
    - 생활형 검색어 풀(QueryRotator)을 순환 검색
    - Whitelist 카테고리 필터(DiscoveryTopicFilter) 통과 글만 선별
    - 1세션 1블로그 1글 엄격 제한 (seen_blogs)
    """

    SEARCH_BASE_URL = "https://m.search.naver.com/search.naver?where=m_blog&sm=mtb_jum&query="

    def __init__(
        self,
        page: Page,
        max_items: int = 20,
        stop_event: Optional[threading.Event] = None,
        enabled_categories: Optional[List[str]] = None,
        custom_queries: Optional[List[str]] = None,
        posts_per_query: int = 3
    ):
        self.page = page
        self.max_items = max_items
        self.stop_event = stop_event

        self.rotator = QueryRotator(
            enabled_categories=enabled_categories,
            custom_queries=custom_queries,
            posts_per_query=posts_per_query
        )

        self.seen_keys: Set[str] = set()
        self.seen_blogs: Set[str] = set()
        self._exhausted = False
        self._current_loaded_query: Optional[str] = None

    def _build_search_url(self, query: str) -> str:
        encoded_query = urllib.parse.quote(query)
        return f"{self.SEARCH_BASE_URL}{encoded_query}"

    def open(self):
        query = self.rotator.current_query
        url = self._build_search_url(query)
        self._current_loaded_query = query
        logger.log(f"🎯 [DISCOVERY] 관심주제 탐색 시작 (검색어: '{query}') -> {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(self.stop_event, 1.5)
        except Exception as e:
            logger.log(f"⚠️ [DISCOVERY] 검색 페이지 로드 경고: {e}", "WARNING")

    def _switch_to_next_query(self) -> bool:
        """다음 검색어로 이동"""
        if self.is_exhausted():
            return False

        next_q = self.rotator.next_query()
        if next_q == self._current_loaded_query:
            # 쿼리가 1개뿐인 경우 스크롤 진행
            return False

        url = self._build_search_url(next_q)
        self._current_loaded_query = next_q
        logger.log(f"🔄 [DISCOVERY] 다음 관심주제 검색어로 순환: '{next_q}'")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
            interruptible_wait(self.stop_event, 1.5)
            return True
        except Exception as e:
            logger.log(f"⚠️ [DISCOVERY] 순환 검색 페이지 로드 오류: {e}", "WARNING")
            return False

    def discover_posts(self) -> List[FeedPost]:
        discovered = []

        # 검색 결과 내 포스트 링크 추출 (a[href*='blog.naver.com/'])
        cards = self.page.locator("li[class*='bx'], div[class*='item'], div[class*='card'], li[class*='item']")
        card_count = cards.count()

        if card_count == 0:
            # 기본 링크 직접 탐색
            cards = self.page.locator("a[href*='blog.naver.com/']")
            card_count = cards.count()

        for idx in range(card_count):
            if self.stop_event and self.stop_event.is_set():
                break
            if len(self.seen_keys) >= self.max_items:
                break

            card = cards.nth(idx)
            try:
                # 1. 링크 엘리먼트 찾기
                link_el = card.locator("a[href*='blog.naver.com/']").first
                if link_el.count() == 0:
                    # card 자체가 링크인 경우
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

                # 5. 세션 중복 체크 (포스트 및 동일 블로거 중복 방지)
                if post.key in self.seen_keys:
                    continue
                if post.blog_id in self.seen_blogs:
                    logger.log(f"  ⏭️ [DISCOVERY] 동일 블로그 1세션 1글 제한에 따라 스킵: {post.blog_id}")
                    continue

                # 6. Whitelist 카테고리 필터 검증
                allowed, cat_or_reason = DiscoveryTopicFilter.is_allowed(post.title or title)
                if not allowed:
                    logger.log(f"  🚫 [DISCOVERY] 비생활형/제외 주제 필터링: '{post.title[:30]}...' ({cat_or_reason})")
                    continue

                # 통과: 수집 및 상태 등록
                self.seen_keys.add(post.key)
                self.seen_blogs.add(post.blog_id)
                discovered.append(post)
                logger.log(f"  ✅ [DISCOVERY] 찐이웃 생활형 글 발굴 [{cat_or_reason}]: '{post.title[:32]}...' ({post.blog_id})")

                # 검색어별 포스트 할당량 도달 시 쿼리 순환
                should_switch = self.rotator.record_post_found()
                if should_switch:
                    self._switch_to_next_query()
                    break

            except Exception:
                continue

        return discovered

    def load_more(self) -> bool:
        if self.is_exhausted():
            return False

        # 1. 쿼리 전환 시도
        switched = self._switch_to_next_query()
        if switched:
            return True

        # 2. 현재 쿼리에서 스크롤 다운하여 더보기
        try:
            self.page.mouse.wheel(0, 1000)
            interruptible_wait(self.stop_event, 1.2)
            return True
        except Exception:
            return False

    def is_exhausted(self) -> bool:
        return self._exhausted or len(self.seen_keys) >= self.max_items
