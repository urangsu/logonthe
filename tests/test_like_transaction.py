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

    def test_circuit_breaker_reset(self):
        LikeCircuitBreaker.trip("test")
        self.assertTrue(LikeCircuitBreaker.is_open())
        LikeCircuitBreaker.reset()
        self.assertFalse(LikeCircuitBreaker.is_open())


if __name__ == "__main__":
    unittest.main()
