import os
import unittest
from unittest.mock import MagicMock, patch
from app.models import LikeState
from services.like_transaction import (
    LikeTransactionService, LikeCircuitBreaker, ReactionStateResult, ReactionType, LikeConfidence
)
from browser.session import ProfileLockManager, ProfileStatus


class TestRuntimeV4(unittest.TestCase):
    def setUp(self):
        LikeCircuitBreaker.reset()

    def test_no_playwright_pseudo_selectors_in_browser_evaluate(self):
        """interaction.py 내 browser evaluate 문자열에 Playwright 전용 의사선택자(:has-text, :text-is)가 없는지 검증"""
        interaction_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "naver", "interaction.py"))
        with open(interaction_path, "r", encoding="utf-8") as f:
            content = f.read()

        # evaluate 내부 JS 블록에서 :has-text(:text-is) 검증
        # evaluate(""" ... """) 블록 찾기
        import re
        eval_blocks = re.findall(r'page\.evaluate\("""(.*?)"""\)', content, re.DOTALL)
        for block in eval_blocks:
            self.assertNotIn(":has-text(", block, "Found Playwright-only :has-text() inside browser evaluate JS block!")
            self.assertNotIn(":text-is(", block, "Found Playwright-only :text-is() inside browser evaluate JS block!")

    def test_like_summary_direct_activation_path(self):
        """요약 버튼 상호작용 후 공감이 즉시 활성화되는 경우(Summary Direct Activation) 회귀 검증"""
        page_mock = MagicMock()
        # 1. resolve_reaction_state -> NONE
        # 2. after summary click -> LIKE
        page_mock.evaluate.side_effect = [
            {"reacted": False, "reaction_type": "none", "confidence": "high", "signals": ["all_options_explicit_off"]},
            {"reacted": True, "reaction_type": "like", "confidence": "high", "signals": ["opt_like_on"]}
        ]
        summary_btn_mock = MagicMock()
        summary_btn_mock.count.return_value = 1
        page_mock.locator.return_value.first = summary_btn_mock

        with patch("naver.resolver.MobileDOMResolver.get_reaction_like_option") as mock_like_opt, \
             patch("naver.resolver.MobileDOMResolver.get_reaction_summary_button") as mock_summary:
            mock_like_opt.return_value.count.return_value = 1
            mock_like_opt.return_value.is_visible.return_value = False
            mock_summary.return_value = summary_btn_mock

            res = LikeTransactionService.execute_like_transaction(page_mock)
            self.assertTrue(res.action_taken)
            self.assertEqual(res.state_after, LikeState.LIKED)
            self.assertFalse(LikeCircuitBreaker.is_open())

    def test_like_option_hidden_timeout_does_not_open_circuit(self):
        """옵션 레이어가 열리지 않은 경우 해당 글만 skip되고 서킷 브레이커는 열리지 않음을 검증"""
        page_mock = MagicMock()
        page_mock.evaluate.side_effect = [
            {"reacted": False, "reaction_type": "none", "confidence": "high", "signals": ["all_options_explicit_off"]},
            {"reacted": False, "reaction_type": "none", "confidence": "high", "signals": ["all_options_explicit_off"]}
        ]
        summary_btn_mock = MagicMock()
        summary_btn_mock.count.return_value = 1
        like_opt_mock = MagicMock()
        like_opt_mock.count.return_value = 1
        like_opt_mock.is_visible.return_value = False
        like_opt_mock.wait_for.side_effect = Exception("timeout")

        with patch("naver.resolver.MobileDOMResolver.get_reaction_like_option") as mock_like_opt, \
             patch("naver.resolver.MobileDOMResolver.get_reaction_summary_button") as mock_summary:
            mock_like_opt.return_value = like_opt_mock
            mock_summary.return_value = summary_btn_mock

            res = LikeTransactionService.execute_like_transaction(page_mock)
            self.assertFalse(res.action_taken)
            self.assertEqual(res.error, "reaction_option_not_visible")
            self.assertFalse(LikeCircuitBreaker.is_open(), "Circuit breaker should remain CLOSED when option was not visible!")


if __name__ == "__main__":
    unittest.main()
