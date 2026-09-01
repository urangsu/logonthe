import threading
import time
import unittest
from unittest.mock import MagicMock

from services.gemini_extension_bridge import (
    GeminiBridgeHTTPServer,
    GeminiCommand,
    GeminiExtensionBridge,
    GeminiResult,
    GeminiResultStatus,
)
from naver.editor_adapter import CommentEditorAdapter
from naver.resolver import MobileDOMResolver


class GeminiBusyRecoveryContractTests(unittest.TestCase):
    """
    Contract tests for Gemini busy lifecycle, cancel propagation, orphaned reset,
    and Naver editor fail-closed exception handling.
    """

    def test_case_1_js_exception_finally_clears_active_execution(self):
        """① execute 중 JS exception 발생 시 try/finally에 의해 activeExecution이 정리되어 ready 복구"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r5")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2)
        self.assertTrue(bridge.preflight().ready)

        # Publish command -> active_request_id set
        cmd = GeminiCommand.create("post:1", 1, "test prompt")
        bridge.publish(cmd)
        self.assertEqual(bridge.preflight().active_request_id, cmd.request_id)

        # Exception caught in JS wrapper / bridge cancel
        bridge.cancel_command(cmd.request_id)
        self.assertIsNone(bridge.preflight().active_request_id)

        # Heartbeat returns ready after finally block
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2)
        pf = bridge.preflight()
        self.assertTrue(pf.ready)
        self.assertEqual(pf.status, "ready")

    def test_case_2_skip_event_triggers_cancel_command_within_2s(self):
        """② skip 후 2초 내 ready 및 cancel_command 전송"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r5")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2)

        cmd = GeminiCommand.create("post:skip", 1, "prompt")
        bridge.publish(cmd)
        bridge.claim_command(cmd.request_id, "tab-1")

        # Heartbeat reports busy with cmd.request_id
        bridge.record_heartbeat("busy", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2, busy_request_id=cmd.request_id)
        self.assertEqual(bridge.preflight().status, "busy_active_command")

        # Skip event fired
        skip_event = threading.Event()
        skip_event.set()

        res = bridge.wait_for_result(cmd, timeout=2.0, skip_event=skip_event)
        self.assertIsNone(res)

        # Active request is cancelled in Python bridge
        self.assertIsNone(bridge.preflight().active_request_id)
        self.assertIn(cmd.request_id, bridge._cancel_requests)

        # Heartbeat reports ready after cancellation
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2)
        self.assertTrue(bridge.preflight().ready)

    def test_case_3_python_restart_orphaned_busy_auto_reset(self):
        """③ Python 재시작 + 이전 busy → 자동 orphan reset"""
        # Previous python session left an orphan request in Chrome runtime
        orphan_req_id = "stale_request_from_previous_run"
        new_bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r5")

        # Chrome extension sends heartbeat with orphan_req_id
        new_bridge.record_heartbeat(
            "busy", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2,
            busy_request_id=orphan_req_id, busy_since=time.time() - 10.0, busy_deadline_at=time.time() + 50.0
        )

        # Preflight recognizes this as busy_orphaned because Python activeRequestId is None
        pf = new_bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "busy_orphaned")

        # await_ready triggers auto recovery
        def simulate_content_reset():
            time.sleep(0.3)
            # Extension background receives cancel and resets to ready
            new_bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2)

        threading.Thread(target=simulate_content_reset, daemon=True).start()
        pf_recovered = new_bridge.await_ready(timeout=2.0)
        self.assertTrue(pf_recovered.ready)
        self.assertEqual(pf_recovered.status, "ready")

    def test_case_4_active_command_generation_retains_busy_active(self):
        """④ 실제 current command generation은 busy_active_command 유지"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r5")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2)

        cmd = GeminiCommand.create("post:active", 1, "prompt")
        bridge.publish(cmd)

        # Extension reports busy with the same requestId as active command
        bridge.record_heartbeat(
            "busy", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r5", 3, 2,
            busy_request_id=cmd.request_id, busy_since=time.time(), busy_deadline_at=cmd.deadline_at
        )

        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "busy_active_command")

    def test_case_5_submit_is_disabled_exception_fails_closed(self):
        """⑤ submit button is_disabled() exception 발생 시 fail-closed로 set_text 실패 처리"""
        page = MagicMock()
        frame = MagicMock()
        editor = MagicMock()
        editor.evaluate.return_value = True
        editor.inner_text.return_value = "테스트 댓글 내용"

        submit_btn = MagicMock()
        submit_btn.is_disabled.side_effect = RuntimeError("DOM disconnected during check")

        dom_context = {
            "frame": frame,
            "frame_name": "main_frame",
            "editor": editor,
            "selector": "#naverComment__write_textarea",
            "type": "contenteditable",
            "placeholder_visible": False,
        }

        with unittest.mock.patch.object(MobileDOMResolver, "get_comment_editor_context", return_value=dom_context), \
             unittest.mock.patch.object(MobileDOMResolver, "get_comment_submit_context", return_value={"frame": frame, "button": submit_btn, "selector": "button.u_cbox_btn_upload"}):
            ok = CommentEditorAdapter.set_text(page, "테스트 댓글 내용")
            self.assertFalse(ok, "is_disabled() exception must fail-closed")


if __name__ == "__main__":
    unittest.main()
