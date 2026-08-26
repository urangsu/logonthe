from typing import List, Protocol, Set, Optional
from playwright.sync_api import Page
from app.models import FeedPost, FeedSourceType
from naver.url_utils import extract_canonical_post
from naver.resolver import MobileDOMResolver
from browser.session import interruptible_wait
from src.logger import logger
import threading


class FeedSource(Protocol):
    def open(self) -> None:
        ...

    def discover_posts(self) -> List[FeedPost]:
        ...

    def load_more(self) -> bool:
        ...

    def is_exhausted(self) -> bool:
        ...


class NeighborFeedSource:
    """모바일 이웃 새글 피드 (FeedList.naver) 탐색 소스"""
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
            logger.log(f"[SOURCE] 피드 페이지 로드 안내: {e}", "WARNING")

    def discover_posts(self) -> List[FeedPost]:
        """현재 화면에 렌더링된 피드 카드들로부터 새로운 canonical FeedPost 수집"""
        discovered = []
        cards = MobileDOMResolver.get_feed_cards(self.page)
        card_count = cards.count()

        for idx in range(card_count):
            if self.stop_event and self.stop_event.is_set():
                break

            card = cards.nth(idx)
            try:
                link_el = MobileDOMResolver.get_card_post_link(card)
                if link_el.count() == 0:
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
            except Exception:
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
        except Exception:
            return False

    def is_exhausted(self) -> bool:
        return self._exhausted or len(self.seen_keys) >= self.max_items


class RecommendationFeedSource:
    """모바일 탐색 추천 피드 (Recommendation.naver) 탐색 소스"""
    URL = "https://m.blog.naver.com/Recommendation.naver"

    def __init__(self, page: Page, max_items: int = 20, stop_event: Optional[threading.Event] = None):
        self.page = page
        self.max_items = max_items
        self.stop_event = stop_event
        self.seen_keys: Set[str] = set()
        self._exhausted = False

    def open(self):
        logger.log(f"[SOURCE] 탐색 추천 피드 접속: {self.URL}")
        try:
            self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            interruptible_wait(self.stop_event, 1.5)
        except Exception as e:
            logger.log(f"[SOURCE] 추천 페이지 로드 안내: {e}", "WARNING")

    def discover_posts(self) -> List[FeedPost]:
        discovered = []
        cards = MobileDOMResolver.get_feed_cards(self.page)
        card_count = cards.count()

        for idx in range(card_count):
            if self.stop_event and self.stop_event.is_set():
                break

            card = cards.nth(idx)
            try:
                link_el = MobileDOMResolver.get_card_post_link(card)
                if link_el.count() == 0:
                    continue

                raw_href = link_el.get_attribute("href")
                if not raw_href:
                    continue

                title = MobileDOMResolver.get_card_title(card)
                author = MobileDOMResolver.get_card_author(card)

                post = extract_canonical_post(raw_href, FeedSourceType.RECOMMENDATION, title=title, author=author)
                if post and post.key not in self.seen_keys:
                    self.seen_keys.add(post.key)
                    discovered.append(post)
            except Exception:
                continue

        return discovered

    def load_more(self) -> bool:
        if self.is_exhausted():
            return False

        try:
            self.page.mouse.wheel(0, 900)
            interruptible_wait(self.stop_event, 1.0)
            return True
        except Exception:
            return False

    def is_exhausted(self) -> bool:
        return self._exhausted or len(self.seen_keys) >= self.max_items


class DirectUrlSource:
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


# Alias/Re-export TargetedSearchFeedSource for convenience
from naver.discovery.search_source import TargetedSearchFeedSource
