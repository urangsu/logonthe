import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import threading
import json
import os
import time
import urllib.request

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
from services.gemini_extension_bridge import (
    GeminiBridgeHTTPServer,
    GeminiCommand,
    GeminiExtensionBridge,
    GeminiResult,
    GeminiResultStatus,
)


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
        should_switch = rotator.record_post_found()
        self.assertFalse(should_switch)
        self.assertEqual(rotator.current_query, "쿼리1")

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
                "https://m.blog.naver.com/user1/1",
                "https://m.blog.naver.com/user2/2",
                "https://m.blog.naver.com/user3/3",
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
            self.assertEqual(mock_processor.process.call_count, 2)

    def test_disc_005_expected_category_without_weak_positive_is_blocked(self):
        decision = DiscoveryTopicFilter.evaluate(
            "명품 선글라스 신상품 착용 후기",
            "백화점에서 직접 사서 써봤어요",
            stage="card",
            expected_category="FOOD"
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "not_target_category")

    def test_disc_006_expected_category_with_weak_positive_is_allowed(self):
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
        self.assertEqual(contract.runtime_build, "13.2.3-r15")
        self.assertEqual(contract.protocol_version, 3)
        self.assertEqual(contract.bridge_schema_version, 2)

        manifest_path = os.path.join(WORKSPACE_DIR, "browser_extension", "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["version"], contract.extension_version)

    def test_disc_008_runtime_contract_fail_closed_on_missing(self):
        with patch("os.path.exists", return_value=False):
            with self.assertRaises(RuntimeContractError):
                load_runtime_contract()

    def test_disc_009_popup_direct_foreground_probe_static_check(self):
        popup_js_path = os.path.join(WORKSPACE_DIR, "browser_extension", "popup.js")
        with open(popup_js_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("fetch(`${BASE}/v1/status`", content)
        self.assertIn("directForegroundProbe()", content)
        self.assertIn("btnRecover", content)

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
            mock_processor.process.side_effect = BrowserDisconnectedError("Browser crashed")
            controller.run()
            self.assertEqual(mock_processor.process.call_count, 1)
            mock_session.close.assert_called_with(reason="fatal_error")

    def test_disc_010_recommendation_source_gourmet_priority_and_fallback(self):
        page = MagicMock()
        page.evaluate.side_effect = [
            ["card1", "card2"],
            {"status": "clicked", "text": "맛집"},
            {"active": True, "cardsChanged": True, "verified": True},
        ]
        source = RecommendationFeedSource(page, max_items=5)
        self.assertEqual(source.preferred_category, "맛집")
        self.assertEqual(source.fallback_category, "푸드")
        source.open()
        self.assertFalse(source.is_exhausted())

        page.evaluate.side_effect = [
            ["card1", "card2"],
            {"status": "not_found"},
            {"status": "clicked", "text": "푸드"},
            {"active": True, "cardsChanged": True, "verified": True},
        ]
        source_fallback = RecommendationFeedSource(page, max_items=5)
        source_fallback.open()
        self.assertFalse(source_fallback.is_exhausted())

        page.evaluate.side_effect = [
            ["card1", "card2"],
            {"status": "not_found"},
            {"status": "not_found"},
        ]
        source_fail = RecommendationFeedSource(page, max_items=5)
        source_fail.open()
        self.assertTrue(source_fail.is_exhausted())

    def test_gem_r15_py_001_generation_timeout_accepted_within_acceptance_deadline(self):
        """GEM-R15-PY-001: generation deadline 초과 후 acceptance deadline 이내에 도착한 timeout 결과는 late_result 거절 없이 accepted 됨"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r15")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r15", 3, 2)

        now = time.time()
        # Generation deadline is in the past, while acceptance deadline is in the future
        cmd = GeminiCommand(
            request_id="req_timeout_test_01",
            post_key="post:timeout_test",
            navigation_version=1,
            prompt="생성 타임아웃 테스트 프롬프트",
            created_at=now - 12.0,
            deadline_at=now + 7.0,
            deadline_at_ms=int((now + 7.0) * 1000),
            created_at_ms=int((now - 12.0) * 1000),
            generation_deadline_at=now - 2.0,
            generation_deadline_at_ms=int((now - 2.0) * 1000),
            acceptance_deadline_at=now + 7.0,
            acceptance_deadline_at_ms=int((now + 7.0) * 1000),
            timeout_seconds=10.0,
            delivery_reserve_seconds=9.0
        )
        bridge.publish(cmd)
        bridge.claim_command(cmd.request_id, "tab_test_01")

        # Content reports generation timeout
        res = GeminiResult(
            request_id=cmd.request_id,
            post_key=cmd.post_key,
            navigation_version=cmd.navigation_version,
            status="timeout",
            text="",
            error="response_stalled",
            delivery_id=f"{cmd.request_id}:timeout:0:h_test"
        )
        accepted, reason = bridge.submit_result(res)
        self.assertTrue(accepted, f"Result should be accepted before acceptance deadline, got {reason}")
        self.assertEqual(reason, "accepted")
        self.assertNotEqual(reason, "late_result")

    def test_gem_r15_py_002_idempotent_failover_delivery(self):
        """GEM-R15-PY-002: Primary와 Failover 경로로 동일 delivery_id 결과가 2회 전달되어도 충돌 없이 already_accepted 처리"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r15")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r15", 3, 2)

        cmd = GeminiCommand.create("post:idempotent_test", 1, "중복 전달 멱등성 테스트", timeout_seconds=30.0)
        bridge.publish(cmd)
        bridge.claim_command(cmd.request_id, "tab_test_02")

        delivery_id = f"{cmd.request_id}:completed:15:h_abc"
        res_primary = GeminiResult(
            request_id=cmd.request_id,
            post_key=cmd.post_key,
            navigation_version=cmd.navigation_version,
            status="completed",
            text="테스트 생성 댓글 내용",
            error="",
            delivery_id=delivery_id
        )
        # First delivery (Primary via Background)
        acc1, reason1 = bridge.submit_result(res_primary)
        self.assertTrue(acc1)
        self.assertEqual(reason1, "accepted")

        # Second delivery (Failover direct HTTP with same delivery_id and payload)
        res_failover = GeminiResult(
            request_id=cmd.request_id,
            post_key=cmd.post_key,
            navigation_version=cmd.navigation_version,
            status="completed",
            text="테스트 생성 댓글 내용",
            error="",
            delivery_id=delivery_id
        )
        acc2, reason2 = bridge.submit_result(res_failover)
        self.assertTrue(acc2)
        self.assertEqual(reason2, "already_accepted")

    def test_gem_r15_int_001_extension_result_http_submission(self):
        """GEM-R15-INT-001: 실제 extension result payload -> HTTP bridge (/v1/result) -> submit_result 통합 검증"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r15")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r15", 3, 2)

        # Find an open port and start GeminiBridgeHTTPServer
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        server = GeminiBridgeHTTPServer(bridge, host="127.0.0.1", port=free_port)
        server.start()
        try:
            cmd = GeminiCommand.create("post:int_001", 1, "통합 테스트 프롬프트", timeout_seconds=10.0)
            bridge.publish(cmd)
            bridge.claim_command(cmd.request_id, "tab_int_01")

            payload = {
                "requestId": cmd.request_id,
                "postKey": cmd.post_key,
                "navigationVersion": cmd.navigation_version,
                "deliveryId": f"{cmd.request_id}:completed:10:h_int",
                "status": "completed",
                "text": "통합 완료 텍스트",
                "error": ""
            }
            req = urllib.request.Request(
                f"http://127.0.0.1:{free_port}/v1/result",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(body.get("accepted"))
                self.assertEqual(body.get("reason"), "accepted")

            # Check that wait_for_result immediately returns this result
            result = bridge.wait_for_result(cmd, timeout=1.0)
            self.assertIsNotNone(result)
            self.assertEqual(result.status, GeminiResultStatus.COMPLETED)
            self.assertEqual(result.text, "통합 완료 텍스트")
            self.assertEqual(result.delivery_id, f"{cmd.request_id}:completed:10:h_int")
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
