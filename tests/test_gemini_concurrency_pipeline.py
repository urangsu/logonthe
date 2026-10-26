"""
Gemini Concurrency Pipeline Tests
Validates eligibility-first Gemini generation, order of operations,
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
            "like_popularity_guard_enabled": False,
            "daily_visitor_guard_enabled": False,
        }
        self.post = FeedPost(
            key="concurrency:post_001",
            url="https://m.blog.naver.com/test/concurrency_001",
            source=FeedSourceType.NEIGHBOR,
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        self.mock_page = MagicMock()

    def test_excluded_neighbor_never_generates_even_with_likes_disabled(self):
        for likes_enabled in (True, False):
            for status in (LikeEligibility.SKIP_LIKE_COUNT, LikeEligibility.SKIP_DAILY_VISITORS,
                           LikeEligibility.UNKNOWN_DAILY_VISITORS):
                with self.subTest(likes_enabled=likes_enabled, status=status), \
                        patch("app.processor.TargetPostGuard.verify"), \
                        patch("app.processor.LikeEligibilityService.evaluate", return_value=LikeEligibilityResult(
                            eligible=False, status=status, reason=status.value)), \
                        patch("app.processor.LikeTransactionService.execute_like_transaction") as like, \
                        patch("app.processor.CommentInteractionService.open_comment_layer") as open_comment:
                    bridge = MagicMock()
                    processor = PostProcessor(self.config, like_enabled=likes_enabled,
                        comment_enabled=True, gemini_web_enabled=True, gemini_extension_bridge=bridge)
                    with patch.object(processor, "_prepare_comment_generation_context") as prompt:
                        result = processor.process(self.mock_page, self.post)
                    self.assertEqual(result.comment_result.error, f"post_guard:{status.value}")
                    bridge.publish.assert_not_called()
                    prompt.assert_not_called()
                    like.assert_not_called()
                    open_comment.assert_not_called()

    def test_duplicate_or_closed_comments_never_publish(self):
        for presence_state, open_ok in ((CommentPresenceState.PRESENT, True),
                                        (CommentPresenceState.UNKNOWN, True),
                                        (CommentPresenceState.ABSENT, False)):
            with self.subTest(presence=presence_state, open_ok=open_ok), \
                    patch("app.processor.TargetPostGuard.verify"), \
                    patch("app.processor.LikeTransactionService.resolve_like_state", return_value=LikeStateResult(
                        state=LikeState.LIKED, confidence=LikeConfidence.HIGH)), \
                    patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(open_ok, "comment_disabled")), \
                    patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(
                        state=presence_state, confidence="high")):
                bridge = MagicMock()
                processor = PostProcessor(self.config, like_enabled=True, comment_enabled=True,
                    gemini_web_enabled=True, gemini_extension_bridge=bridge)
                with patch.object(processor, "_prepare_comment_generation_context") as prompt:
                    processor.process(self.mock_page, self.post)
                bridge.publish.assert_not_called()
                prompt.assert_not_called()

    def test_meta_followup_never_reaches_comment_editor(self):
        bridge = MagicMock()
        bridge.await_ready.return_value = MagicMock(ready=True)
        bridge.wait_for_result.side_effect = lambda cmd, **kwargs: GeminiResult(
            request_id=cmd.request_id, post_key=cmd.post_key,
            navigation_version=cmd.navigation_version, status=GeminiResultStatus.COMPLETED,
            text="에스카르고 식감이 골뱅이랑 비슷하다니 저두 한번 도전해보고 싶네요 ㅎㅎ\n이 느낌이 괜찮으신가요, 아니면 와인이나 디저트(크림 브륄레) 쪽으로 포인트를 바꿔볼까요?",
        )
        with patch("app.processor.TargetPostGuard.verify"), \
                patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
                patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(
                    state=CommentPresenceState.ABSENT, confidence="high")), \
                patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(
                    title="짜장과 군만두", excerpt="짜장 소스가 넉넉하고 군만두는 바삭하게 나왔어요. 군만두를 짜장 소스에 찍어 먹는 조합이 좋았어요.")), \
                patch("app.processor.CommentEditorAdapter.set_text") as write, \
                patch("app.processor.CommentInteractionService.submit_and_verify") as submit:
            processor = PostProcessor(self.config, like_enabled=False, comment_enabled=True,
                gemini_web_enabled=True, gemini_extension_bridge=bridge)
            result = processor.process(self.mock_page, self.post)
        self.assertEqual(result.comment_result.status, CommentSubmitState.SKIPPED)
        self.assertEqual(result.comment_result.error, "response_meta_contamination")
        self.assertEqual(bridge.publish.call_count, 2)
        self.assertIn("만족 여부", bridge.publish.call_args_list[1].args[0].prompt)
        write.assert_not_called()
        submit.assert_not_called()

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
        resolve_like_state < like_transaction < post_like < comment_open < duplicate_scan < publish < wait_for_result
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
        mock_elig.side_effect = lambda **kwargs: (
            execution_order.append("eligibility"), mock_elig.return_value
        )[1]
        mock_tx.side_effect = lambda page, stop_event=None, post=None, *args, **kwargs: (
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
            "eligibility",
            "resolve_like_state",
            "like_transaction",
            "post_like",
            "comment_open",
            "duplicate_scan",
            "publish",
            "wait_for_result",
        ]
        self.assertEqual(execution_order, expected_order)
        mock_elig.assert_called_once()

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
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    @patch("app.processor.LikeEligibilityService.evaluate")
    @patch("app.processor.LikeTransactionService.execute_like_transaction")
    def test_zero_orphan_with_real_bridge_on_stop_requested(
        self, mock_tx, mock_elig, mock_like_state, mock_extract, mock_guard
    ):
        """
        Using real GeminiExtensionBridge:
        A stop before like completion never publishes a generation command.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        mock_like_state.return_value = LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)
        mock_elig.return_value = LikeEligibilityResult(
            eligible=True, status=LikeEligibility.ELIGIBLE, reason="ok", like_count=10, daily_visitors=100
        )
        mock_tx.return_value = LikeProcessResult(
            state_before=LikeState.NOT_LIKED, action_taken=False, state_after=LikeState.NOT_LIKED, error="stopped_pre_like"
        )

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

        with self.assertRaises(StopRequestedException):
            processor.process(self.mock_page, self.post)

        # Real bridge invariant verification
        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(bridge._command_state, "idle")
            self.assertEqual(len(bridge._cancel_requests), 0)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    @patch("app.processor.LikeEligibilityService.evaluate")
    @patch("app.processor.LikeTransactionService.execute_like_transaction")
    def test_zero_orphan_with_real_bridge_on_skip_requested(
        self, mock_tx, mock_elig, mock_like_state, mock_extract, mock_guard
    ):
        """
        Using real GeminiExtensionBridge:
        A skip before like completion never publishes a generation command.
        """
        mock_extract.return_value = MagicMock(
            title="테스트 맛집 탐방",
            excerpt="서귀포에 위치한 신선한 해물 파스타 전문점 리뷰입니다.",
        )
        mock_like_state.return_value = LikeStateResult(state=LikeState.NOT_LIKED, confidence=LikeConfidence.HIGH)
        mock_elig.return_value = LikeEligibilityResult(
            eligible=True, status=LikeEligibility.ELIGIBLE, reason="ok", like_count=10, daily_visitors=100
        )
        mock_tx.return_value = LikeProcessResult(
            state_before=LikeState.NOT_LIKED, action_taken=False, state_after=LikeState.NOT_LIKED, error="user_skipped_pre_like"
        )

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

        res = processor.process(self.mock_page, self.post)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(bridge._command_state, "idle")
            self.assertEqual(len(bridge._cancel_requests), 0)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.LikeTransactionService.resolve_like_state")
    def test_zero_orphan_with_real_bridge_on_unexpected_exception(
        self, mock_like_state, mock_extract, mock_guard
    ):
        """
        Using real GeminiExtensionBridge:
        An exception in like processing leaves the generation bridge idle.
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

        # No generation command existed before the like failure.
        with bridge._condition:
            self.assertIsNone(bridge._command)
            self.assertIsNone(bridge._active_request_id)
            self.assertEqual(bridge._command_state, "idle")
            self.assertEqual(len(bridge._cancel_requests), 0)

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
