from services.ai_prompt import AIPromptBuilder, PROMPT_VERSION_V3_4
from services.comments.community_rhythm import FinalQualityGate
from services.comments.policy import CommentStylePolicy


def test_v3_4_prompt_prioritizes_youthful_mobile_voice_for_concern_post():
    policy = CommentStylePolicy.from_context(
        preset="community",
        config={"allow_soft_laughter": True, "allow_soft_emoji": False},
    )
    prompt = AIPromptBuilder.build(
        title="추석 저녁부터 비, 귀경길 안전 주의",
        excerpt="비가 내리고 도로가 미끄러울 수 있어 이동 시 주의가 필요합니다.",
        content_focus="GENERAL",
        style_policy=policy,
        version=PROMPT_VERSION_V3_4,
    )

    assert "20대" in prompt
    assert "모바일" in prompt
    assert "마침표(.) 없이" in prompt
    assert "ㅎㅎ·ㅠㅠ·ㅜㅜ" in prompt
    assert "뉴스나 안내문처럼 정보를 다시 전달하지" in prompt
    assert "아쉬움이나 걱정" in prompt
    assert "장식 없음" not in prompt


def test_community_policy_allows_one_soft_text_reaction_but_not_period():
    policy = CommentStylePolicy.from_context(
        preset="community",
        config={"allow_soft_laughter": True, "allow_soft_emoji": False},
    )

    assert policy.allow_period is False
    assert policy.allow_soft_laughter is True
    assert FinalQualityGate.validate_final_text(
        "보름달 기대했는데 비 소식이라 조금 아쉽네요 ㅠㅠ",
        preset="community",
        source="gemini",
        style_policy=policy,
    ).valid
    assert not FinalQualityGate.validate_final_text(
        "보름달 기대했는데 비 소식이라 조금 아쉽네요.",
        preset="community",
        source="gemini",
        style_policy=policy,
    ).valid
    assert not FinalQualityGate.validate_final_text(
        "보름달 기대했는데 아쉽네요 ㅠㅠ ㅎㅎ",
        preset="community",
        source="gemini",
        style_policy=policy,
    ).valid


def test_v3_4_does_not_feed_mismatched_or_suffix_examples_back_to_gemini():
    prompt = AIPromptBuilder.build(
        title="비 오는 귀경길",
        excerpt="오후부터 비가 내릴 전망입니다.",
        content_focus="GENERAL",
        corpus_examples=[
            "승모근 관리가 시원하겠어요. 저도 받아보고 싶네요\n즐거운 추석 보내세요~",
            "담백한 메뉴 조합이라 넘 맛있겠는데용~",
        ],
        version=PROMPT_VERSION_V3_4,
    )

    assert "승모근" not in prompt
    assert "담백한 메뉴" not in prompt
    assert "즐거운 추석 보내세요" not in prompt
