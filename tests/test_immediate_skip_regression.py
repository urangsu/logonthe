import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.models import (
    FeedPost,
    FeedSourceType,
    PostActionPlan,
    PostProcessResult,
    LikeProcessResult,
    CommentProcessResult,
    CommentSubmitState,
    LikeState,
    WorkerCommand,
    WorkerCommandType,
)
from app.processor import PostProcessor
from browser.session import WaitInterruptionReason
from services.pacing import PacingService, PacingKind, PacingResult
from services.clipboard_bridge import ClipboardCommandBridge
from naver.interaction import CommentInteractionService, UserAction


class TestImmediateSkipRegression(unittest.TestCase):
    """
    SKIP-IMMEDIATE Regression Test Suite:
    '다음 글 스킵' 클릭 시 지연(pacing delay, random pause, 5s layer poll 등) 없이
    즉각적으로 스킵되고 다음 글 루프로 이어지는지 검증
    """

    def setUp(self):
        self.config = {
            "pacing_enabled": True,
            "pre_like_delay_min": 5.0,
            "pre_like_delay_max": 10.0,
            "post_like_delay_min": 2.0,
            "post_like_delay_max": 5.0,
            "next_post_delay_min": 2.0,
            "next_post_delay_max": 5.0,
            "random_pause_enabled": True,
            "random_pause_chance": 1.0,  # 100% to test bypass
            "random_pause_min": 10.0,
            "random_pause_max": 20.0,
        }
        self.post = FeedPost(
            key="test_blog:12345",
            url="https://m.blog.naver.com/test_blog/12345",
            title="테스트 포스팅",
            author="test_author",
            source=FeedSourceType.NEIGHBOR,
        )
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False
        self.mock_page.main_frame = self.mock_page

    def test_pacing_service_returns_immediately_when_skip_event_is_set(self):
        """pacing_service의 모든 대기 메서드가 skip_event set 상태에서 0초 즉시 반환하는지 검증"""
        skip_event = threading.Event()
        skip_event.set()
        pacing = PacingService(self.config, skip_event=skip_event)

        start = time.monotonic()
        res_action = pacing.wait_action()
        res_settle = pacing.wait_page_settle()
        res_pre_like = pacing.wait_pre_like()
        res_post_like = pacing.wait_post_like()
        res_next_post = pacing.wait_next_post()
        res_pause = pacing.maybe_pause()
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.2, "Pacing delays must exit immediately when skip_event is set")
        self.assertTrue(res_action.skipped)
        self.assertTrue(res_settle.skipped)
        self.assertTrue(res_pre_like.skipped)
        self.assertTrue(res_post_like.skipped)
        self.assertTrue(res_next_post.skipped)
        self.assertIsNotNone(res_pause)
        self.assertTrue(res_pause.skipped)

    def test_open_comment_layer_aborts_immediately_on_skip_event(self):
        """open_comment_layer가 5초 동안 polling하지 않고 skip_event 감지 즉시 user_skipped 반환"""
        skip_event = threading.Event()
        skip_event.set()

        start = time.monotonic()
        with patch("naver.interaction.ensure_page_alive"):
            ok, reason = CommentInteractionService.open_comment_layer(
                self.mock_page,
                skip_event=skip_event
            )
        elapsed = time.monotonic() - start

        self.assertFalse(ok)
        self.assertEqual(reason, "user_skipped")
        self.assertLess(elapsed, 0.2, "open_comment_layer must abort immediately on skip_event")

    def test_wait_for_user_action_sets_skip_event_on_skip_action(self):
        """wait_for_user_action에서 SKIP / CLOSED 수신 시 skip_event를 set하여 다음 글 페이싱까지 즉시 생략되게 함"""
        skip_event = threading.Event()
        self.assertFalse(skip_event.is_set())

        # Simulate browser returning 'SKIP'
        self.mock_page.evaluate.return_value = ["SKIP", False]

        with patch("naver.interaction.ensure_page_alive"), \
             patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                skip_event=skip_event,
                post_key="test_blog:12345"
            )

        self.assertEqual(action, UserAction.SKIP)
        self.assertTrue(skip_event.is_set(), "skip_event must be set so subsequent next-post pacing is skipped")

    def test_wait_for_user_action_sets_skip_event_on_command_bridge_skip(self):
        """UI에서 command_bridge로 전달된 SKIP_POST 명령 수신 시 skip_event를 set하고 SKIP 반환"""
        skip_event = threading.Event()
        bridge = ClipboardCommandBridge()
        bridge.send_skip_post("test_blog:12345")

        self.mock_page.evaluate.return_value = [None, False]

        with patch("naver.interaction.ensure_page_alive"), \
             patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                command_bridge=bridge,
                skip_event=skip_event,
                post_key="test_blog:12345"
            )

        self.assertEqual(action, UserAction.SKIP)
        self.assertTrue(skip_event.is_set())

    def test_controller_bypasses_pacing_and_pause_when_user_skipped(self):
        """글 처리 결과가 user_skipped인 경우 controller가 wait_next_post 및 maybe_pause를 전혀 호출하지 않고 즉시 이동"""
        from app.controller import FeedController
        from app.state import StateManager

        history_mock = MagicMock()
        state_mgr = StateManager()
        skip_event = threading.Event()

        config = dict(self.config)
        config["max_feed_items"] = 1
        config["feed_source"] = "neighbor"

        controller = FeedController(
            config=config,
            history=history_mock,
            state_mgr=state_mgr,
            skip_event=skip_event,
        )

        pacing_mock = MagicMock()
        controller.pacing = pacing_mock

        # Setup 1 post source
        feed_source_mock = MagicMock()
        feed_source_mock.discover_posts.side_effect = [[self.post], []]
        feed_source_mock.is_exhausted.return_value = True

        session_mock = MagicMock()
        session_mock.is_alive.return_value = True
        session_mock.get_feed_page.return_value = self.mock_page
        session_mock.get_detail_page.return_value = self.mock_page
        controller.session = session_mock

        history_mock.is_liked.return_value = False
        history_mock.is_comment_submitted.return_value = False
        history_mock.is_comment_unconfirmed.return_value = False
        history_mock.get_recent_submitted_comments.return_value = []
        history_mock.get_unconfirmed_posts.return_value = {}

        with patch("app.controller.BrowserSession", return_value=session_mock), \
             patch("app.controller.NeighborFeedSource", return_value=feed_source_mock), \
             patch("app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), \
             patch("app.controller.PostProcessor") as mock_proc_cls:

            proc_instance = mock_proc_cls.return_value
            proc_instance._processed_post_keys = set()
            # PostProcessor returns user_skipped result
            proc_instance.process.return_value = PostProcessResult(
                post=self.post,
                like_result=LikeProcessResult(action_taken=False, state_after=LikeState.NOT_LIKED),
                comment_result=CommentProcessResult(status=CommentSubmitState.SKIPPED, error="user_skipped")
            )

            controller._run()

        # Pacing wait_next_post and maybe_pause must NOT have been called!
        pacing_mock.wait_next_post.assert_not_called()
        pacing_mock.maybe_pause.assert_not_called()


if __name__ == "__main__":
    unittest.main()
