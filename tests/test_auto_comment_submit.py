"""
Tests for Random Auto-Comment Submission functionality.
Verifies:
1. [P0-1] Timeout vs Native Action Race: Native SUBMIT_MANUAL / SKIP drained before timeout.
   Final drain on timeout boundary catches last-second native clicks.
   SUBMIT_MANUAL causes submit_and_verify(click=False), preventing double clicks.
2. [P0-2] User Editing Disarm: User typing (USER_DIRTY=True) disarms auto-submit countdown.
3. [P0-3] PostActionPlan-scoped Sampling Idempotency: Pre-computed roll/selection is strictly respected across retries.
4. [P1-1] Processor Invariant: auto_comment_submit_enabled=True guarantees comment_enabled=True.
5. [P1-3] UI Badge Counter: random_chance_skipped increments both skip and processed counters in StateManager.
6. [P1-5] Config Fail-closed & Zero Delay: Clamping and non-hanging 0.0s immediate auto-submit.
7. [P1-6] Provenance Separation: decision_origin="auto_submit" vs "user" in UserLearningService.
8. Real Gemini auto-submit pipeline with QualityGate, injection, and click=True verification.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.models import (
    CommentProcessResult,
    CommentSubmitState,
    FeedPost,
    FeedSourceType,
    PostActionPlan,
    UserAction,
)
from app.processor import PostProcessor
from app.state import FeedState
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from naver.interaction import CommentInteractionService
from services.config import DEFAULT_CONFIG_V2, normalize_auto_comment_config
from services.gemini_extension_bridge import GeminiResult, GeminiResultStatus


class TestAutoCommentSubmit(unittest.TestCase):
    def setUp(self):
        self.config = {
            "comment_enabled": True,
            "like_enabled": False,
            "auto_comment_submit_enabled": True,
            "auto_comment_chance": 0.60,
            "auto_comment_delay_min": 3.0,
            "auto_comment_delay_max": 6.0,
            "secret_comment": False,
            "ai_clipboard_enabled": False,
            "gemini_web_enabled": False,
            "comment_style_preset": "community",
            "comment_template": "좋은 글 잘 보았습니다."
        }
        self.post = FeedPost(
            key="test:67890",
            url="https://m.blog.naver.com/test/67890",
            source=FeedSourceType.NEIGHBOR,
            title="맛있는 삼겹살 맛집 후기",
            blog_id="test",
            log_no="67890"
        )
        self.mock_page = MagicMock()

    def test_config_defaults(self):
        """Verify new configuration defaults exist."""
        self.assertIn("auto_comment_submit_enabled", DEFAULT_CONFIG_V2)
        self.assertIn("auto_comment_chance", DEFAULT_CONFIG_V2)
        self.assertIn("auto_comment_delay_min", DEFAULT_CONFIG_V2)
        self.assertIn("auto_comment_delay_max", DEFAULT_CONFIG_V2)
        self.assertFalse(DEFAULT_CONFIG_V2["auto_comment_submit_enabled"])
        self.assertEqual(DEFAULT_CONFIG_V2["auto_comment_chance"], 0.60)

    def test_config_normalization(self):
        """Verify normalize_auto_comment_config clamps chance and validates non-negative delay."""
        # Over upper bound
        c1 = normalize_auto_comment_config({
            "auto_comment_chance": 1.5,
            "auto_comment_delay_min": -3.0,
            "auto_comment_delay_max": -1.0,
        })
        self.assertEqual(c1["auto_comment_chance"], 1.0)
        self.assertEqual(c1["auto_comment_delay_min"], 0.0)
        self.assertEqual(c1["auto_comment_delay_max"], 0.0)

        # Under lower bound
        c2 = normalize_auto_comment_config({
            "auto_comment_chance": -0.2,
            "auto_comment_delay_min": 5.0,
            "auto_comment_delay_max": 2.0,
        })
        self.assertEqual(c2["auto_comment_chance"], 0.0)
        self.assertEqual(c2["auto_comment_delay_min"], 5.0)
        self.assertEqual(c2["auto_comment_delay_max"], 5.0)  # max adjusted to >= min

    def test_p1_processor_invariant_auto_comment_enables_comment_pipeline(self):
        """P1-1 Invariant: if auto_comment_submit_enabled is True, comment_enabled is forced True."""
        processor = PostProcessor(
            self.config,
            comment_enabled=False,
            auto_comment_submit_enabled=True,
        )
        self.assertTrue(processor.comment_enabled)

    # -------------------------------------------------------------------------
    # P0-1: Timeout vs Native Action Race Tests
    # -------------------------------------------------------------------------

    def test_p0_native_click_submit_manual_drained_before_timeout(self):
        """P0-1: When user clicks [등록] natively before timeout, NATIVE_SUBMIT is returned, not AUTO_SUBMIT."""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        def mock_evaluate(script, *args):
            if "return [act, dirty]" in script:
                return ["SUBMIT_MANUAL", False]
            return None

        mock_frame.evaluate.side_effect = mock_evaluate

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                timeout_seconds=2.0  # long enough that timeout doesn't expire first
            )
            self.assertEqual(action, UserAction.NATIVE_SUBMIT)

    def test_p0_native_esc_skip_drained_before_timeout(self):
        """P0-1: When user presses Esc (SKIP) natively before timeout, SKIP is returned, not AUTO_SUBMIT."""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        def mock_evaluate(script, *args):
            if "return [act, dirty]" in script:
                return ["SKIP", False]
            return None

        mock_frame.evaluate.side_effect = mock_evaluate

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                timeout_seconds=2.0
            )
            self.assertEqual(action, UserAction.SKIP)

    def test_p0_timeout_boundary_final_drain_catches_native_submit(self):
        """P0-1: On timeout expiration, final drain catches last-second native SUBMIT_MANUAL instead of AUTO_SUBMIT."""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        def mock_evaluate(script, *args):
            if "return [act, dirty]" in script:
                return [None, False]
            if "return act;" in script:
                return "SUBMIT_MANUAL"
            return None

        mock_frame.evaluate.side_effect = mock_evaluate

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                timeout_seconds=0.01
            )
            self.assertEqual(action, UserAction.NATIVE_SUBMIT)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.NATIVE_SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="삼겹살 육즙이랑 볶음밥 비주얼이 최고네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    def test_p0_manual_submit_calls_submit_and_verify_with_click_false(
        self, mock_submit, mock_read, mock_wait, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """P0-1: When wait_for_user_action yields NATIVE_SUBMIT, submit_and_verify is called with click=False (no double click)."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="삼겹살 맛집", excerpt="노릇노릇 삼겹살")

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=True,
        )
        res = processor.process(self.mock_page, self.post, action_plan=plan)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        mock_submit.assert_called_once()
        # click must be False for NATIVE_SUBMIT!
        self.assertFalse(mock_submit.call_args[1].get("click"))

    # -------------------------------------------------------------------------
    # P0-2: User Editing Disarm Tests
    # -------------------------------------------------------------------------

    def test_p0_user_dirty_edit_disarms_timeout(self):
        """P0-2: When user types in comment box (__NAVER_COMMENT_USER_DIRTY__ = True), auto-submit timeout is disarmed."""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        state = {"poll": 0}
        stop_event = threading.Event()

        def mock_evaluate(script, *args):
            if "return [act, dirty]" in script:
                state["poll"] += 1
                if state["poll"] >= 2:
                    stop_event.set()
                    return [None, True]
                return [None, False]
            return None

        mock_frame.evaluate.side_effect = mock_evaluate

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.05
            )
            # Stopped by stop_event because timeout was disarmed!
            self.assertEqual(action, UserAction.STOP)

    # -------------------------------------------------------------------------
    # P0-3: PostActionPlan-scoped Sampling Idempotency Tests
    # -------------------------------------------------------------------------

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("random.random")
    def test_p0_post_action_plan_sampling_idempotency_skip(
        self, mock_rand, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """P0-3: When action_plan has comment_sample_selected=False, processor uses it without calling random.random()."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=False,
            comment_sample_roll=0.88
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            auto_comment_submit_enabled=True,
            auto_comment_chance=0.60,
        )
        res = processor.process(self.mock_page, self.post, action_plan=plan)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        self.assertEqual(res.comment_result.error, "random_chance_skipped")
        # random.random() must NOT be called because plan was pre-computed!
        mock_rand.assert_not_called()

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="삼겹살 육즙이랑 볶음밥 비주얼이 최고네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    @patch("random.random")
    def test_p0_post_action_plan_sampling_idempotency_selected(
        self, mock_rand, mock_submit, mock_read, mock_wait, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """P0-3: When action_plan has comment_sample_selected=True, processor proceeds to draft without re-rolling."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="삼겹살 맛집", excerpt="노릇노릇 삼겹살 후기")

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.15
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=True,
            auto_comment_chance=0.60,
        )
        res = processor.process(self.mock_page, self.post, action_plan=plan)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        mock_rand.assert_not_called()

    # -------------------------------------------------------------------------
    # P1-3: UI Badge Counter Alignment Test
    # -------------------------------------------------------------------------

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    def test_p1_random_chance_skipped_increments_state_mgr(
        self, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """P1-3: When comment is skipped due to random chance, state_mgr updates skip and processed counts."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")

        mock_state_mgr = MagicMock()
        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=False,
            comment_sample_roll=0.95
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            auto_comment_submit_enabled=True,
            state_manager=mock_state_mgr,
        )
        processor.process(self.mock_page, self.post, action_plan=plan)

        # Check that state_mgr.update was called with SKIPPING and inc_skip=True, inc_processed=True
        calls = [c for c in mock_state_mgr.update.call_args_list if c.kwargs.get("inc_skip") is True]
        self.assertTrue(len(calls) > 0)
        self.assertEqual(calls[0].kwargs.get("new_state"), FeedState.SKIPPING)
        self.assertTrue(calls[0].kwargs.get("inc_processed"))

    # -------------------------------------------------------------------------
    # P1-5: Zero Delay Test
    # -------------------------------------------------------------------------

    def test_p1_zero_delay_auto_submits_immediately(self):
        """P1-5: timeout_seconds=0.0 immediately returns AUTO_SUBMIT without hanging."""
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.evaluate.return_value = None
        mock_page.main_frame = mock_page

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page}):
            start = time.time()
            action = CommentInteractionService.wait_for_user_action(
                mock_page,
                timeout_seconds=0.0
            )
            elapsed = time.time() - start
            self.assertEqual(action, UserAction.AUTO_SUBMIT)
            self.assertLess(elapsed, 0.5)

    # -------------------------------------------------------------------------
    # P1-6: Provenance Separation Tests
    # -------------------------------------------------------------------------

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="삼겹살 육즙이랑 볶음밥 비주얼이 최고네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    @patch("services.user_learning_service.UserLearningService.record_submission")
    def test_p1_provenance_separation_auto_submit_records_decision_origin_auto(
        self, mock_learning, mock_submit, mock_read, mock_wait, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """P1-6: When action is AUTO_SUBMIT, UserLearningService.record_submission receives decision_origin='auto_submit'."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="삼겹살 맛집", excerpt="노릇노릇 삼겹살")

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=True,
        )
        processor.process(self.mock_page, self.post, action_plan=plan)

        mock_learning.assert_called_once()
        self.assertEqual(mock_learning.call_args[1].get("decision_origin"), "auto_submit")

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.NATIVE_SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="삼겹살 육즙이랑 볶음밥 비주얼이 최고네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    @patch("services.user_learning_service.UserLearningService.record_submission")
    def test_p1_provenance_separation_manual_submit_records_decision_origin_user(
        self, mock_learning, mock_submit, mock_read, mock_wait, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """P1-6: When action is NATIVE_SUBMIT, UserLearningService.record_submission receives decision_origin='user'."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="삼겹살 맛집", excerpt="노릇노릇 삼겹살")

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=True,
        )
        processor.process(self.mock_page, self.post, action_plan=plan)

        mock_learning.assert_called_once()
        self.assertEqual(mock_learning.call_args[1].get("decision_origin"), "user")

    # -------------------------------------------------------------------------
    # E2E Gemini Pipeline Test
    # -------------------------------------------------------------------------

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="삼겹살 솥뚜껑 비주얼이 최고네요! 김치 구워먹는 것도 넘 맛있어보여요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    def test_gemini_auto_submit_full_pipeline(
        self, mock_submit, mock_read, mock_wait, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """Verify full Gemini pipeline: generation, quality gate, injection, auto-submit timeout, and click=True submission."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(
            title="성수동 삼겹살 솥뚜껑 구이 맛집",
            excerpt="성수동 솥뚜껑 삼겹살집 다녀왔습니다. 두툼한 고기랑 김치가 예술입니다."
        )

        mock_gemini_bridge = MagicMock()
        mock_gemini_bridge.preflight.return_value = MagicMock(ready=True)
        mock_gemini_bridge.wait_for_result.side_effect = lambda cmd, **kwargs: GeminiResult(
            request_id=cmd.request_id,
            post_key=cmd.post_key,
            navigation_version=cmd.navigation_version,
            status=GeminiResultStatus.COMPLETED,
            text="삼겹살 솥뚜껑 비주얼이 최고네요! 김치 구워먹는 것도 넘 맛있어보여요~",
            error=""
        )

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        cfg = dict(self.config)
        cfg["gemini_web_enabled"] = True

        processor = PostProcessor(
            cfg,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_gemini_bridge,
            auto_comment_submit_enabled=True,
            auto_comment_delay_min=3.0,
            auto_comment_delay_max=5.0,
        )
        res = processor.process(self.mock_page, self.post, action_plan=plan)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        # Verify Gemini extension bridge was invoked
        mock_gemini_bridge.publish.assert_called_once()
        # Verify auto-submit called wait_for_user_action with timeout
        self.assertTrue(mock_wait.called)
        _, kwargs = mock_wait.call_args
        self.assertIn("timeout_seconds", kwargs)
        self.assertGreaterEqual(kwargs["timeout_seconds"], 3.0)
        # Verify submit_and_verify was called with click=True
        mock_submit.assert_called_once()
        self.assertTrue(mock_submit.call_args[1].get("click"))


if __name__ == "__main__":
    unittest.main()
