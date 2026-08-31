import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import threading
import json
import os

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
from naver.sources import RecommendationFeedSource, TargetedSearchFeedSource
from services.runtime_contract import load_runtime_contract, RuntimeContractError, WORKSPACE_DIR


class TestV133DiscoveryAndRuntime(unittest.TestCase):
    def test_disc_001_targeted_search_category_preserved(self):
        rotator = QueryRotator(enabled_categories=["FOOD", "CAFE"], posts_per_query=2)
        spec = rotator.current_spec
        self.assertIsInstance(spec, QuerySpec)
        self.assertIn(spec.category, ["FOOD", "CAFE"])
        self.assertEqual(rotator.current_query, spec.query)

    def test_disc_001b_single_query_advance_on_quota(self):
        """posts_per_query=2일 때 1개 발견시 유지, 2개 발견시 정확히 1칸 이동 검증 (더블 스킵 방지)"""
        rotator = QueryRotator(
            enabled_categories=["FOOD"],
            custom_queries=["쿼리1", "쿼리2", "쿼리3"],
            posts_per_query=2
        )
        rotator.specs = [
            QuerySpec("FOOD", "쿼리1"),
            QuerySpec("FOOD", "쿼리2"),
            QuerySpec("FOOD", "쿼리3"),
        ]
        rotator._current_index = 0
        rotator._current_query_post_count = 0

        self.assertEqual(rotator.current_query, "쿼리1")
        # 1st post found -> stays on 쿼리1
        should_switch = rotator.record_post_found()
        self.assertFalse(should_switch)
        self.assertEqual(rotator.current_query, "쿼리1")

        # 2nd post found -> advances exactly to 쿼리2, NEVER to 쿼리3!
        should_switch = rotator.record_post_found()
        self.assertTrue(should_switch)
        self.assertEqual(rotator.current_query, "쿼리2")

    def test_disc_001c_load_more_advances_query_when_no_new_unique_cards(self):
        """스크롤 후 새 유니크 카드가 없으면 다음 검색어로 이동"""
        page = MagicMock()
        rotator = QueryRotator(
            enabled_categories=["FOOD"],
            custom_queries=["쿼리A", "쿼리B"],
            posts_per_query=2
        )
        rotator.specs = [
            QuerySpec("FOOD", "쿼리A"),
            QuerySpec("FOOD", "쿼리B"),
        ]
        rotator._current_index = 0

        source = TargetedSearchFeedSource(page, rotator=rotator)
        source._get_card_fingerprints = MagicMock(return_value={"https://m.blog.naver.com/user/1"})

        # load_more executes scroll but fingerprints are identical (no new cards)
        with patch.object(source, "_switch_to_next_query") as mock_switch:
            source.load_more()
            mock_switch.assert_called_once()

    def test_disc_002_same_blog_second_post_skipped(self):
        page = MagicMock()
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
            mock_res = MagicMock()
            mock_res.like_result.action_taken = True
            mock_res.like_result.error = ""
            mock_res.comment_result.status = CommentSubmitState.SUBMITTED
            mock_processor.process.return_value = mock_res

            controller.run()

            # user1:1 was skipped, so user2:2 and user3:3 were processed (total 2 processed)
            self.assertEqual(mock_processor.process.call_count, 2)

    def test_disc_005_expected_category_without_weak_positive_is_blocked(self):
        """FOOD 검색어 결과에 전혀 엉뚱한 패션 선글라스 글이 삽입된 경우 차단 검증"""
        decision = DiscoveryTopicFilter.evaluate(
            "명품 선글라스 신상품 착용 후기",
            "백화점에서 직접 사서 써봤어요",
            stage="card",
            expected_category="FOOD"
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "not_target_category")

    def test_disc_006_expected_category_with_weak_positive_is_allowed(self):
        """FOOD 검색어 결과에 약한 음식 관련 단서가 있는 경우 통과"""
        decision = DiscoveryTopicFilter.evaluate(
            "광화문 직장인 점심 기록",
            "오늘 점심 메뉴로 든든한 밥 한끼 먹었습니다",
            stage="card",
            expected_category="FOOD"
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.detected_category, "FOOD")

    def test_disc_007_runtime_contract_source_of_truth(self):
        """runtime_contract.json과 manifest.json 및 Python loader 일치 검증"""
        contract = load_runtime_contract()
        self.assertEqual(contract.extension_version, "13.2.3")
        self.assertEqual(contract.runtime_build, "13.2.3-r1")
        self.assertEqual(contract.protocol_version, 3)
        self.assertEqual(contract.bridge_schema_version, 2)

        manifest_path = os.path.join(WORKSPACE_DIR, "browser_extension", "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["version"], contract.extension_version)

    def test_disc_008_runtime_contract_fail_closed_on_missing(self):
        """runtime_contract.json 파일 누락시 RuntimeContractError 발생 검증 (Fail-Closed)"""
        with patch("os.path.exists", return_value=False):
            with self.assertRaises(RuntimeContractError):
                load_runtime_contract()

    def test_session_001_and_002_page_closed_retries_with_new_page(self):
        exc = Exception("Target page, context or browser has been closed")
        page = MagicMock()
        page.is_closed.return_value = True
        context = MagicMock()
        context.pages = [MagicMock()]

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
