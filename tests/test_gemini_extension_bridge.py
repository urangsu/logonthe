import threading
import time
import unittest
import json
import http.client

from services.gemini_extension_bridge import (
    GeminiCommand,
    GeminiExtensionBridge,
    GeminiResult,
    GeminiResultStatus,
    GeminiBridgeHTTPServer,
    classify_gemini_failure,
    GeminiFailure,
)


class GeminiExtensionBridgeTests(unittest.TestCase):
    def test_preflight_requires_fresh_ready_heartbeat(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r3")
        self.assertFalse(bridge.preflight().ready)
        self.assertEqual(bridge.preflight().status, "heartbeat_never_received")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r3", 3, 2)
        self.assertTrue(bridge.preflight().ready)
        self.assertEqual(bridge.preflight().status, "ready")

    def test_gem_conn_001_server_unavailable(self):
        bridge = GeminiExtensionBridge()
        bridge.bridge_server_started = False
        bridge.bridge_server_error = "Address already in use"
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "bridge_port_in_use")

    def test_gem_conn_002_heartbeat_never_received(self):
        bridge = GeminiExtensionBridge()
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "heartbeat_never_received")

    def test_gem_conn_003_heartbeat_stale(self):
        bridge = GeminiExtensionBridge()
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r3", 3, 2)
        bridge._heartbeat_at = time.time() - 50.0
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "heartbeat_stale")

    def test_gem_conn_004_extension_version_mismatch(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.0", "13.2.3-r3", 3, 2)
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "extension_version_mismatch")

    def test_gem_conn_005_extension_identity_mismatch(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r3")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "wrong-build", 3, 2)
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "extension_identity_mismatch")

    def test_gem_conn_006_auth_required(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r3")
        bridge.record_heartbeat("auth_required", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r3", 3, 2)
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "auth_required")

    def test_gem_conn_007_dom_unsupported(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r3")
        bridge.record_heartbeat("dom_unsupported", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r3", 3, 2)
        pf = bridge.preflight()
        self.assertFalse(pf.ready)
        self.assertEqual(pf.status, "dom_unsupported")

    def test_gem_conn_008_await_ready_grace_success(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r3")

        def delayed_heartbeat():
            time.sleep(0.2)
            bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.3", "13.2.3-r3", 3, 2)

        threading.Thread(target=delayed_heartbeat, daemon=True).start()
        pf = bridge.await_ready(timeout=1.0)
        self.assertTrue(pf.ready)
        self.assertEqual(pf.status, "ready")

    def test_claimed_and_completed_command_is_not_replayed(self):
        bridge = GeminiExtensionBridge()
        command = GeminiCommand.create("post:claim", 1, "prompt")
        bridge.publish(command)
        self.assertTrue(bridge.claim_command(command.request_id, "tab-a"))
        self.assertFalse(bridge.claim_command(command.request_id, "tab-b"))
        self.assertIsNone(bridge.current_command())
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, command.navigation_version, GeminiResultStatus.COMPLETED, "ok"))
        self.assertIsNone(bridge.current_command())

    def test_stale_result_cannot_complete_new_request(self):
        bridge = GeminiExtensionBridge()
        command = GeminiCommand.create("post:1", 3, "prompt")
        bridge.publish(command)
        bridge.claim_command(command.request_id, "test_claimant")
        bridge.submit_result(GeminiResult("old", "post:0", 2, GeminiResultStatus.COMPLETED, "wrong"))
        self.assertIsNone(bridge.wait_for_result(command, timeout=0.02))
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, 2, GeminiResultStatus.COMPLETED, "wrong nav"))
        self.assertIsNone(bridge.wait_for_result(command, timeout=0.02))
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, 3, GeminiResultStatus.COMPLETED, "right"))
        result = bridge.wait_for_result(command, timeout=0.1)
        self.assertEqual(result.text, "right")

    def test_failure_result_is_returned_without_local_fallback(self):
        bridge = GeminiExtensionBridge()
        command = GeminiCommand.create("post:2", 1, "prompt")
        bridge.publish(command)
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, 1, GeminiResultStatus.AUTH_REQUIRED, "", "login"))
        result = bridge.wait_for_result(command, timeout=0.1)
        self.assertEqual(result.status, GeminiResultStatus.AUTH_REQUIRED)

    def test_http_server_is_loopback_without_extra_pairing_step(self):
        bridge = GeminiExtensionBridge()
        server = GeminiBridgeHTTPServer(bridge, port=0)
        server.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1)
            connection.request("GET", "/v1/status")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertFalse(payload["ready"])
            self.assertEqual(payload["status"], "heartbeat_never_received")
        finally:
            server.stop()

    def test_http_server_cors_headers_and_options_204(self):
        bridge = GeminiExtensionBridge()
        server = GeminiBridgeHTTPServer(bridge, port=0)
        server.start()
        try:
            # 1. OPTIONS preflight
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1)
            conn.request("OPTIONS", "/v1/status")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), "*")
            self.assertIn("OPTIONS", resp.getheader("Access-Control-Allow-Methods", ""))
            conn.close()

            # 2. GET headers
            conn2 = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1)
            conn2.request("GET", "/v1/status")
            resp2 = conn2.getresponse()
            self.assertEqual(resp2.status, 200)
            self.assertIn("no-store", resp2.getheader("Cache-Control", ""))
            self.assertEqual(resp2.getheader("Access-Control-Allow-Origin"), "*")
            conn2.close()
        finally:
            server.stop()

    def test_classify_gemini_failure_pre_dispatch(self):
        for code in ["send_not_ready", "prompt_exact_readback_failed", "prompt_editor_changed_before_send", "fresh_chat_not_verified", "bridge_not_started"]:
            failure = classify_gemini_failure("failed", code, dispatch_attempted=False, click_count=0)
            self.assertEqual(failure.phase, "PRE_DISPATCH", f"Failed for {code}")
            self.assertTrue(failure.retry_safe, f"Should be retry_safe for {code}")
            self.assertEqual(failure.click_count, 0, f"Click count should be 0 for {code}")

        dom_fail = classify_gemini_failure("dom_unsupported", "")
        self.assertEqual(dom_fail.phase, "UNKNOWN")
        self.assertFalse(dom_fail.retry_safe)
        self.assertIsNone(dom_fail.click_count)

    def test_classify_gemini_failure_dispatched(self):
        for code in ["send_dispatch_unconfirmed", "send_commit_unknown"]:
            failure = classify_gemini_failure("failed", code, dispatch_attempted=True, click_count=1)
            self.assertEqual(failure.phase, "DISPATCHED", f"Failed for {code}")
            self.assertFalse(failure.retry_safe, f"Should NOT be retry_safe for {code}")
            self.assertEqual(failure.click_count, 1, f"Click count should be 1 for {code}")

    def test_classify_gemini_failure_committed(self):
        for code in ["send_state_lost", "post_dispatch_uncommitted_suspected"]:
            failure = classify_gemini_failure("failed", code, dispatch_attempted=True, click_count=1, ui_committed=True)
            self.assertEqual(failure.phase, "UI_COMMITTED", f"Failed for {code}")
            self.assertFalse(failure.retry_safe, f"Should NOT be retry_safe for {code}")
            self.assertEqual(failure.click_count, 1, f"Click count should be 1 for {code}")

    def test_classify_gemini_failure_response(self):
        for code in ["response_turn_not_found", "response_stalled", "response_stream_no_text", "timeout"]:
            failure = classify_gemini_failure("timeout", code)
            self.assertEqual(failure.phase, "UNKNOWN", f"Failed for {code}")
            self.assertFalse(failure.retry_safe, f"Should NOT be retry_safe for {code}")
            self.assertIsNone(failure.click_count)

    def test_failure_names_do_not_prove_no_dispatch(self):
        for status in ("failed", "ready", "dom_unsupported"):
            self.assertFalse(classify_gemini_failure(status, "send_not_ready").retry_safe)
            self.assertFalse(classify_gemini_failure(status, "send_not_ready", dispatch_attempted=True, click_count=1).retry_safe)


if __name__ == "__main__":
    unittest.main()
