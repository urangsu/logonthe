from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import threading

import pytest

from services.comments.community_rhythm import FinalQualityGate
from services.pacing import PacingService
from browser.session import WaitInterruptionReason


def test_period_allowed_when_same_style_policy_is_forwarded():
    policy = SimpleNamespace(
        allow_period=True,
        allow_soft_laughter=False,
        allow_soft_emoji=False,
        max_combined_decorations=0,
    )
    text = "승모근부터 팔라인까지 풀어주는 관리라 시원해 보이네요. 저도 궁금하네요"
    result = FinalQualityGate.validate_final_text(
        text,
        preset="community",
        source="user_submission",
        style_policy=policy,
    )
    assert result.valid is True


def test_jamo_emoticon_repair_removes_entire_token():
    text = "추석 시즌 할인 품목이 다양해서 구경하는 재미가 있겠어요~ㅎㅅㅎ"
    gate = FinalQualityGate.validate_final_text(text, preset="community", source="gemini")
    assert gate.valid is False
    assert gate.code == "laughter_or_emoticon"

    repaired, new_gate = FinalQualityGate.auto_repair(
        text,
        gate,
        preset="community",
        source="gemini",
    )
    assert repaired is not None
    assert "ㅎㅅㅎ" not in repaired
    assert not repaired.endswith("ㅅ")
    assert new_gate.valid is True


def test_plan_pre_like_delay_uses_configured_range():
    svc = PacingService({
        "pacing_enabled": True,
        "pre_like_delay_min": 7.0,
        "pre_like_delay_max": 7.0,
    })
    assert svc.plan_pre_like_delay() == 7.0


@pytest.mark.parametrize("method, low_key, high_key", [
    ("wait_page_settle", "page_settle_min", "page_settle_max"),
    ("wait_post_like", "post_like_delay_min", "post_like_delay_max"),
    ("wait_next_post", "next_post_delay_min", "next_post_delay_max"),
])
def test_configured_wait_ranges_and_run_control_are_used(method, low_key, high_key):
    control = MagicMock()
    control.skip_event = threading.Event()
    control.interruptible_wait.return_value = WaitInterruptionReason.COMPLETED
    svc = PacingService({low_key: 12, high_key: 8}, run_control=control)
    with patch("services.pacing.random.uniform", return_value=10) as sample:
        waited = getattr(svc, method)()
    sample.assert_called_once_with(8.0, 12.0)
    assert waited.seconds == 10
    assert control.interruptible_wait.call_args.args[0] == 10


def test_non_finite_and_negative_duration_do_not_create_unbounded_wait():
    svc = PacingService({"min": float("nan"), "max": -10})
    assert svc._range("min", "max", 2, 4) == (0.0, 2)


def test_random_pause_uses_run_control_for_pause_ack_and_stop():
    control = MagicMock()
    control.skip_event = threading.Event()
    control.interruptible_wait.return_value = WaitInterruptionReason.STOPPED
    svc = PacingService({"random_pause_chance": 1, "random_pause_min": 8, "random_pause_max": 8}, run_control=control)
    with patch("services.pacing.random.random", return_value=0):
        waited = svc.maybe_pause()
    assert waited.stopped
    control.interruptible_wait.assert_called_once_with(8, stage="pacing_pause")
