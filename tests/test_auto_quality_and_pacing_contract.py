from types import SimpleNamespace

from services.comments.community_rhythm import FinalQualityGate
from services.pacing import PacingService


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
