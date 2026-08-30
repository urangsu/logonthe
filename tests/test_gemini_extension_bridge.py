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
)


class GeminiExtensionBridgeTests(unittest.TestCase):
    def test_preflight_requires_fresh_ready_heartbeat(self):
        bridge = GeminiExtensionBridge(token="test-token")
        self.assertFalse(bridge.preflight().ready)
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app")
        self.assertTrue(bridge.preflight().ready)

    def test_extension_version_mismatch_blocks_preflight(self):
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.2")
        bridge.record_heartbeat("ready", "Gemini", "https://gemini.google.com/app", "13.2.0", "13.2.0")
        self.assertFalse(bridge.preflight().ready)
        self.assertEqual(bridge.preflight().status, "extension_version_mismatch")

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
        bridge = GeminiExtensionBridge(token="test-token")
        command = GeminiCommand.create("post:1", 3, "prompt")
        bridge.publish(command)
        bridge.submit_result(GeminiResult("old", "post:0", 2, GeminiResultStatus.COMPLETED, "wrong"))
        self.assertIsNone(bridge.wait_for_result(command, timeout=0.02))
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, 2, GeminiResultStatus.COMPLETED, "wrong nav"))
        self.assertIsNone(bridge.wait_for_result(command, timeout=0.02))
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, 3, GeminiResultStatus.COMPLETED, "right"))
        result = bridge.wait_for_result(command, timeout=0.1)
        self.assertEqual(result.text, "right")

    def test_failure_result_is_returned_without_local_fallback(self):
        bridge = GeminiExtensionBridge(token="test-token")
        command = GeminiCommand.create("post:2", 1, "prompt")
        bridge.publish(command)
        bridge.submit_result(GeminiResult(command.request_id, command.post_key, 1, GeminiResultStatus.AUTH_REQUIRED, "", "login"))
        result = bridge.wait_for_result(command, timeout=0.1)
        self.assertEqual(result.status, GeminiResultStatus.AUTH_REQUIRED)

    def test_http_server_is_loopback_without_extra_pairing_step(self):
        bridge = GeminiExtensionBridge(token="test-token")
        server = GeminiBridgeHTTPServer(bridge, port=0)
        server.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1)
            connection.request("GET", "/v1/status")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1)
            connection.request("GET", "/v1/status", headers={"Authorization": "Bearer test-token"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            self.assertFalse(payload["ready"])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
