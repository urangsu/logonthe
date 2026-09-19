"""
Gemini Concurrency Pipeline Tests
Validates the speculative early Gemini generation lifecycle, order of operations,
await_ready call counts, and zero-orphan RID invariants with real GeminiExtensionBridge.
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from app.models import (
    FeedPost,
    FeedSourceType,
    CommentSubmitState,
    LikeState,
    LikeProcessResult,
    CommentProcessResult,
    UserAction,
)
from app.processor import PostProcessor
from app.run_control import StopRequestedException
from browser.session import WaitInterruptionReason
from naver.comment_guard import CommentPresenceState, CommentPresenceResult
from services.gemini_extension_bridge import (
    GeminiExtensionBridge,
    GeminiResult,
    GeminiResultStatus,
)
from services.like_transaction import LikeStateResult, LikeConfidence
from services.like_eligibility import LikeEligibility, LikeEligibilityResult
from services.pacing import PacingKind, PacingResult


class TestGeminiConcurrencyPipeline(unittest.TestCase):
    def setUp(self):
        self.config = {
            "comment_enabled": True,
            "like_enabled": True,
            "auto_comment_submit_enabled": True,
            "auto_comment_chance": 1.0,
            "gemini_web_enabled": True,
            "gemini_browser_mode": "extension_existing_chrome",
            "gemini_response_timeout": 55.0,
            "secret_comment": False,
            "ai_clipboard_enabled": False,
            "comment_style_preset": "community",
            "comment_template": "좋은 글 잘 보았습니다.",
            "skip_on_comment_failure": True,
        }
        self.post = FeedPost(
            key="concurrency:post_001",
            url="https://m.blog.naver.com/test/concurrency_001",
            source=FeedSourceType.NEIGHBOR,
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        self.mock_page = MagicMock()

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    @patch("app.processor.LikeEligibilityService.evaluate")
    @patch("app.processor.LikeTransactionService.execute_like_transaction")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.CommentInteractionService.wait_for_user_action")
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    def test_concurrency_execution_order_and_readiness_count(
        self,
        mock_set_text,
        mock_submit,
        mock_user_act,
        mock_dup_scan,
        mock_ctx,
        mock_open,
        mock_tx,
        mock_elig,
        mock_like_state,
        mock_extract,
        mock_guard,
    ):
        """
        Verify exact execution order:
        publish < pre_like < resolve_like_state < like_transaction < post_like < comment_open < duplicate_scan < wait_for_result
        and bridge.await_ready.call_count == 1 (no second preflight in comment phase).
        """
        execution_order = []

        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_like_state.side_effect = lambda page: (
            execution_order.append("resolve_like_state"),
            LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)
        )[1]
        mock_elig.return_value = LikeEligibilityResult(
            eligible=True, status=LikeEligibility.ELIGIBLE, reason="ok", like_count=10, daily_visitors=100
        )
        mock_tx.side_effect = lambda page, stop_event=None, post=None: (
            execution_order.append("like_transaction"),
            LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)
        )[1]
        mock_open.side_effect = lambda page, stop_event=None, skip_event=None: (
            execution_order.append("comment_open"),
            (True, "ok")
        )[1]
        mock_dup_scan.side_effect = lambda frame, stop_event=None: (
            execution_order.append("duplicate_scan"),
            CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        )[1]
        mock_user_act.return_value = UserAction.SKIP

        mock_bridge = MagicMock()
        mock_bridge.await_ready.return_value = MagicMock(ready=True)

        def record_publish(cmd, stop_event=None, skip_event=None):
            execution_order.append("publish")
            return True

        def record_wait_for_result(cmd, stop_event=None, skip_event=None):
            execution_order.append("wait_for_result")
            return GeminiResult(
                request_id=cmd.request_id,
                post_key=cmd.post_key,
                navigation_version=cmd.navigation_version,
                status=GeminiResultStatus.COMPLETED,
                text="서귀포 파스타 전문점이라니 정말 맛있어 보이네요! 비주얼부터 군침 돕니다.",
            )

        mock_bridge.publish.side_effect = record_publish
        mock_bridge.wait_for_result.side_effect = record_wait_for_result

        mock_pacing = MagicMock()
        mock_pacing.wait_page_settle.return_value = PacingResult(
            kind=PacingKind.PAGE_SETTLE, seconds=0.0, reason=WaitInterruptionReason.COMPLETED
        )
        mock_pacing.wait_action.return_value = PacingResult(
            kind=PacingKind.ACTION, seconds=0.0, reason=WaitInterruptionReason.COMPLETED
        )
        mock_pacing.wait_pre_like.side_effect = lambda: (
            execution_order.append("pre_like"),
            PacingResult(kind=PacingKind.PRE_LIKE, seconds=0.1, reason=WaitInterruptionReason.COMPLETED)
        )[1]
        mock_pacing.wait_post_like.side_effect = lambda: (
            execution_order.append("post_like"),
            PacingResult(kind=PacingKind.POST_LIKE, seconds=0.1, reason=WaitInterruptionReason.COMPLETED)
        )[1]

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_bridge,
            pacing_service=mock_pacing,
        )

        res = processor.process(self.mock_page, self.post)

        # 1. Verify exact chronological milestone sequence
        expected_order = [
            "publish",
            "pre_like",
            "resolve_like_state",
            "like_transaction",
            "post_like",
            "comment_open",
            "duplicate_scan",
            "wait_for_result",
        ]
        self.assertEqual(execution_order, expected_order)

        # 2. Verify readiness check count: strictly 1 call (initial pre-publish only)
        self.assertEqual(mock_bridge.await_ready.call_count, 1)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    @patch("app.processor.LikeEligibilityService.evaluate")
    @patch("app.processor.LikeTransactionService.execute_like_transaction")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.CommentInteractionService.wait_for_user_action")
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    def test_busy_bridge_during_like_does_not_block_and_no_second_preflight(
        self,
        mock_set_text,
        mock_submit,
        mock_user_act,
        mock_dup_scan,
        mock_ctx,
        mock_open,
        mock_tx,
        mock_elig,
        mock_like_state,
        mock_extract,
        mock_guard,
    ):
        """
        When Gemini is busy executing during like processing,
        the comment phase must NOT run preflight await_ready again (which would see busy or block),
        but must go directly to wait_for_result.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_like_state.return_value = LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)
        mock_elig.return_value = LikeEligibilityResult(
            eligible=True, status=LikeEligibility.ELIGIBLE, reason="ok", like_count=10, daily_visitors=100
        )
        mock_tx.return_value = LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_user_act.return_value = UserAction.SKIP

        mock_bridge = MagicMock()
        mock_bridge.await_ready.return_value = MagicMock(ready=True)
        mock_bridge.publish.return_value = True
        mock_bridge.wait_for_result.return_value = GeminiResult(
            request_id="rid_busy_test",
            post_key=self.post.key,
            navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text="서귀포 파스타 전문점이라니 정말 맛있어 보이네요! 비주얼부터 군침 돕니다.",
        )

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_bridge,
        )

        res = processor.process(self.mock_page, self.post)

        self.assertEqual(mock_bridge.await_ready.call_count, 1)
        mock_bridge.wait_for_result.assert_called_once()

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    def test_zero_orphan_with_real_bridge_on_stop_requested(self, mock_extract, mock_guard):
        """
        Using real GeminiExtensionBridge:
        When stop requested during pre-like pacing, the early command is cleanly cancelled,
        bridge._command is None, bridge._active_request_id is None, and RID is in _cancel_requests.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )

        bridge = GeminiExtensionBridge()
        bridge.record_heartbeat(
            status="ready",
            extension_version=bridge._expected_extension_version,
            content_build=bridge._expected_build_id,
            protocol_version=bridge._protocol_version_expected,
            bridge_schema_version=bridge._bridge_schema_version_expected,
        )

        mock_pacing = MagicMock()
        mock_pacing.wait_page_settle.return_value = PacingResult(
            kind=PacingKind.PAGE_SETTLE, seconds=0.0, reason=WaitInterruptionReason.COMPLETED
        )
        mock_pacing.wait_action.return_value = PacingResult(
            kind=PacingKind.ACTION, seconds=0.0, reason=WaitInterruptionReason.COMPLETED
        )
        mock_pacing.wait_pre_like.return_value = PacingResult(
            kind=PacingKind.PRE_LIKE, seconds=0.1, reason=WaitInterruptionReason.STOPPED
        )

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=bridge,
            pacing_service=mock_pacing,
        )

        with self.assertRaises(StopRequestedException):
            processor.process(self.mock_page, self.post)

        # Real bridge invariant verification
        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(bridge._command_state, "cancelled")
            self.assertGreaterEqual(len(bridge._cancel_requests), 1)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    def test_zero_orphan_with_real_bridge_on_skip_requested(self, mock_extract, mock_guard):
        """
        Using real GeminiExtensionBridge:
        When skip requested during pre-like pacing, the early command is cleanly cancelled,
        bridge._command is None, bridge._active_request_id is None, and RID is in _cancel_requests.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )

        bridge = GeminiExtensionBridge()
        bridge.record_heartbeat(
            status="ready",
            extension_version=bridge._expected_extension_version,
            content_build=bridge._expected_build_id,
            protocol_version=bridge._protocol_version_expected,
            bridge_schema_version=bridge._bridge_schema_version_expected,
        )

        mock_pacing = MagicMock()
        mock_pacing.wait_page_settle.return_value = PacingResult(
            kind=PacingKind.PAGE_SETTLE, seconds=0.0, reason=WaitInterruptionReason.COMPLETED
        )
        mock_pacing.wait_action.return_value = PacingResult(
            kind=PacingKind.ACTION, seconds=0.0, reason=WaitInterruptionReason.COMPLETED
        )
        mock_pacing.wait_pre_like.return_value = PacingResult(
            kind=PacingKind.PRE_LIKE, seconds=0.1, reason=WaitInterruptionReason.SKIPPED
        )

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=bridge,
            pacing_service=mock_pacing,
        )

        res = processor.process(self.mock_page, self.post)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(bridge._command_state, "cancelled")
            self.assertGreaterEqual(len(bridge._cancel_requests), 1)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    def test_zero_orphan_with_real_bridge_on_unexpected_exception(
        self, mock_like_state, mock_extract, mock_guard
    ):
        """
        Using real GeminiExtensionBridge:
        When an unexpected exception occurs in like processing,
        finally block cleanly cancels the early command with zero leaks.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        mock_like_state.side_effect = RuntimeError("DOM disconnected unexpectedly")

        bridge = GeminiExtensionBridge()
        bridge.record_heartbeat(
            status="ready",
            extension_version=bridge._expected_extension_version,
            content_build=bridge._expected_build_id,
            protocol_version=bridge._protocol_version_expected,
            bridge_schema_version=bridge._bridge_schema_version_expected,
        )

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=bridge,
        )

        with self.assertRaises(RuntimeError):
            processor.process(self.mock_page, self.post)

        # Invariant check: finally block cleanly cancelled command
        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(bridge._command_state, "cancelled")
            self.assertGreaterEqual(len(bridge._cancel_requests), 1)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    @patch("app.processor.LikeEligibilityService.evaluate")
    @patch("app.processor.LikeTransactionService.execute_like_transaction")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.CommentInteractionService.wait_for_user_action")
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    def test_zero_orphan_with_real_bridge_on_successful_consumption(
        self,
        mock_set_text,
        mock_submit,
        mock_user_act,
        mock_dup_scan,
        mock_ctx,
        mock_open,
        mock_tx,
        mock_elig,
        mock_like_state,
        mock_extract,
        mock_guard,
    ):
        """
        Using real GeminiExtensionBridge:
        When Gemini generation completes normally and is consumed by wait_for_result,
        bridge._command is None, bridge._active_request_id is None, and command is NOT cancelled.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_like_state.return_value = LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)
        mock_elig.return_value = LikeEligibilityResult(
            eligible=True, status=LikeEligibility.ELIGIBLE, reason="ok", like_count=10, daily_visitors=100
        )
        mock_tx.return_value = LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED)
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_user_act.return_value = UserAction.SKIP

        bridge = GeminiExtensionBridge()
        bridge.record_heartbeat(
            status="ready",
            extension_version=bridge._expected_extension_version,
            content_build=bridge._expected_build_id,
            protocol_version=bridge._protocol_version_expected,
            bridge_schema_version=bridge._bridge_schema_version_expected,
        )

        # Hook publish to immediately submit result simulating background extension
        orig_publish = bridge.publish

        def auto_reply_publish(cmd, stop_event=None, skip_event=None):
            pub_ok = orig_publish(cmd, stop_event=stop_event, skip_event=skip_event)
            if pub_ok:
                claim_ok = bridge.claim_command(cmd.request_id, claimant="mock_ext")
                if claim_ok:
                    bridge.submit_result(GeminiResult(
                        request_id=cmd.request_id,
                        post_key=cmd.post_key,
                        navigation_version=cmd.navigation_version,
                        status=GeminiResultStatus.COMPLETED,
                        text="서귀포 파스타 전문점이라니 정말 맛있어 보이네요! 비주얼부터 군침 돕니다.",
                    ))
            return pub_ok

        bridge.publish = auto_reply_publish

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=bridge,
        )

        res = processor.process(self.mock_page, self.post)

        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(len(bridge._cancel_requests), 0)


if __name__ == "__main__":
    unittest.main()
