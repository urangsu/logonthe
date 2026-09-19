import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.run_control import RunControl, RunControlState, StopRequestedException
from app.models import FeedPost
from browser.session import WaitInterruptionReason
from app.processor import PostProcessor
from services.pacing import PacingService


class TestRunControlPauseAckAndSkip(unittest.TestCase):
    def test_pause_ack_before_side_effect(self):
        """
        When pause is requested, the worker must transition to PAUSED and signal
        pause_ack_event at the next checkpoint BEFORE executing any side effect.
        """
        rc = RunControl()
        checkpoint_reached = threading.Event()
        side_effect_executed = threading.Event()

        def worker():
            rc.checkpoint("before_side_effect")
            checkpoint_reached.set()
            side_effect_executed.set()

        rc.request_pause("test_pause")
        self.assertEqual(rc.state, RunControlState.PAUSE_REQUESTED)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Wait for worker to hit checkpoint and ack
        self.assertTrue(rc.pause_ack_event.wait(timeout=1.0))
        self.assertEqual(rc.state, RunControlState.PAUSED)
        # Side effect has NOT executed yet because worker is paused at the checkpoint
        self.assertFalse(side_effect_executed.is_set())

        # Now resume
        rc.request_resume()
        t.join(timeout=1.0)
        self.assertTrue(side_effect_executed.is_set())
        self.assertEqual(rc.state, RunControlState.RUNNING)

    def test_pause_right_before_scroll_blocks_scroll(self):
        """
        Pausing right before scroll ensures 0 scroll/load_more calls occur while paused.
        """
        rc = RunControl()
        scroll_mock = MagicMock()

        def worker():
            rc.checkpoint("before_scroll")
            scroll_mock()

        rc.request_pause("pause_before_scroll")
        t = threading.Thread(target=worker, daemon=True)
        t.start()

        self.assertTrue(rc.pause_ack_event.wait(timeout=1.0))
        self.assertEqual(scroll_mock.call_count, 0)

        # Still 0 calls while paused
        time.sleep(0.1)
        self.assertEqual(scroll_mock.call_count, 0)

        rc.request_resume()
        t.join(timeout=1.0)
        self.assertEqual(scroll_mock.call_count, 1)

    def test_skip_during_pause_bound_to_active_post(self):
        """
        Skip requested during pause is bound strictly to the active_post_key.
        It is consumed on that post and does NOT leak to a subsequent post.
        """
        rc = RunControl()
        post1_key = "post_111"
        post2_key = "post_222"

        rc.set_active_post(post1_key)
        rc.request_pause("user_pause")

        # User requests skip while paused
        skipped = rc.request_skip()
        self.assertTrue(skipped)
        self.assertEqual(rc.pending_skip_post_key, post1_key)

        # Resume execution
        rc.request_resume()

        # Post 1 entry consumes the skip
        self.assertTrue(rc.consume_pending_skip(post1_key))
        self.assertIsNone(rc.pending_skip_post_key)

        # Post 2 must NOT receive this skip
        rc.set_active_post(post2_key)
        self.assertFalse(rc.consume_pending_skip(post2_key))
        self.assertFalse(rc.skip_event.is_set())

    def test_pacing_disabled_or_zero_delay_still_evaluates_pause(self):
        """
        Even if pacing is disabled or wait time is 0.0s, interruptible_wait must evaluate pause checkpoints.
        """
        rc = RunControl()
        pacing = PacingService(
            config={"pacing_enabled": False, "action_delay_min": 0, "action_delay_max": 0},
            run_control=rc,
        )

        rc.request_pause("test_zero_delay")
        paused_at_checkpoint = threading.Event()

        def worker():
            res = pacing.wait_action()
            paused_at_checkpoint.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        self.assertTrue(rc.pause_ack_event.wait(timeout=1.0))
        self.assertEqual(rc.state, RunControlState.PAUSED)
        self.assertFalse(paused_at_checkpoint.is_set())

        rc.request_resume()
        t.join(timeout=1.0)
        self.assertTrue(paused_at_checkpoint.is_set())

    def test_skip_does_not_clear_manual_pause(self):
        """
        Manual pause is cleared ONLY by explicit resume.
        Sending a skip request does NOT clear pause state.
        """
        rc = RunControl()
        rc.set_active_post("active_post_1")
        rc.request_pause("manual_pause")

        self.assertEqual(rc.state, RunControlState.PAUSE_REQUESTED)
        rc.request_skip()

        # Pause state must remain active
        self.assertTrue(rc.is_paused())
        self.assertIn(rc.state, (RunControlState.PAUSE_REQUESTED, RunControlState.PAUSED))


if __name__ == "__main__":
    unittest.main()
