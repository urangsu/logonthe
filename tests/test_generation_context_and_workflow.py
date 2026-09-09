import pytest
from unittest.mock import MagicMock, patch
from app.models import FeedPost, PostActionPlan, CommentSubmitState, LikeState, PostProcessResult, LikeProcessResult, CommentProcessResult
from app.processor import PostProcessor, GenerationContext
from services.config import resolve_comment_workflow, CommentWorkflowMode
from services.comments.community_rhythm import CommentDraftInspector, FinalQualityGate
from services.ai_prompt import AIPromptBuilder
from app.state import StateManager


def test_generation_context_lifecycle():
    """GenerationContext가 초기 앵커를 보관하고, 본문 확장 시 앵커를 안전하게 갱신하며 시도 횟수를 추적하는지 검증"""
    ctx = GenerationContext(
        title="제주 흑돼지 맛집 탐방",
        excerpt="제주 공항 근처 흑돼지 구이 먹으러 왔어요.",
        preset="community",
        style="warm_short",
        content_focus="FOOD",
        verified_anchors=["흑돼지"],
        secondary_anchors=["공항"],
        max_attempts=3,
    )

    assert ctx.attempt_count == 0
    assert ctx.secondary_anchors == ["공항"]

    # 1회 빌드
    p1 = ctx.build_prompt()
    assert ctx.attempt_count == 1
    assert "흑돼지" in p1

    # 본문 확장 시 앵커 완전 갱신
    ctx.update_excerpt("제주 흑돼지 삼겹살과 멜젓 조합이 맛있었고 주차도 편했어요.")
    assert "흑돼지" in ctx.verified_anchors or "삼겹살" in ctx.verified_anchors
    assert ctx.excerpt.startswith("제주 흑돼지")

    # 재작성 빌드
    p2 = ctx.build_prompt(rewrite_feedback="조금 더 일상 대화처럼 짧게 작성해 주세요.")
    assert ctx.attempt_count == 2
    assert "조금 더 일상 대화처럼 짧게 작성해 주세요." in p2
    assert len(ctx.rewrite_reasons) == 1


def test_no_sec_anchors_unbound_error_on_inspector_rewrite():
    """초안 검사 실패로 1회 재작성 진입 시 sec_anchors 미참조(UnboundLocalError) 없이 안전하게 재작성 프롬프트가 구성되는지 검증"""
    ctx = GenerationContext(
        title="성수동 소금빵 베이커리 카페",
        excerpt="소금빵 나오는 시간 맞춰서 방문했더니 갓 구워져 나왔어요.",
        preset="community",
        style="warm_short",
        content_focus="FOOD",
        verified_anchors=["소금빵"],
        secondary_anchors=["성수동"],
        max_attempts=3,
    )

    # 1회 생성 (검사 미통과 시나리오 시뮬레이션)
    ai_prompt_initial = ctx.build_prompt()
    assert "소금빵" in ai_prompt_initial

    inspection_failed_feedback = "너무 형식적인 설명조 문장입니다. 본문 경험에 가볍게 공감하는 한 줄로 고쳐주세요."
    # GenerationContext를 통한 재작성 빌드 -> sec_anchors는 항상 ctx.secondary_anchors에 존재하므로 에러 없음
    rewrite_prompt = ctx.build_prompt(
        rewrite_feedback=inspection_failed_feedback,
        recent_repeats="너무 맛있어 보여요",
        recent_comments=["잘 보고 갑니다~"],
    )
    assert ctx.attempt_count == 2
    assert inspection_failed_feedback in rewrite_prompt
    assert "성수동" in rewrite_prompt or "소금빵" in rewrite_prompt


def test_factuality_inspection_blocks_ungrounded_textures_and_causality():
    """본문에 없는 식감 수식어(꼬독꼬독 등)와 날조된 인과관계를 초안 검사기가 차단하는지 검증"""
    post_excerpt = "제주 흑돼지 숯불구이와 현미밥 추가(500원)해서 먹었습니다."

    # 1. 본문에 '꼬독꼬독'이 없는데 AI가 날조한 경우
    hallucinated_comment = "현미밥 추가가 500원이라니 가성비 좋네요! 꼬독꼬독하니 씹는 맛도 있어서 흑돼지랑 잘 어울렸겠어요~"
    result = CommentDraftInspector.inspect(hallucinated_comment, excerpt=post_excerpt)
    assert not result.passed
    assert result.stage == 2
    assert "texture" in result.code
    assert "꼬독꼬독" in result.matched

    # 2. 본문에 인과관계가 없는데 '~해서 더 통통' 날조한 경우
    invented_causality_comment = "흑돼지를 숯불에 구워서 더 통통하고 육즙 가득해 보여요!"
    result2 = CommentDraftInspector.inspect(invented_causality_comment, excerpt=post_excerpt)
    assert not result2.passed
    assert result2.stage == 2
    assert "invented_causality" in result2.code

    # 3. 본문에 근거한 자연스러운 한 줄 반응
    natural_reaction = "현미밥 500원 추가되는 거 진짜 혜자네요 흑돼지랑 든든하셨겠어요!"
    result3 = CommentDraftInspector.inspect(natural_reaction, excerpt=post_excerpt)
    assert result3.passed


def test_resolve_comment_workflow_matrix():
    """댓글 설정 매트릭스 4가지 조합이 올바른 CommentWorkflowConfig로 매핑되는지 검증"""
    # 1. OFF / OFF -> NONE
    cfg1 = resolve_comment_workflow({"comment_enabled": False, "auto_comment_submit_enabled": False})
    assert cfg1.mode == CommentWorkflowMode.NONE
    assert cfg1.effective_comment_enabled is False
    assert cfg1.effective_auto_submit_enabled is False

    # 2. ON / OFF -> DRAFT_REVIEW
    cfg2 = resolve_comment_workflow({"comment_enabled": True, "auto_comment_submit_enabled": False})
    assert cfg2.mode == CommentWorkflowMode.DRAFT_REVIEW
    assert cfg2.effective_comment_enabled is True
    assert cfg2.effective_auto_submit_enabled is False

    # 3. OFF / ON -> AUTO_SUBMIT (불일치 해소: 자동 등록 활성 시 댓글 기능 강제 유효화)
    cfg3 = resolve_comment_workflow({"comment_enabled": False, "auto_comment_submit_enabled": True, "auto_comment_chance": 0.5})
    assert cfg3.mode == CommentWorkflowMode.AUTO_SUBMIT
    assert cfg3.effective_comment_enabled is True
    assert cfg3.effective_auto_submit_enabled is True
    assert cfg3.chance == 0.5

    # 4. ON / ON -> AUTO_SUBMIT
    cfg4 = resolve_comment_workflow({"comment_enabled": True, "auto_comment_submit_enabled": True, "auto_comment_chance": 0.6})
    assert cfg4.mode == CommentWorkflowMode.AUTO_SUBMIT
    assert cfg4.effective_comment_enabled is True
    assert cfg4.effective_auto_submit_enabled is True
    assert cfg4.chance == 0.6


def test_random_sampling_skip_prevents_comment_layer_opening():
    """랜덤 제외된 포스트에서는 댓글창(레이어)을 열지 않고 즉시 SKIPPED 처리하는지 검증"""
    mock_detail_page = MagicMock()
    post = FeedPost(key="testuser:123456789012", url="https://m.blog.naver.com/testuser/123456789012", title="테스트 맛집", source="recommendation")
    mock_detail_page.url = post.url

    # action_plan에서 표본 탈락(comment_sample_selected=False) 지정
    action_plan = PostActionPlan(
        process_like=True,
        process_comment=False,
        comment_sample_selected=False,
        comment_sample_roll=0.85,
    )

    processor = PostProcessor(
        config={"auto_comment_submit_enabled": True, "auto_comment_chance": 0.60},
        like_enabled=False,
        comment_enabled=True,
        auto_comment_submit_enabled=True,
        auto_comment_chance=0.60,
    )

    with patch("naver.interaction.CommentInteractionService.open_comment_layer") as mock_open:
        res = processor.process(mock_detail_page, post, action_plan=action_plan)

        # 댓글창 열기 시도가 전혀 호출되지 않아야 함!
        mock_open.assert_not_called()
        assert res.comment_result.status == CommentSubmitState.SKIPPED
        assert res.comment_result.error == "random_chance_skipped"


def test_user_edit_exempt_from_ai_style_restrictions():
    """사용자가 직접 수정한 댓글은 AI 문체 제한(물결표 2개 초과 등)에서 면제되는지 검증"""
    user_edited_text = "올레시장 맛있는거 진짜 많죠 넘 좋네요~~~"

    # AI 생성물 소스 검증 시에는 물결표 제한에 걸림
    ai_gate = FinalQualityGate.validate_final_text(user_edited_text, source="gemini")
    assert not ai_gate.valid
    assert ai_gate.code == "excessive_tilde"

    # 사용자 직접 수정(user_edit) 소스 검증 시에는 면제되어 통과
    user_gate = FinalQualityGate.validate_final_text(user_edited_text, source="user_edit")
    assert user_gate.valid


def test_status_bar_and_state_manager_disaggregation():
    """StateManager가 sampled_in, sampled_out, gen_success 카운터를 독립적으로 집계하는지 검증"""
    sm = StateManager()
    sm.update(inc_sampled_in=True)
    sm.update(inc_sampled_in=True)
    sm.update(inc_sampled_out=True)
    sm.update(inc_gen_success=True)
    sm.update(inc_comment=True)

    state = sm.get_state()
    assert state.sampled_in_count == 2
    assert state.sampled_out_count == 1
    assert state.generated_success_count == 1
    assert state.comments_count == 1
