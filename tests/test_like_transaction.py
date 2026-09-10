import unittest
from unittest.mock import MagicMock
from app.models import LikeState
from services.like_transaction import (
    LikeTransactionService, LikeCircuitBreaker, LikeConfidence, LikeStateResult
)


class TestLikeTransaction(unittest.TestCase):
    def setUp(self):
        LikeCircuitBreaker.reset()

    def test_circuit_breaker_trip_and_block(self):
        self.assertFalse(LikeCircuitBreaker.is_open())
        LikeCircuitBreaker.trip("test_unverified_transition")
        self.assertTrue(LikeCircuitBreaker.is_open())

        # 서킷 브레이커가 열리면 클릭을 실행하지 않고 즉시 반환
        page_mock = MagicMock()
        res = LikeTransactionService.execute_like_transaction(page_mock)
        self.assertFalse(res.action_taken)
        self.assertEqual(res.eligibility_reason, "circuit_breaker_open")

    def test_like_click_timeout_recovered_when_post_click_state_is_liked(self):
        """P1: 공감 클릭이 Timeout 2000ms를 초과했더라도 실제 DOM 상태가 LIKED로 전이되었으면 성공 처리하고 서킷 브레이커를 보존"""
        from unittest.mock import patch
        from services.like_transaction import ReactionStateResult, ReactionType, LikeConfidence

        page_mock = MagicMock()
        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True
        like_opt.click.side_effect = Exception("Timeout 2000ms exceeded while waiting for action")

        summary_mock = MagicMock()
        summary_mock.count.return_value = 0

        # Initial state: NOT reacted
        initial_rx = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        # Post click check: already LIKED despite timeout!
        post_rx = ReactionStateResult(reacted=True, reaction_type=ReactionType.LIKE, confidence=LikeConfidence.HIGH)

        with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[initial_rx, post_rx]), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
            res = LikeTransactionService.execute_like_transaction(page_mock)

        self.assertEqual(res.state_after, LikeState.LIKED)
        self.assertTrue(res.action_taken)
        self.assertFalse(LikeCircuitBreaker.is_open(), "클릭 타임아웃 이후 LIKED가 확인되면 서킷 브레이커가 열리지 않아야 함")

    def test_like_click_timeout_trips_circuit_breaker_when_not_liked(self):
        """P1: 공감 클릭 타임아웃 후 상태 확인에서도 LIKED가 아니면 서킷 브레이커 발동"""
        from unittest.mock import patch
        from services.like_transaction import ReactionStateResult, ReactionType, LikeConfidence

        page_mock = MagicMock()
        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True
        like_opt.click.side_effect = Exception("Timeout 2000ms exceeded while waiting for action")

        summary_mock = MagicMock()
        summary_mock.count.return_value = 0

        initial_rx = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        post_rx = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)

        with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[initial_rx, post_rx, post_rx]), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
            res = LikeTransactionService.execute_like_transaction(page_mock)

        self.assertEqual(res.state_after, LikeState.UNKNOWN)
        self.assertTrue(LikeCircuitBreaker.is_open(), "클릭 실패 후 LIKED 전이가 확인되지 않으면 서킷 브레이커 발동")


if __name__ == "__main__":
    unittest.main()
