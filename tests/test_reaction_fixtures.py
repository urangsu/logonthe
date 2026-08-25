import unittest
from unittest.mock import MagicMock
from app.models import LikeState
from services.like_transaction import (
    LikeTransactionService, ReactionStateResult, ReactionType, LikeConfidence, LikeCircuitBreaker
)


class TestReactionDOMModel(unittest.TestCase):
    def setUp(self):
        LikeCircuitBreaker.reset()

    def test_neutral_summary_is_not_liked_high(self):
        """요약 버튼(a.u_likeit_button) 하나만으로 NOT_LIKED HIGH가 되지 않음을 검증"""
        page_mock = MagicMock()
        page_mock.evaluate.return_value = {
            "reacted": False,
            "reaction_type": "unknown",
            "confidence": "unknown",
            "signals": ["options_not_found"]
        }
        res = LikeTransactionService.resolve_reaction_state(page_mock)
        self.assertFalse(res.reacted)
        self.assertEqual(res.reaction_type, ReactionType.UNKNOWN)
        self.assertNotEqual(res.confidence, LikeConfidence.HIGH)

    def test_explicit_all_options_off_is_none_high(self):
        page_mock = MagicMock()
        page_mock.evaluate.return_value = {
            "reacted": False,
            "reaction_type": "none",
            "confidence": "high",
            "signals": ["all_options_explicit_off"]
        }
        res = LikeTransactionService.resolve_reaction_state(page_mock)
        self.assertFalse(res.reacted)
        self.assertEqual(res.reaction_type, ReactionType.NONE)
        self.assertEqual(res.confidence, LikeConfidence.HIGH)

    def test_like_option_on_is_liked_high(self):
        page_mock = MagicMock()
        page_mock.evaluate.return_value = {
            "reacted": True,
            "reaction_type": "like",
            "confidence": "high",
            "signals": ["opt_like_on"]
        }
        res = LikeTransactionService.resolve_reaction_state(page_mock)
        self.assertTrue(res.reacted)
        self.assertEqual(res.reaction_type, ReactionType.LIKE)
        self.assertEqual(res.confidence, LikeConfidence.HIGH)

    def test_other_reaction_active_preserves_choice(self):
        """칭찬/감사/웃김 등 다른 리액션이 활성화된 경우 ALREADY_REACTED 처리"""
        page_mock = MagicMock()
        page_mock.evaluate.return_value = {
            "reacted": True,
            "reaction_type": "impressive",
            "confidence": "high",
            "signals": ["opt_impressive_on"]
        }
        res = LikeTransactionService.resolve_reaction_state(page_mock)
        self.assertTrue(res.reacted)
        self.assertEqual(res.reaction_type, ReactionType.IMPRESSIVE)

        # execute_like_transaction 호출 시 클릭하지 않고 즉시 반환
        tx_res = LikeTransactionService.execute_like_transaction(page_mock)
        self.assertFalse(tx_res.action_taken)
        self.assertEqual(tx_res.state_after, LikeState.LIKED)


if __name__ == "__main__":
    unittest.main()
