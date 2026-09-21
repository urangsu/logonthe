import unittest
from unittest.mock import MagicMock, patch
import threading

from app.models import (
    FeedPost, FeedSourceType, PostProcessResult, LikeProcessResult, CommentProcessResult,
    CommentSubmitState, LikeState, PostActionPlan
)
from app.controller import FeedController
from app.processor import PostProcessor
from app.state import StateManager, FeedState
from services.relationship_resolver import RelationshipType, RelationshipResolution
from services.pacing import PacingResult


class TestV133RuntimeErrorAndIsolation(unittest.TestCase):
    def test_post_processor_state_isolation_between_posts(self):
        """Post 1 처리 중 발생한 상태/오류가 Post 2로 누수되지 않고 격리되는지 검증"""
        processor = PostProcessor(
            config={"ai_prompt_version": "3.0.0-grounded-human"}
        )

        post1 = FeedPost(key="post_1", source=FeedSourceType.RECOMMENDATION, url="https://m.blog.naver.com/u/1", blog_id="u", title="글 1")
        post2 = FeedPost(key="post_2", source=FeedSourceType.RECOMMENDATION, url="https://m.blog.naver.com/u/2", blog_id="u", title="글 2")

        # Post 1 실패 모의
        with patch.object(processor, "_process_internal", side_effect=ValueError("Post 1 crash in comment_generation")):
            processor.current_stage = "comment_generation"
            processor.current_request_id = "req-post1-123"
            with self.assertRaises(ValueError):
                processor.process(MagicMock(), post1)

        # Post 2 처리 진입 시 _process_internal 내부에서 current_stage="start", current_post_key="post_2", current_result가 새로 생성되는지 직접 검증
        mock_detail_page = MagicMock()
        mock_detail_page.goto.side_effect = RuntimeError("Detail page navigation fail")
        try:
            processor._process_internal(mock_detail_page, post2)
        except Exception:
            pass

        self.assertEqual(processor.current_stage, "start")
        self.assertEqual(processor.current_post_key, "post_2")
        self.assertIsNotNone(processor.current_result)
        self.assertEqual(processor.current_result.post.key, "post_2")

    def _mock_pacing(self, controller):
        mock_p = MagicMock()
        mock_wait = MagicMock()
        mock_wait.stopped = False
        mock_wait.skipped = False
        mock_p.wait_next_post.return_value = mock_wait
        mock_p.maybe_pause.return_value = None
        mock_p.reset = MagicMock()
        controller.pacing = mock_p

    def test_like_state_preserved_when_comment_fails(self):
        """공감은 성공(LIKED)했으나 댓글 처리 중 예외 발생 시 공감 결과가 UNKNOWN으로 유실되지 않고 보존되는지 검증"""
        config_mock = MagicMock()
        config_mock.get.side_effect = lambda k, default=None: {
            "source": "neighbor",
            "max_items": 1,
            "like_enabled": True,
            "comment_enabled": True,
            "neighbor_mutual_only": False,
            "neighbor_like_sweep_mode": False,
        }.get(k, default)
        config_mock.load.return_value = config_mock

        history_mock = MagicMock()
        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False

        state_mgr_mock = MagicMock()

        controller = FeedController(
            config=config_mock,
            history=history_mock,
            state_mgr=state_mgr_mock,
        )
        self._mock_pacing(controller)

        post = FeedPost(key="post_like_ok_comment_err", url="https://blog.naver.com/u/1", blog_id="u", source=FeedSourceType.RECOMMENDATION)

        source_mock = MagicMock()
        source_mock.discover_posts.return_value = [post]
        source_mock.is_exhausted.return_value = True

        processor_mock = MagicMock()
        # Like 성공 상태를 current_result에 설정한 후 예외 발생 모의
        preserved_like_result = LikeProcessResult(
            state_before=LikeState.NOT_LIKED,
            action_taken=True,
            state_after=LikeState.LIKED,
        )
        processor_mock.current_stage = "comment_injection"
        processor_mock.current_post_key = post.key
        processor_mock.current_request_id = "req-fail-789"
        processor_mock.current_result = PostProcessResult(
            post=post,
            like_result=preserved_like_result,
        )

        def mock_process_crash(detail_page, p, action_plan=None):
            raise RuntimeError("Comment injection failed after like was committed")

        processor_mock.process.side_effect = mock_process_crash

        session_mock = MagicMock()

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.RecommendationFeedSource", return_value=source_mock), \
             patch("app.controller.PostProcessor", return_value=processor_mock):
            controller.run()

        # history.record_result에 전달된 결과 검증
        self.assertEqual(history_mock.record_result.call_count, 1)
        recorded_res = history_mock.record_result.call_args[0][0]
        self.assertEqual(recorded_res.post.key, "post_like_ok_comment_err")
        self.assertEqual(recorded_res.like_result.state_after, LikeState.LIKED)
        self.assertTrue(recorded_res.like_result.action_taken)
        self.assertEqual(recorded_res.comment_result.status, CommentSubmitState.FAILED)

    def test_consecutive_identical_error_circuit_breaker(self):
        """동일 예외가 3회 연속 발생 시 회로 차단기가 발동하여 루프를 즉시 중단하는지 검증"""
        config_mock = MagicMock()
        config_mock.get.side_effect = lambda k, default=None: {
            "source": "recommend",
            "max_items": 10,
            "like_enabled": True,
            "comment_enabled": True,
            "neighbor_mutual_only": False,
            "neighbor_like_sweep_mode": False,
        }.get(k, default)
        config_mock.load.return_value = config_mock

        history_mock = MagicMock()
        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False

        state_mgr_mock = MagicMock()

        controller = FeedController(
            config=config_mock,
            history=history_mock,
            state_mgr=state_mgr_mock,
        )
        self._mock_pacing(controller)

        posts = [
            FeedPost(key=f"p_{i}", url=f"https://blog.naver.com/u/{i}", blog_id="u", source=FeedSourceType.RECOMMENDATION)
            for i in range(1, 10)
        ]

        source_mock = MagicMock()
        source_mock.discover_posts.return_value = posts
        source_mock.is_exhausted.return_value = False

        processor_mock = MagicMock()
        processor_mock.current_stage = "start"
        processor_mock.current_request_id = "none"
        processor_mock.current_result = None

        # 3회 연속 동일 에러 발생
        processor_mock.process.side_effect = RuntimeError("DOM selector failure timeout")

        session_mock = MagicMock()

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.RecommendationFeedSource", return_value=source_mock), \
             patch("app.controller.PostProcessor", return_value=processor_mock):
            controller.run()

        # 정확히 3회만 호출되고 회로 차단되어야 함 (10개까지 가지 않음)
        self.assertEqual(processor_mock.process.call_count, 3)

        # StateManager에 ERROR 상태 및 회로 차단 메시지가 기록되었는지 검증
        error_updates = [
            c.kwargs for c in state_mgr_mock.update.call_args_list
            if c.kwargs.get("new_state") == FeedState.ERROR
        ]
        self.assertTrue(len(error_updates) > 0)
        self.assertIn("회로 차단", error_updates[-1].get("message", ""))

    def test_circuit_breaker_resets_on_intervening_success(self):
        """중간에 성공한 글이 있으면 연속 동일 예외 카운터가 리셋되는지 검증"""
        config_mock = MagicMock()
        config_mock.get.side_effect = lambda k, default=None: {
            "source": "recommend",
            "max_items": 4,
            "like_enabled": True,
            "comment_enabled": True,
            "neighbor_mutual_only": False,
            "neighbor_like_sweep_mode": False,
        }.get(k, default)
        config_mock.load.return_value = config_mock

        history_mock = MagicMock()
        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False

        state_mgr_mock = MagicMock()

        controller = FeedController(
            config=config_mock,
            history=history_mock,
            state_mgr=state_mgr_mock,
        )
        self._mock_pacing(controller)

        posts = [
            FeedPost(key=f"p_{i}", url=f"https://blog.naver.com/u/{i}", blog_id="u", source=FeedSourceType.RECOMMENDATION)
            for i in range(1, 5)
        ]

        source_mock = MagicMock()
        source_mock.discover_posts.return_value = posts
        source_mock.is_exhausted.return_value = True

        processor_mock = MagicMock()
        processor_mock.current_stage = "start"
        processor_mock.current_request_id = "none"
        processor_mock.current_result = None

        # p1: fail, p2: success, p3: fail, p4: fail (총 4개 모두 처리되어야 함, 연속 3회가 아니므로)
        def side_effect(detail_page, post, action_plan=None):
            if post.key == "p_2":
                return PostProcessResult(post=post)
            raise RuntimeError("Intermittent failure")

        processor_mock.process.side_effect = side_effect

        session_mock = MagicMock()

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("naver.auth_guard.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.RecommendationFeedSource", return_value=source_mock), \
             patch("app.controller.PostProcessor", return_value=processor_mock):
            controller.run()

        # 회로 차단되지 않고 4개 모두 처리됨
        self.assertEqual(processor_mock.process.call_count, 4)


if __name__ == "__main__":
    unittest.main()
