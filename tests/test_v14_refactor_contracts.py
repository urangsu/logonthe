import unittest
from unittest.mock import MagicMock, patch
import threading
import time
import os

from services.gemini_extension_bridge import (
    GeminiCommand,
    GeminiResult,
    GeminiResultStatus,
    GeminiExtensionBridge,
    GeminiBridgeHTTPServer,
)
from services.my_blog_reply_service import MyBlogReplyService
from services.draft import DraftService
from services.runtime_contract import get_python_git_commit


class TestV14RefactorContracts(unittest.TestCase):
    """
    Contract tests for:
    - GEM-001 ~ GEM-005: GeminiCommand creation, cancellation, and API endpoints
    - REPLY-001 ~ REPLY-006: Reply assistant architecture and session reuse
    - GUI-001 ~ GUI-005: UI cleanup and neighbor sweep frame behavior
    - RUNTIME-001: Real git commit resolution without hardcoded fallback
    """

    # --- GEMINI CONTRACTS ---

    def test_gem_001_gemini_command_create_factory(self):
        """GEM-001: GeminiCommand.create factory computes correct deadlines and delivery reserve."""
        cmd = GeminiCommand.create(
            post_key="test_post_1",
            navigation_version=2,
            prompt="Hello Gemini",
            request_id="test_req_001",
            timeout_seconds=50.0,
        )
        self.assertEqual(cmd.request_id, "test_req_001")
        self.assertEqual(cmd.post_key, "test_post_1")
        self.assertEqual(cmd.navigation_version, 2)
        self.assertEqual(cmd.prompt, "Hello Gemini")
        # Delivery reserve is 9.0s
        self.assertAlmostEqual(cmd.deadline_at - cmd.created_at, 50.0 - 9.0, delta=1.0)
        self.assertAlmostEqual(cmd.acceptance_deadline_at - cmd.created_at, 50.0, delta=1.0)

    def test_gem_002_processor_uses_command_create_factory(self):
        """GEM-002: app/processor.py uses GeminiCommand.create factory."""
        processor_path = os.path.join(os.path.dirname(__file__), "..", "app", "processor.py")
        with open(processor_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("GeminiCommand.create(", content)
        # Verify no direct GeminiCommand(...) instantiation in processor.py
        import re
        direct_calls = re.findall(r"(?<!\.)GeminiCommand\(", content)
        self.assertEqual(len(direct_calls), 0, f"Found direct GeminiCommand() instantiation in processor.py: {direct_calls}")

    def test_gem_003_immediate_cancel_on_skip(self):
        """GEM-003: cancel_command immediately marks request as cancelled and exposes to API."""
        bridge = GeminiExtensionBridge()
        cmd = GeminiCommand.create(post_key="p1", navigation_version=1, prompt="test")
        bridge.publish(cmd)

        self.assertFalse(bridge.is_command_cancelled(cmd.request_id))
        bridge.cancel_command(cmd.request_id)
        self.assertTrue(bridge.is_command_cancelled(cmd.request_id))

        # Result submitted after cancellation must be rejected
        res = GeminiResult(
            request_id=cmd.request_id,
            post_key="p1",
            navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text="late reply",
        )
        ok, reason = bridge.submit_result(res)
        self.assertFalse(ok)
        self.assertEqual(reason, "request_cancelled")

    def test_gem_004_bridge_wait_for_result_aborts_on_skip_event(self):
        """GEM-004: wait_for_result aborts immediately and cancels command when skip_event is set."""
        bridge = GeminiExtensionBridge()
        cmd = GeminiCommand.create(post_key="p2", navigation_version=1, prompt="test", timeout_seconds=10.0)
        bridge.publish(cmd)

        skip_evt = threading.Event()
        # Set skip_event after small delay
        def trigger_skip():
            time.sleep(0.05)
            skip_evt.set()

        t = threading.Thread(target=trigger_skip)
        t.start()
        start_t = time.time()
        result = bridge.wait_for_result(cmd, timeout=5.0, skip_event=skip_evt)
        elapsed = time.time() - start_t
        t.join()

        self.assertIsNone(result)
        self.assertLess(elapsed, 1.0)
        self.assertTrue(bridge.is_command_cancelled(cmd.request_id))

    def test_gem_005_preflight_and_cancel_http_endpoints(self):
        """GEM-005: /v1/preflight includes cancelled_request_ids and /v1/cancel reports status."""
        bridge = GeminiExtensionBridge()
        bridge.cancel_command("cancelled_req_xyz")

        status = bridge.preflight_status()
        self.assertIn("cancelled_request_ids", status)
        self.assertIn("cancelled_req_xyz", status["cancelled_request_ids"])

        # Test cancel_status method directly
        cancel_info = bridge.cancel_status("cancelled_req_xyz")
        self.assertTrue(cancel_info["cancelled"])
        self.assertEqual(cancel_info["requestId"], "cancelled_req_xyz")

        non_cancelled = bridge.cancel_status("other_req")
        self.assertFalse(non_cancelled["cancelled"])

    # --- REPLY ASSISTANT CONTRACTS ---

    def test_reply_001_no_config_import_in_main_window(self):
        """REPLY-001: ui/main_window.py does not import nonexistent config module."""
        main_window_path = os.path.join(os.path.dirname(__file__), "..", "ui", "main_window.py")
        with open(main_window_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("from config import", content)
        self.assertNotIn("import config\n", content)

    def test_reply_002_reply_service_preflight_check(self):
        """REPLY-003: MyBlogReplyService checks preflight.ready before publishing."""
        bridge = MagicMock()
        bridge.preflight_status.return_value = {"ready": False, "reason": "no_tab"}

        result = MyBlogReplyService.generate_replies(
            bridge=bridge,
            post_title="제목",
            post_excerpt="본문",
            comments=[{"comment_no": "1", "nickname": "test", "text": "댓글"}],
        )
        self.assertEqual(len(result), 0)
        bridge.publish.assert_not_called()

    def test_reply_003_reply_service_checks_publish_success(self):
        """REPLY-004: MyBlogReplyService handles publish failure gracefully."""
        bridge = MagicMock()
        bridge.preflight_status.return_value = {"ready": True}
        bridge.publish.return_value = False

        result = MyBlogReplyService.generate_replies(
            bridge=bridge,
            post_title="제목",
            post_excerpt="본문",
            comments=[{"comment_no": "1", "nickname": "test", "text": "댓글"}],
        )
        self.assertEqual(len(result), 0)
        bridge.wait_for_result.assert_not_called()

    def test_reply_004_reply_service_wait_for_result_error_handling(self):
        """REPLY-005: MyBlogReplyService handles failed/timeout result status."""
        bridge = MagicMock()
        bridge.preflight_status.return_value = {"ready": True}
        bridge.publish.return_value = True
        bridge.wait_for_result.return_value = GeminiResult(
            request_id="req_reply",
            post_key="reply_batch",
            navigation_version=1,
            status=GeminiResultStatus.FAILED,
            text="",
            error="gemini_error",
        )

        result = MyBlogReplyService.generate_replies(
            bridge=bridge,
            post_title="제목",
            post_excerpt="본문",
            comments=[{"comment_no": "1", "nickname": "test", "text": "댓글"}],
        )
        self.assertEqual(len(result), 0)

    # --- GUI CLEANUP & BEHAVIOR CONTRACTS ---

    def test_gui_001_apple_events_removed_from_main_window(self):
        """GUI-001: Apple Events radio option is removed from ui/main_window.py."""
        main_window_path = os.path.join(os.path.dirname(__file__), "..", "ui", "main_window.py")
        with open(main_window_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("existing_chrome_mac", content)
        self.assertNotIn("Apple Events", content)
        self.assertNotIn("ExistingChromeGeminiBridge", content)

    def test_gui_002_recommendation_suffix_removed(self):
        """GUI-002: Recommendation-feed-only suffix removed from UI and DraftService."""
        main_window_path = os.path.join(os.path.dirname(__file__), "..", "ui", "main_window.py")
        with open(main_window_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("recommendation_suffix_enabled", content)
        self.assertNotIn("recommendation_suffix", content)

        # DraftService suffix resolution must ignore feed source differentiation
        cfg = {"suffix_enabled": True, "suffix": " 감사합니다!"}
        suffix = DraftService.resolve_suffix(cfg, is_recommendation=True)
        self.assertEqual(suffix, " 감사합니다!")
        suffix_neighbor = DraftService.resolve_suffix(cfg, is_recommendation=False)
        self.assertEqual(suffix_neighbor, " 감사합니다!")

    def test_gui_003_neighbor_options_frame_always_visible(self):
        """GUI-003: neighbor_options_frame is always packed and does not get unpacked on feed change."""
        main_window_path = os.path.join(os.path.dirname(__file__), "..", "ui", "main_window.py")
        with open(main_window_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("self.neighbor_options_frame.pack_forget()", content)

    # --- RUNTIME GIT COMMIT CONTRACT ---

    def test_runtime_001_git_commit_no_hardcoded_fallback(self):
        """RUNTIME-001: get_python_git_commit does not contain hardcoded a924dcf fallback."""
        utils_path = os.path.join(os.path.dirname(__file__), "..", "services", "runtime_contract.py")
        with open(utils_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn('"a924dcf"', content)
        self.assertNotIn("'a924dcf'", content)

        commit = get_python_git_commit()
        self.assertIsInstance(commit, str)
        self.assertNotEqual(commit, "")
        # Should be either valid 7-char SHA or "unknown"
        if commit != "unknown":
            self.assertEqual(len(commit), 7)


if __name__ == "__main__":
    unittest.main()
