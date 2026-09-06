"""
Tests for Random Auto-Comment Submission functionality.
Verifies:
1. When auto_comment_submit_enabled is True and random roll exceeds chance, comment is skipped.
2. When auto_comment_submit_enabled is True and random roll is within chance, Gemini comment is generated and automatically submitted.
3. Timeout and AUTO_SUBMIT action in wait_for_user_action.
4. Skip event during waiting period interrupts and skips post cleanly.
5. Config schema and default values include auto_comment_submit configurations.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

from app.models import CommentSubmitState, FeedPost, FeedSourceType, UserAction
from app.processor import PostProcessor
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from naver.interaction import CommentInteractionService
from services.config import DEFAULT_CONFIG_V2


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

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("random.random", return_value=0.85)  # 0.85 > 0.60 -> Skip
    def test_random_roll_miss_skips_comment(
        self, mock_rand, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """When random roll > auto_comment_chance, comment step is skipped with random_chance_skipped."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            auto_comment_submit_enabled=True,
            auto_comment_chance=0.60,
        )
        res = processor.process(self.mock_page, self.post)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        self.assertEqual(res.comment_result.error, "random_chance_skipped")

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
    @patch("random.random", return_value=0.30)  # 0.30 <= 0.60 -> Win
    def test_random_roll_hit_auto_submits(
        self, mock_rand, mock_submit, mock_read, mock_wait, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """When random roll <= auto_comment_chance, comment is drafted and auto submitted."""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="삼겹살 맛집", excerpt="노릇노릇 삼겹살과 된장찌개 맛집 다녀왔습니다. 김치도 맛있어요.")

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=True,
            auto_comment_chance=0.60,
            auto_comment_delay_min=3.0,
            auto_comment_delay_max=5.0,
        )
        res = processor.process(self.mock_page, self.post)

        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertTrue(mock_wait.called)
        # Check that timeout_seconds was passed to wait_for_user_action
        _, kwargs = mock_wait.call_args
        self.assertIn("timeout_seconds", kwargs)
        self.assertGreaterEqual(kwargs["timeout_seconds"], 3.0)
        self.assertLessEqual(kwargs["timeout_seconds"], 5.0)
        # Check submit_and_verify was called with click=True
        mock_submit.assert_called_once()
        self.assertTrue(mock_submit.call_args[1].get("click"))

    def test_wait_for_user_action_timeout_returns_auto_submit(self):
        """CommentInteractionService.wait_for_user_action returns AUTO_SUBMIT when timeout expires."""
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.evaluate.return_value = None
        mock_page.main_frame = mock_page

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page}):
            action = CommentInteractionService.wait_for_user_action(
                mock_page,
                timeout_seconds=0.05
            )
            self.assertEqual(action, UserAction.AUTO_SUBMIT)

    def test_wait_for_user_action_skip_event_interrupts(self):
        """Skip event interrupts wait_for_user_action and returns SKIP."""
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        skip_event = threading.Event()
        skip_event.set()

        action = CommentInteractionService.wait_for_user_action(
            mock_page,
            skip_event=skip_event,
            timeout_seconds=5.0
        )
        self.assertEqual(action, UserAction.SKIP)


if __name__ == "__main__":
    unittest.main()
