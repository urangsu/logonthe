import json
import re
import unittest
from pathlib import Path

from services.gemini_extension_bridge import GeminiCommand

ROOT = Path(__file__).resolve().parent.parent


class GeminiChatReuseContractTest(unittest.TestCase):
    def test_default_command_payload_is_unchanged(self):
        payload = GeminiCommand.create("p", 1, "prompt").to_json()
        self.assertNotIn("reuseChat", payload)
        self.assertNotIn("maxReuseTurns", payload)

    def test_reuse_flag_is_serialized_only_when_enabled(self):
        payload = GeminiCommand.create("p", 1, "prompt", reuse_chat=True, max_reuse_turns=12).to_json()
        self.assertIs(payload["reuseChat"], True)
        self.assertEqual(payload["maxReuseTurns"], 12)

    def test_max_reuse_turns_never_below_one(self):
        cmd = GeminiCommand.create("p", 1, "prompt", reuse_chat=True, max_reuse_turns=0)
        self.assertGreaterEqual(cmd.max_reuse_turns, 1)

    def test_extension_requires_empty_composer_for_reuse(self):
        content = (ROOT / "browser_extension" / "content.js").read_text(encoding="utf-8")
        self.assertIn("command.reuseChat === true && composerReady", content)
        background = (ROOT / "browser_extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("tryReuseGeminiConversation", background)
        self.assertIn("res.composerEmpty", background)
        # 한도 초과 시 새 채팅으로 되돌아가야 한다
        self.assertIn("reuse_turn_limit", background)

    def test_runtime_build_is_consistent_across_extension_files(self):
        contract = json.loads((ROOT / "browser_extension" / "runtime_contract.json").read_text(encoding="utf-8"))
        content = (ROOT / "browser_extension" / "content.js").read_text(encoding="utf-8")
        match = re.search(r"runtimeBuild:\s*'([^']+)'", content)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), contract["runtimeBuild"])


if __name__ == "__main__":
    unittest.main()
