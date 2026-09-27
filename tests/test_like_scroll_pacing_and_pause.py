import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.models import LikeState
from services.like_transaction import (
    LikeCircuitBreaker,
    LikeConfidence,
    LikeProcessResult,
    LikeTransactionService,
    ReactionStateResult,
    ReactionType,
)


class TestLikeScrollPacingAndPause(unittest.TestCase):
    def setUp(self):
        LikeCircuitBreaker.reset()
        self.mock_page = MagicMock()

    def test_pre_like_click_never_faster_than_target_deadline(self):
        """1. 스크롤 유무와 관계없이 공감 클릭은 설정한 목표시각보다 빠르지 않아야 함"""
        target_delay = 0.35
        now_mono = time.monotonic()
        not_before = now_mono + target_delay

        rx_before = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        rx_after = ReactionStateResult(reacted=True, reaction_type=ReactionType.LIKE, confidence=LikeConfidence.HIGH)

        click_times = []
        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True

        def _record_click(*args, **kwargs):
            click_times.append(time.monotonic())

        like_opt.click.side_effect = _record_click

        summary_mock = MagicMock()
        summary_mock.count.return_value = 0

        # 짧은 글 (스크롤 없음)
        self.mock_page.evaluate.return_value = {"maxScroll": 100}

        with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[rx_before, rx_after]), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
            res = LikeTransactionService.execute_like_transaction(
                self.mock_page,
                not_before_monotonic=not_before,
            )

        self.assertTrue(res.action_taken)
        self.assertEqual(len(click_times), 1)
        self.assertGreaterEqual(click_times[0], not_before - 0.02, "클릭 시각은 목표시각보다 앞서면 안 됨")

    def test_pause_event_prevents_scroll_and_click_until_resumed(self):
        """2. 일시정지 중 스크롤·공감 클릭은 0회여야 함"""
        pause_event = threading.Event()
        pause_event.set()  # 일시정지 활성화

        rx_before = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        rx_after = ReactionStateResult(reacted=True, reaction_type=ReactionType.LIKE, confidence=LikeConfidence.HIGH)

        scroll_evals = []
        click_calls = []

        def _mock_eval(script):
            if "scrollBy" in script:
                scroll_evals.append(script)
            return {"maxScroll": 1000}

        self.mock_page.evaluate.side_effect = _mock_eval

        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True
        like_opt.click.side_effect = lambda *args, **kwargs: click_calls.append(True)

        summary_mock = MagicMock()
        summary_mock.count.return_value = 0

        not_before = time.monotonic() + 0.2

        thread_done = threading.Event()
        exec_result = []

        def _worker():
            with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[rx_before, rx_after]), \
                 patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
                 patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
                res = LikeTransactionService.execute_like_transaction(
                    self.mock_page,
                    not_before_monotonic=not_before,
                    pause_event=pause_event,
                )
                exec_result.append(res)
                thread_done.set()

        t = threading.Thread(target=_worker)
        t.start()

        # 일시정지 중 0.15초 대기
        time.sleep(0.15)
        self.assertEqual(len(scroll_evals), 0, "일시정지 중 스크롤은 0회여야 함")
        self.assertEqual(len(click_calls), 0, "일시정지 중 클릭은 0회여야 함")
        self.assertFalse(thread_done.is_set(), "일시정지 중에는 함수가 완료되지 않아야 함")

        # 일시정지 해제
        pause_event.clear()
        thread_done.wait(timeout=2.0)
        self.assertTrue(thread_done.is_set())
        self.assertEqual(len(click_calls), 1, "재개 후 정상적으로 1회 클릭 수행")

    def test_short_body_skips_scrolling(self):
        """3. 짧은 글이나 이동할 영역이 없으면 스크롤을 생략"""
        now_mono = time.monotonic()
        not_before = now_mono + 0.1

        rx_before = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        rx_after = ReactionStateResult(reacted=True, reaction_type=ReactionType.LIKE, confidence=LikeConfidence.HIGH)

        scroll_calls = []

        def _mock_eval(script):
            if "scrollBy" in script:
                scroll_calls.append(script)
            return {"maxScroll": 200}  # 짧은 글 (maxScroll < 400)

        self.mock_page.evaluate.side_effect = _mock_eval

        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True

        summary_mock = MagicMock()
        summary_mock.count.return_value = 0

        with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[rx_before, rx_after]), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
            LikeTransactionService.execute_like_transaction(
                self.mock_page,
                not_before_monotonic=not_before,
            )

        self.assertEqual(len(scroll_calls), 0, "짧은 글에서는 스크롤이 생략되어야 함")

    def test_long_body_with_sufficient_time_scrolls_2_to_4_times(self):
        """4. 본문이 충분히 길고 시간이 남은 글에서만 2~4회 작은 폭 스크롤"""
        now_mono = time.monotonic()
        not_before = now_mono + 3.2  # 남은 시간 3.2초 (충분)

        rx_before = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        rx_after = ReactionStateResult(reacted=True, reaction_type=ReactionType.LIKE, confidence=LikeConfidence.HIGH)

        scroll_calls = []

        def _mock_eval(script):
            if "scrollBy" in script:
                scroll_calls.append(script)
            return {"maxScroll": 1200}  # 긴 글

        self.mock_page.evaluate.side_effect = _mock_eval

        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True

        summary_mock = MagicMock()
        summary_mock.count.return_value = 0

        with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[rx_before, rx_after]), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
            LikeTransactionService.execute_like_transaction(
                self.mock_page,
                not_before_monotonic=not_before,
            )

        self.assertGreaterEqual(len(scroll_calls), 2)
        self.assertLessEqual(len(scroll_calls), 4)

    def test_summary_button_scrolled_into_view_at_end_of_wait(self):
        """5. 공감 버튼 선이동이 대기 끝으로 옮겨졌는지 호출 순서 검증"""
        event_order = []

        now_mono = time.monotonic()
        not_before = now_mono + 0.1

        rx_before = ReactionStateResult(reacted=False, reaction_type=ReactionType.NONE, confidence=LikeConfidence.HIGH)
        rx_after = ReactionStateResult(reacted=True, reaction_type=ReactionType.LIKE, confidence=LikeConfidence.HIGH)

        self.mock_page.evaluate.side_effect = lambda script: {"maxScroll": 100}

        summary_mock = MagicMock()
        summary_mock.count.return_value = 1
        summary_mock.scroll_into_view_if_needed.side_effect = lambda *a, **kw: event_order.append("summary_scroll")

        like_opt = MagicMock()
        like_opt.count.return_value = 1
        like_opt.is_visible.return_value = True
        like_opt.click.side_effect = lambda *a, **kw: event_order.append("like_click")

        with patch("services.like_transaction.LikeTransactionService.resolve_reaction_state", side_effect=[rx_before, rx_after]), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_like_option", return_value=like_opt), \
             patch("services.like_transaction.MobileDOMResolver.get_reaction_summary_button", return_value=summary_mock):
            LikeTransactionService.execute_like_transaction(
                self.mock_page,
                not_before_monotonic=not_before,
            )

        self.assertEqual(event_order, ["summary_scroll", "like_click"])


if __name__ == "__main__":
    unittest.main()
