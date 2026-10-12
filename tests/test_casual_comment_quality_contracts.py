from types import SimpleNamespace
from unittest.mock import patch

from services.ai_prompt import AIPromptBuilder
from services.comments.policy import CommentStylePolicy
from services.reaction_planner import ReactionContextPlanner
from services.user_learning_service import UserLearningService


def test_explicit_false_decoration_overrides_learning():
    profile = SimpleNamespace(total_samples=20, laughter_ratio=0.8, emoji_ratio=0.8)
    explicit = CommentStylePolicy.from_context(
        config={"allow_soft_laughter": False, "allow_soft_emoji": False}, style_profile=profile)
    assert not explicit.allow_soft_laughter
    assert not explicit.allow_soft_emoji
    inferred = CommentStylePolicy.from_context(config={}, style_profile=profile)
    assert inferred.allow_soft_emoji


def test_food_anchor_does_not_select_business_region_or_ordinal():
    plan = ReactionContextPlanner.plan(
        "오남 맛집 이자카야 우규 불닭도삭면육회 대왕연어초밥 솔직후기",
        "이자카야 우규에서 두 번째로 주문한 육회와 연어초밥이 푸짐했어요",
    )
    assert plan.domain == "FOOD"
    assert plan.primary_anchor not in ("이자카야", "우규", "오남", "번째")
    assert plan.secondary_anchor not in ("이자카야", "우규", "오남", "번째")
    candidates = ReactionContextPlanner.extract_open_vocabulary_food_candidates(
        "신라호텔 파크뷰 만두 맛집 방문후기", "만두가 육즙이 풍부해요")
    assert "신라호텔" not in candidates
    assert "파크뷰" not in candidates


def test_recent_style_example_is_downweighted_without_importing_automatic_text():
    items = [
        {"final_submitted": "국물 한 숟갈이면 좋겠네요~", "source_type": "user_edit", "category": "FOOD"},
        {"final_submitted": "만두피 얇은 거 눈에 들어오네요 ㅎㅎ", "source_type": "user_edit", "category": "FOOD"},
    ]
    with patch.object(UserLearningService, "get_cleaned_corpus", return_value=items):
        examples, _ = UserLearningService.select_dynamic_examples(
            category="FOOD", limit=1, raw_entries=items,
            recent_comments=[items[1]["final_submitted"]])
    assert examples == [items[0]["final_submitted"]]


def test_active_prompt_addresses_repeated_phrases_not_only_endings():
    prompt = AIPromptBuilder.build_v3_5(
        title="칼국수 후기", excerpt="칼국수 국물이 진해요",
        recent_comments=["만두 조합이라니 궁금하네요", "국수 조합이라니 든든하겠어요", "치즈 조합이라니 신기하네요"],
    )
    assert "조합이라니" in prompt
    assert "최근 반복 표현" in prompt
    assert "금지어" not in prompt


def test_other_domains_do_not_receive_food_examples_or_observer_voice():
    for title, excerpt in (
        ("무선청소기 사용기", "배터리가 오래가고 흡입력이 만족스러워요"),
        ("강릉 해변 여행", "산책로에서 바다와 일몰을 볼 수 있어요"),
    ):
        plan = ReactionContextPlanner.plan(title, excerpt)
        assert plan.domain != "FOOD"
        assert "관찰자 입장" not in plan.reaction_instruction
    food_example = {"final_submitted": "장어 비주얼이 좋아요~", "source_type": "user_edit", "category": "FOOD"}
    with patch.object(UserLearningService, "get_cleaned_corpus", return_value=[food_example]):
        examples, stats = UserLearningService.select_dynamic_examples(category="PRODUCT", raw_entries=[food_example])
    assert examples == []
    assert stats["referenced"] == 0


def test_user_voice_principles_are_compact_and_do_not_import_visit_stories():
    prompt = AIPromptBuilder.build_v3_5(
        title="만두 후기", excerpt="만두피가 얇고 속이 꽉 차 있어요",
        reaction_instruction="만두피에 반응해",
    )
    assert "읽고 든 느낌 한 가지만 툭" in prompt
    assert "요약·과장·상투적 인사" in prompt
    assert "없는 사실·사진 묘사·방문·시식 경험" in prompt
    assert "작성자의 좋고 아쉬운 마음을 바꾸지 마" in prompt
    assert "116개" not in prompt
    assert "김유랑" not in prompt
    assert len(prompt) < 500


def test_bereavement_keeps_decoration_options_but_suppresses_food_excitement():
    title = "할아버지 추모"
    excerpt = "함께 먹던 만두가 생각나지만 이제는 떠나보낸 마음이 남아요"
    profile = SimpleNamespace(total_samples=20, laughter_ratio=0.8, emoji_ratio=0.8)
    policy = CommentStylePolicy.from_context(
        config={"allow_soft_laughter": True, "allow_soft_emoji": True},
        style_profile=profile, title=title, excerpt=excerpt)
    assert policy.allow_soft_laughter
    assert policy.allow_soft_emoji
    assert policy.allow_period
    prompt = AIPromptBuilder.build_v3_5(
        title=title, excerpt=excerpt, content_focus="FOOD_RESTAURANT", style_policy=policy,
        corpus_examples=["만두 조합 대박이네요 ㅎㅎ"],
    )
    assert "짧고 담백한 존댓말" in prompt
    assert "만두 조합 대박" not in prompt
    assert "솔직한 끌림" not in prompt
    assert "억지 위로·교훈 없이" in prompt
    assert AIPromptBuilder.select_v3_5_style_examples(["예시 ㅎㅎ"], title, excerpt) == []


def test_serious_full_body_survives_selected_context_truncation():
    prompt = AIPromptBuilder.build_v3_5(
        title="함께한 기억", excerpt="투병하던 가족과 함께 먹던 만두가 생각나요",
        selected_context="함께 먹던 만두가 생각나요", reaction_instruction="조합에 들뜬 반응을 해",
        content_focus="FOOD_RESTAURANT", corpus_examples=["비주얼 대박이네요 ㅎㅎ"],
    )
    assert "짧고 담백한 존댓말" in prompt
    assert "조합에 들뜬 반응" not in prompt
    assert "비주얼 대박" not in prompt


def test_ordinary_cooking_disappointment_is_not_a_bereavement_cue():
    policy = CommentStylePolicy.from_context(
        config={"allow_soft_laughter": True}, title="빵 만들기 실패",
        excerpt="반죽이 잘 안 돼서 아쉽지만 다음에 다시 해보려고요")
    assert policy.allow_soft_laughter


def test_shared_wording_on_different_blogs_is_not_forced_to_change():
    prompt = AIPromptBuilder.build_v3_5(
        title="만두 후기", excerpt="만두피가 얇아요", reaction_instruction="만두피에 반응해",
        recent_comments=["피자 조합이라니 궁금하네요", "떡볶이 조합이라니 신기하네요"])
    assert "최근 반복 표현" not in prompt
    assert "최근 끝맺음" not in prompt
