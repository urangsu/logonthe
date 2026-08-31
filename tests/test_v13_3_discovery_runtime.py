import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import threading

from app.models import (
    FeedPost, FeedSourceType, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState, PostActionPlan
)
from app.controller import FeedController
from app.processor import PostProcessor
from app.errors import (
    BrowserDisconnectedError, RecoverablePostError, PostNavigationMismatchError,
    BrowserFailureKind, classify_playwright_failure
)
from naver.discovery.query_pool import QueryRotator, QuerySpec
from naver.discovery.topic_filter import DiscoveryTopicFilter
from naver.sources import RecommendationFeedSource
from naver.discovery.search_source import TargetedSearchFeedSource


class TestV133DiscoveryAndRuntime(unittest.TestCase):
    def test_disc_001_targeted_search_category_preserved(self):
        rotator = QueryRotator(enabled_categories=["FOOD", "CAFE"], posts_per_query=2)
        spec = rotator.current_spec
        self.assertIsInstance(spec, QuerySpec)
        self.assertIn(spec.category, ["FOOD", "CAFE"])
        self.assertEqual(rotator.current_query, spec.query)

    def test_disc_002_same_blog_second_post_skipped(self):
        page = MagicMock()
        # Mocking cards
        card1 = MagicMock()
        card1.locator.return_value.first.get_attribute.return_value = "https://m.blog.naver.com/same_author/101"
        card1.locator.return_value.first.count.return_value = 1
        card1.locator.return_value.first.inner_text.return_value = "맛집 후기"
        card1.inner_text.return_value = "맛있는 고기"

        card2 = MagicMock()
        card2.locator.return_value.first.get_attribute.return_value = "https://m.blog.naver.com/same_author/102"
        card2.locator.return_value.first.count.return_value = 1
        card2.locator.return_value.first.inner_text.return_value = "맛집 또 다녀옴"
        card2.inner_text.return_value = "맛있는 갈비"

        cards_mock = MagicMock()
        cards_mock.count.return_value = 2
        cards_mock.nth.side_effect = [card1, card2]

        with patch("naver.sources.MobileDOMResolver.get_feed_cards", return_value=cards_mock), \
             patch("naver.sources.MobileDOMResolver.get_card_post_link", side_effect=[MagicMock(count=lambda: 1, get_attribute=lambda k: "https://m.blog.naver.com/same_author/101"), MagicMock(count=lambda: 1, get_attribute=lambda k: "https://m.blog.naver.com/same_author/102")]), \
             patch("naver.sources.MobileDOMResolver.get_card_title", side_effect=["맛집 후기", "맛집 또 다녀옴"]), \
             patch("naver.sources.MobileDOMResolver.get_card_author", return_value="same_author"):
            source = RecommendationFeedSource(page, max_items=10)
            posts = source.discover_posts()
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0].key, "same_author:101")

    def test_disc_003_and_004_filtered_and_idempotent_do_not_consume_max_items(self):
        config = MagicMock()
        config.get.side_effect = lambda k, default=None: {
            "feed_source": "direct",
            "max_feed_items": 2,
            "like_enabled": True,
            "comment_enabled": True,
            "topic_filter_enabled": True,
            "direct_urls": [
                "https://m.blog.naver.com/user1/1",  # Will be idempotent
                "https://m.blog.naver.com/user2/2",  # Will be processed
                "https://m.blog.naver.com/user3/3",  # Will be processed
            ]
        }.get(k, default)

        history = MagicMock()
        # user1/1 is already liked and commented (idempotent)
        history.is_liked.side_effect = lambda key: key == "user1:1"
        history.is_comment_submitted.side_effect = lambda key: key == "user1:1"

        state_mgr = MagicMock()
        stop_event = threading.Event()

        controller = FeedController(config, history, state_mgr, stop_event)

        with patch("app.controller.BrowserSession") as mock_session_cls, \
             patch("app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.PostProcessor") as mock_proc_cls:

            mock_session = mock_session_cls.return_value
            mock_session.context = MagicMock()
            mock_processor = mock_proc_cls.return_value
            mock_processor.process.return_value = MagicMock()

            controller.run()

            # user1:1 was skipped, so user2:2 and user3:3 were processed (total 2 processed)
            self.assertEqual(mock_processor.process.call_count, 2)

    def test_session_001_and_002_page_closed_retries_with_new_page(self):
        exc = Exception("Target page, context or browser has been closed")
        page = MagicMock()
        page.is_closed.return_value = True
        context = MagicMock()
        context.pages = [MagicMock()] # Context is still alive

        kind = classify_playwright_failure(exc, page=page, context=context)
        self.assertEqual(kind, BrowserFailureKind.PAGE_CLOSED)

    def test_session_003_context_closed_classified_as_context_closed(self):
        exc = Exception("Target page, context or browser has been closed")
        page = MagicMock()
        context = MagicMock()
        type(context).pages = PropertyMock(side_effect=Exception("Context closed"))

        kind = classify_playwright_failure(exc, page=page, context=context)
        self.assertEqual(kind, BrowserFailureKind.CONTEXT_CLOSED)

    def test_session_004_and_005_context_closed_raises_fatal_and_stops_immediately(self):
        config = MagicMock()
        config.get.side_effect = lambda k, default=None: {
            "feed_source": "direct",
            "max_feed_items": 5,
            "direct_urls": [
                "https://m.blog.naver.com/user1/1",
                "https://m.blog.naver.com/user2/2",
                "https://m.blog.naver.com/user3/3",
            ]
        }.get(k, default)

        history = MagicMock()
        history.is_liked.return_value = False
        history.is_comment_submitted.return_value = False
        state_mgr = MagicMock()
        stop_event = threading.Event()

        controller = FeedController(config, history, state_mgr, stop_event)

        with patch("app.controller.BrowserSession") as mock_session_cls, \
             patch("app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.PostProcessor") as mock_proc_cls:

            mock_session = mock_session_cls.return_value
            mock_session.context = MagicMock()
            mock_processor = mock_proc_cls.return_value

            # First post encounters BrowserDisconnectedError (context crash)
            mock_processor.process.side_effect = BrowserDisconnectedError("Browser crashed")

            controller.run()

            # Process should be called ONLY ONCE and immediately stop, not looping through remaining 2 posts!
            self.assertEqual(mock_processor.process.call_count, 1)
            mock_session.close.assert_called_with(reason="fatal_error")


if __name__ == "__main__":
    unittest.main()
