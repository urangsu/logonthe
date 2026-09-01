import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.models import (
    FeedPost,
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
from app.errors import StopRequestedException
from browser.session import WaitInterruptionReason, interruptible_wait
from services.pacing import PacingService, PacingKind, PacingResult
from services.clipboard_bridge import ClipboardCommandBridge
from services.gemini_extension_bridge import GeminiCommand, GeminiExtensionBridge, GeminiResult, GeminiResultStatus


class TestSkipPipelineContract(unittest.TestCase):
    """
    SKIP-001 ~ SKIP-007 Contract Test Suite:
    스킵(SKIP)이 작업 중지(STOP)로 오작동하지 않고 각 단계별로 안전하게 현재 단계/글만 넘기며
    전체 실행 루프가 정상 지속되는지 엄격 검증
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
            "random_pause_enabled": False,
            "like_count_skip_threshold": 999,
            "daily_visitor_skip_threshold": 10000,
        }
        from app.models import FeedSourceType
        self.post = FeedPost(
            key="test_blog:12345",
            url="https://m.blog.naver.com/test_blog/12345",
            title="테스트 포스팅",
            author="test_author",
            source=FeedSourceType.NEIGHBOR
        )
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False

    def test_skip_001_pre_like_pacing_skips_current_post_without_stopping_run(self):
        """SKIP-001: pre_like pacing 중 skip -> 현재 글만 skip, StopRequestedException 미발생"""
        stop_event = threading.Event()
        skip_event = threading.Event()

        pacing = PacingService(self.config, stop_event=stop_event, skip_event=skip_event)

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            pacing_service=pacing,
            stop_event=stop_event,
            skip_event=skip_event,
        )

        with patch("app.processor.TargetPostGuard.verify"), \
             patch("naver.discovery.topic_filter.DiscoveryTopicFilter.evaluate") as mock_eval, \
             patch.object(pacing, "wait_page_settle", return_value=PacingResult(PacingKind.PAGE_SETTLE, 0.0)), \
             patch.object(pacing, "wait_pre_like", return_value=PacingResult(PacingKind.PRE_LIKE, 0.1, WaitInterruptionReason.SKIPPED)):
            mock_eval.return_value = MagicMock(allowed=True)
            res = processor.process(self.mock_page, self.post)

        # Should skip gracefully without raising StopRequestedException
        self.assertEqual(res.like_result.error, "user_skipped")
        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)

    def test_skip_002_post_like_pacing_preserves_like_checkpoint_and_skips_comment(self):
        """SKIP-002: post_like pacing 중 skip -> Like checkpoint 보존 + 댓글 단계만 skip"""
        stop_event = threading.Event()
        skip_event = threading.Event()

        pacing = PacingService(self.config, stop_event=stop_event, skip_event=skip_event)

        like_checkpoint_mock = MagicMock()
        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            pacing_service=pacing,
            stop_event=stop_event,
            skip_event=skip_event,
            on_like_committed=like_checkpoint_mock,
        )

        from services.like_transaction import LikeConfidence
        with patch("app.processor.TargetPostGuard.verify"), \
             patch("naver.discovery.topic_filter.DiscoveryTopicFilter.evaluate") as mock_eval, \
             patch("app.processor.LikeTransactionService.resolve_like_state") as mock_state, \
             patch("app.processor.LikeEligibilityService.evaluate") as mock_elig, \
             patch("app.processor.LikeTransactionService.execute_like_transaction") as mock_tx, \
             patch.object(pacing, "wait_page_settle", return_value=PacingResult(PacingKind.PAGE_SETTLE, 0.0)), \
             patch.object(pacing, "wait_pre_like", return_value=PacingResult(PacingKind.PRE_LIKE, 0.0)), \
             patch.object(pacing, "wait_post_like", return_value=PacingResult(PacingKind.POST_LIKE, 0.1, WaitInterruptionReason.SKIPPED)):
            
            mock_eval.return_value = MagicMock(allowed=True)
            mock_state.return_value = MagicMock(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)
            mock_elig.return_value = MagicMock(eligible=True, like_count=10, daily_visitors=500)
            mock_tx.return_value = LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)

            res = processor.process(self.mock_page, self.post)

        # Like committed successfully and recorded in checkpoint
        like_checkpoint_mock.assert_called_once()
        self.assertTrue(res.like_result.action_taken)
        self.assertEqual(res.like_result.state_after, LikeState.LIKED)
        # Comment skipped cleanly
        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)

    def test_skip_003_next_post_pacing_wakes_delay_immediately_run_continues(self):
        """SKIP-003: next_post pacing 중 skip -> delay만 즉시 깨우고 다음 글 루프는 중단되지 않음"""
        stop_event = threading.Event()
        skip_event = threading.Event()

        pacing = PacingService(self.config, stop_event=stop_event, skip_event=skip_event)

        # Trigger skip
        skip_event.set()
        p_res = pacing.wait_next_post()

        # Result should be skipped, not stopped, and interrupted property must be False
        self.assertTrue(p_res.skipped)
        self.assertFalse(p_res.stopped)
        self.assertFalse(p_res.interrupted)
        self.assertEqual(p_res.reason, WaitInterruptionReason.SKIPPED)

    def test_skip_004_waiting_user_action_skips_post_immediately(self):
        """SKIP-004: 댓글 초안 검토(WAITING_USER) 중 skip -> 즉시 UserAction.SKIP 반환"""
        from naver.interaction import CommentInteractionService, UserAction
        stop_event = threading.Event()
        skip_event = threading.Event()

        skip_event.set()
        with patch("naver.interaction.ensure_page_alive"):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                skip_event=skip_event,
                post_key="test_blog:12345"
            )
        self.assertEqual(action, UserAction.SKIP)

    def test_skip_005_gemini_failure_pause_skips_post(self):
        """SKIP-005: Gemini 실패 후 일시정지 중 skip -> pause 해제 및 post skip 반환"""
        stop_event = threading.Event()
        pause_event = threading.Event()
        skip_event = threading.Event()
        command_bridge = ClipboardCommandBridge()

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_browser_mode="extension_existing_chrome",
            stop_event=stop_event,
            pause_event=pause_event,
            skip_event=skip_event,
            command_bridge=command_bridge,
        )

        def trigger_skip_during_pause():
            time.sleep(0.1)
            command_bridge.send_skip_post()

        threading.Thread(target=trigger_skip_during_pause, daemon=True).start()

        with patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("app.processor.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=MagicMock(state=MagicMock(value="ABSENT"))), \
             patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(title="테스트", excerpt="본문")), \
             patch.object(processor, "gemini_extension_bridge", None):
            
            res = processor.process(self.mock_page, self.post)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)

    def test_skip_006_gemini_generation_wait_cancels_immediately_on_skip_event(self):
        """SKIP-006: Gemini 생성 중 skip_event 수신 시 최대 70초 지연 없이 즉시 반환"""
        bridge = GeminiExtensionBridge()
        now = time.time()
        cmd = GeminiCommand(
            request_id="req_skip_test",
            post_key="post:1",
            navigation_version=1,
            prompt="테스트 프롬프트",
            created_at=now,
            deadline_at=now + 60.0
        )
        bridge.publish(cmd)

        skip_event = threading.Event()
        threading.Timer(0.1, skip_event.set).start()

        start_t = time.monotonic()
        res = bridge.wait_for_result(cmd, timeout=30.0, skip_event=skip_event)
        elapsed = time.monotonic() - start_t

        self.assertIsNone(res)
        self.assertLess(elapsed, 1.0, "Skip event should immediately abort waiting for result")

    def test_skip_007_stale_skip_events_cleared_at_next_post_entry(self):
        """SKIP-007: 이전 글에서 남은 stale skip_event/command가 다음 글 처리 시작 시 안전하게 초기화됨"""
        skip_event = threading.Event()
        skip_event.set()
        command_bridge = ClipboardCommandBridge()
        command_bridge.send_skip_post("old_post:1")

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=False,
            skip_event=skip_event,
            command_bridge=command_bridge,
        )

        with patch("app.processor.TargetPostGuard.verify"), \
             patch("naver.discovery.topic_filter.DiscoveryTopicFilter.evaluate", return_value=MagicMock(allowed=True)):
            processor.process(self.mock_page, self.post)

        # After process starts, skip_event should have been cleared for this post
        self.assertFalse(skip_event.is_set())
        self.assertIsNone(command_bridge.pop_command())


if __name__ == "__main__":
    unittest.main()
