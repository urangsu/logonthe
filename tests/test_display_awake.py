import subprocess
import unittest
from unittest.mock import Mock, patch

from services.display_awake import DisplayAwakeGuard


class DisplayAwakeTests(unittest.TestCase):
    @patch("services.display_awake.sys.platform", "darwin")
    @patch("services.display_awake.subprocess.Popen")
    def test_start_is_idempotent_and_bound_to_parent(self, popen):
        popen.return_value.poll.return_value = None
        guard = DisplayAwakeGuard()
        guard.start()
        guard.start()
        self.assertEqual(popen.call_count, 1)
        args = popen.call_args.args[0]
        self.assertEqual(args[:3], ["/usr/bin/caffeinate", "-di", "-w"])
        self.assertTrue(args[3].isdigit())
        guard.stop()
        guard.stop()
        popen.return_value.terminate.assert_called_once()
        popen.return_value.wait.assert_called_once()

    @patch("services.display_awake.sys.platform", "linux")
    @patch("services.display_awake.subprocess.Popen")
    def test_non_mac_does_not_launch(self, popen):
        guard = DisplayAwakeGuard()
        guard.start()
        guard.stop()
        popen.assert_not_called()

    @patch("services.display_awake.sys.platform", "darwin")
    @patch("services.display_awake.subprocess.Popen", side_effect=OSError("unavailable"))
    def test_launch_failure_does_not_crash_the_feed(self, popen):
        guard = DisplayAwakeGuard()
        self.assertFalse(guard.start())
        guard.stop()

    def test_stop_reaps_process_after_timeout(self):
        guard = DisplayAwakeGuard()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("caffeinate", 2), 0]
        guard._process = process
        guard.stop()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_controller_releases_awake_guard_on_startup_error(self):
        from app.controller import FeedController
        with patch("app.controller.SamplingHistoryManager"), \
                patch("app.controller.BrowserSession") as session, \
                patch("app.controller.DisplayAwakeGuard") as awake:
            session.return_value.start.side_effect = RuntimeError("test startup error")
            controller = FeedController(
                config={"feed_source": "recommendation", "comment_enabled": True,
                        "gemini_web_enabled": False}, history=Mock(),
            )
            controller.run()
            awake.return_value.start.assert_called_once()
            awake.return_value.stop.assert_called_once()

    def test_likes_only_controller_does_not_request_awake(self):
        from app.controller import FeedController
        with patch("app.controller.SamplingHistoryManager"), \
                patch("app.controller.BrowserSession") as session, \
                patch("app.controller.DisplayAwakeGuard") as awake:
            session.return_value.start.side_effect = RuntimeError("test startup error")
            controller = FeedController(
                config={"feed_source": "recommendation", "comment_enabled": False,
                        "gemini_web_enabled": False}, history=Mock(),
            )
            controller.run()
            awake.return_value.start.assert_not_called()
            awake.return_value.stop.assert_called_once()
