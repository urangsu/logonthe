import pytest

from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import FinalQualityGate
from services.comments.policy import CommentStylePolicy
from services.comments.style_normalizer import CommentStyleNormalizer


@pytest.mark.parametrize("text", [
    "갓덴스시 맛있긴 한데 몇 접시 집다 보면 가격 진짜 훅 나오더라구요",
    "몇 접시 집다 보면 가격이 훅 나오더라고요",
    "먹다 보니 금방 질리더라구요",
    "직접 갔더니 웨이팅이 길었어요",
])
def test_unprovided_implicit_experience_is_rejected(text):
    result = FinalQualityGate.validate(text, source="gemini")
    assert not result.valid
    assert result.code == "fake_experience"


@pytest.mark.parametrize("text", [
    "몇 접시만 골라도 가격이 꽤 나올 것 같아요",
    "가격이 생각보다 나왔다니 조금 아쉬우셨겠어요",
    "비주얼이 좋더라구요",
    "오래 기다리셨다니 지치셨겠어요",
    "저도 한번 먹어보고 싶네요",
])
def test_observation_attribution_and_future_interest_remain_allowed(text):
    assert FinalQualityGate.validate(text, source="gemini").code != "fake_experience"


@pytest.mark.parametrize("emoji", ["🥹", "🥲", "🙂", "☺️", "😌", "🤭", "🫶", "🍀", "💛"])
def test_soft_emojis_survive_normalization_and_final_gate(emoji):
    text = f"기다리셨는데 품절이라니 아쉬우셨겠어요 {emoji}"
    result = CommentStyleNormalizer.normalize_style(
        text, post_key="emoji_voice_recovery", recent_comments=[text],
        allow_emoji_sampling=False, emoji_history_window=0,
        allow_tilde_when_no_emoji=False,
    )
    assert result.text == text
    assert result.emoji_action == "kept"
    policy = CommentStylePolicy.from_context(config={"allow_soft_emoji": True})
    assert FinalQualityGate.validate(result.text, source="gemini", style_policy=policy).valid


def test_food_theme_survives_repeated_marker_and_sentence_guidance():
    prompt = AIPromptBuilder.build_v3_5(
        title="만두 맛집 후기", excerpt="만두피가 얇고 속이 꽉 차요",
        recent_comments=["피자 조합이라니 좋겠어요 😋", "국수 조합이 좋겠어요 😋", "만두 조합이 좋겠어요"],
        style_policy=CommentStylePolicy.from_context(config={"allow_soft_emoji": True}),
    )
    assert "음식 글:" in prompt
    assert "작성자의 경험을 내 경험으로" in prompt
    assert "최근 반복 표현" in prompt
    assert "문장 틀" in prompt
    assert len(prompt) < 650


def test_product_theme_does_not_receive_food_instruction():
    prompt = AIPromptBuilder.build_v3_5(
        title="무선청소기 사용기", excerpt="배터리가 오래가고 흡입력이 만족스러워요",
        corpus_examples=["배터리가 오래가는 점이 좋네요"],
    )
    assert "제품 글:" in prompt
    assert "음식 글:" not in prompt


def test_serious_theme_overrides_food_and_playful_examples():
    prompt = AIPromptBuilder.build_v3_5(
        title="할아버지를 떠나보내고", excerpt="함께 먹던 만두가 생각나요",
        corpus_examples=["만두 맛있겠어요 😋"],
    )
    assert "음식 글:" not in prompt
    assert "만두 맛있겠어요" not in prompt
