import threading
import unittest
from unittest.mock import MagicMock, patch
from app.controller import FeedController
from app.models import FeedPost, FeedSourceType, PostProcessResult
from app.state import StateManager


class HelperControllerTests(unittest.TestCase):
    def test_old_automation_flags_and_idempotent_history_never_bypass_helper(self):
        for flags in (True, False):
            with self.subTest(legacy_flags=flags):
                post = FeedPost("owner:123", FeedSourceType.DIRECT, "https://m.blog.naver.com/owner/123")
                config = {"feed_source": "direct", "max_feed_items": 1, "direct_urls": [post.url],
                          "like_enabled": flags, "comment_enabled": flags, "gemini_web_enabled": True,
                          "assistant_mode": True, "gemini_browser_mode": "managed_playwright"}
                history = MagicMock()
                history.is_liked.return_value = True
                history.is_comment_submitted.return_value = True
                with patch("app.controller.BrowserSession") as session_type, patch(
                        "app.controller.NaverAuthGuard.check_login_cookies", return_value=(True, [])), patch(
                        "app.controller.DirectUrlSource") as source_type, patch(
                        "app.controller.ManualHelperProcessor") as helper_type:
                    source_type.return_value.discover_posts.return_value = [post]
                    helper_type.return_value.process.return_value = PostProcessResult(post)
                    controller = FeedController(config, history, StateManager(), threading.Event())
                    controller.run()
                    helper_type.return_value.process.assert_called_once()
                    session_type.return_value.get_gemini_page.assert_not_called()
                    session_type.return_value.get_stats_page.assert_not_called()
                    history.record_result.assert_called_once()
                    session_type.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
