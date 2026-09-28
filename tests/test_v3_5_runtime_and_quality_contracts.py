"""tests/test_v3_5_runtime_and_quality_contracts.py
v3.5 Reaction Pipeline 런타임 확정 및 품질 계약 검증 스위트.
- P0: 런타임 기본값 3.5 전환 및 마이그레이션 계약
- P0: needs_more_context의 실제 제어 흐름(본문 확장 / SKIP) 연결
- P0: WHAT TO SAY (ReactionPlan) vs HOW TO SAY (StylePlan) 분리
- P1: 도메인 분류 근거 점수제 (주말/아이 여행 -> PLACE)
- P1: FOOD reaction_mode 우선순위 (조합 != 2개 메뉴 언급, 맛/식감 -> taste_reaction)
- P1: 3단계 음식 연관성(Direct / Grounded Sensory / Semantic Signal)
- P1: Context Selector 가중치(preferred_anchors / preferred_terms) 보존
- P1: Context retry 시 StylePolicy 및 config 보존
"""
import unittest
from unittest.mock import MagicMock, patch
from naver.comment_guard import CommentPresenceState, CommentPresenceResult

from services.config import DEFAULT_CONFIG_V2, migrate_ai_prompt_version, ConfigService
from services.ai_prompt import AIPromptBuilder, PROMPT_VERSION_V3_5
from services.reaction_planner import ReactionContextPlanner, ReactionPlan
from services.style_service import StylePlanService
from services.comment_context_selector import select_comment_context
from services.comments.policy import CommentStylePolicy
from app.models import FeedPost, FeedSourceType, CommentSubmitState, StylePlan
from app.processor import PostProcessor, GenerationContext, check_food_relevance


class TestV35RuntimeAndQualityContracts(unittest.TestCase):

    # -------------------------------------------------------------
    # P0. 런타임 기본값 및 마이그레이션 계약
    # -------------------------------------------------------------

    def test_runtime_default_prompt_is_v35(self):
        """런타임 기본 프롬프트 버전이 3.5.0-reaction-planned인지 검증"""
        self.assertEqual(DEFAULT_CONFIG_V2["ai_prompt_version"], "3.5.0-reaction-planned")
        self.assertEqual(AIPromptBuilder.PROMPT_VERSION, "3.5.0-reaction-planned")

        proc = PostProcessor(config={})
        self.assertEqual(proc.ai_prompt_version, "3.5.0-reaction-planned")

        ctx = GenerationContext(
            title="테스트 글",
            excerpt="테스트 본문",
            preset="community",
            style="warm_short",
            content_focus="GENERAL",
            verified_anchors=[],
            secondary_anchors=[],
        )
        self.assertEqual(ctx.prompt_version, "3.5.0-reaction-planned")

    def test_config_34_migrates_to_35(self):
        """기존 3.0~3.4 설정이 로드 시 3.5로 자동 승격되는지 검증"""
        legacy_versions = [
            "3.0", "3.1", "3.2", "3.3", "3.4",
            "3.0.0-grounded-human", "3.1.0-grounded-human",
            "3.2.0-grounded-human", "3.3.0-context-lean", "3.4.0-youthful-mobile"
        ]
        for ver in legacy_versions:
            migrated = migrate_ai_prompt_version({"ai_prompt_version": ver})
            self.assertEqual(
                migrated["ai_prompt_version"],
                "3.5.0-reaction-planned",
                f"Version {ver} failed to migrate to 3.5",
            )

        # 미설정 빈 문자열도 3.5 승격
        self.assertEqual(
            migrate_ai_prompt_version({})["ai_prompt_version"],
            "3.5.0-reaction-planned",
        )

    def test_processor_uses_v35_after_config_load(self):
        """기존 설정 파일이 3.4를 담고 있어도 Processor가 3.5를 사용하는지 검증"""
        raw_cfg = {"ai_prompt_version": "3.4.0-youthful-mobile"}
        migrated_cfg = migrate_ai_prompt_version(raw_cfg)
        proc = PostProcessor(config=migrated_cfg)
        self.assertEqual(proc.ai_prompt_version, "3.5.0-reaction-planned")

    # -------------------------------------------------------------
    # P0. needs_more_context 제어 흐름 연결
    # -------------------------------------------------------------

    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high"))
    def test_needs_more_context_expands_or_skips_before_gemini(self, mock_dup_scan, mock_open):
        """본문 근거가 전무(background only)할 때 Gemini를 호출하지 않고 SKIP되는지 검증"""
        proc = PostProcessor(
            config={"ai_prompt_version": "3.5.0-reaction-planned"},
            auto_comment_submit_enabled=True,
            auto_comment_chance=1.0,
            gemini_web_enabled=True,
            like_enabled=False,
        )
        proc.gemini_extension_bridge = MagicMock()

        post = FeedPost(
            key="test_blog:999",
            url="https://m.blog.naver.com/test_blog/999",
            title="일상의 기록",
            # 사실, 감정, 반전이 없는 단순 배경 문장
            excerpt="날씨가 맑은 날이었습니다.\n하늘이 파랗게 펼쳐져 있었습니다.\n바람도 살랑살랑 불었습니다.",
            source=FeedSourceType.NEIGHBOR,
        )

        detail_page_mock = MagicMock()
        detail_page_mock.url = post.url
        res = proc.process(detail_page=detail_page_mock, post=post)

        # 댓글 작성이 SKIP되고 에러 사유가 insufficient_context여야 함
        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        self.assertEqual(res.comment_result.error, "insufficient_context")
        # 제미나이 브리지로 명령이 발행되지 않아야 함
        proc.gemini_extension_bridge.publish.assert_not_called()

    # -------------------------------------------------------------
    # P0. StylePlan과 ReactionPlan의 완전한 분리
    # -------------------------------------------------------------

    def test_style_plan_does_not_contain_reaction_mode(self):
        """StylePlan은 문체(HOW)만 담당하고 reaction_mode(WHAT)를 포함하지 않음을 검증"""
        plan = StylePlanService.select_style_plan(post_key="test_post:1", preset="community", seed=10)
        self.assertNotIn("reaction_mode", plan.to_dict())
        self.assertNotIn("공감", plan.reaction_type)

    # -------------------------------------------------------------
    # P1. 도메인 분류 점수제 검증 (여행 vs 개인 키워드 혼재)
    # -------------------------------------------------------------

    def test_weekend_jeju_travel_is_place_not_personal(self):
        """'주말' 약한 키워드가 있어도 '제주 여행' 강한 키워드로 PLACE 도메인 선택"""
        title = "주말 제주 여행 코스 정리"
        excerpt = "이번 주말에 친구들과 함께 다녀온 제주도 바다 명소와 산책로 코스입니다."
        plan = ReactionContextPlanner.plan(title=title, excerpt=excerpt)
        self.assertEqual(plan.domain, "PLACE")
        self.assertEqual(plan.reaction_mode, "place_observation")

    def test_child_jeju_travel_is_place_not_personal(self):
        """'아이' 약한 키워드가 있어도 '제주 여행' 강한 키워드로 PLACE 도메인 선택"""
        title = "아이와 다녀온 제주 여행 후기"
        excerpt = "아이와 함께 제주도 해변 산책로를 걸으며 예쁜 바다 풍경을 눈에 담고 왔습니다."
        plan = ReactionContextPlanner.plan(title=title, excerpt=excerpt)
        self.assertEqual(plan.domain, "PLACE")
        self.assertEqual(plan.reaction_mode, "place_observation")

    def test_hotel_trip_is_place_when_travel_evidence_is_strong(self):
        """호텔과 여행 키워드가 혼재할 때 SERVICE가 아닌 PLACE로 정확히 판별"""
        title = "강릉 호텔 1박 2일 여행 후기"
        excerpt = "바다가 한눈에 내려다보이는 강릉 호텔 숙소에서 1박 하며 힐링 관광하고 왔습니다."
        plan = ReactionContextPlanner.plan(title=title, excerpt=excerpt)
        self.assertEqual(plan.domain, "PLACE")
        self.assertEqual(plan.reaction_mode, "place_observation")

    # -------------------------------------------------------------
    # P1. FOOD reaction_mode 우선순위 검증
    # -------------------------------------------------------------

    def test_two_food_items_do_not_automatically_mean_combination(self):
        """음식 앵커 2개 등장만으로 combination_curiosity가 되지 않고 맛 설명에 따라 taste_reaction 선택"""
        title = "마포 삼겹살 맛집 탐방"
        excerpt = "삼겹살도 먹고 된장찌개도 먹었는데 삼겹살은 숯불향이 가득하고 육즙이 많아서 고소했습니다."
        plan = ReactionContextPlanner.plan(title=title, excerpt=excerpt)
        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "taste_reaction")
        self.assertIn("맛이나 식감", plan.reaction_instruction)

    def test_explicit_food_combination_selects_combination_curiosity(self):
        """블루치즈, 꿀조합 등 명시적 조합이나 특수 재료는 combination_curiosity 선택"""
        title = "연남동 피자 맛집 특이한 블루치즈"
        excerpt = "블루치즈 특유의 진한 향 때문에 호불호가 갈릴 수 있는 피자입니다."
        plan = ReactionContextPlanner.plan(title=title, excerpt=excerpt)
        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "combination_curiosity")
        self.assertIn("조합", plan.reaction_instruction)

    def test_strong_taste_evidence_selects_taste_reaction(self):
        """구체적인 식감/맛 묘사가 있는 일반 음식 글은 taste_reaction 선택"""
        title = "성수동 수제버거 맛집"
        excerpt = "소고기 패티 육즙이 아주 풍부했고 번이 부드럽고 촉촉해서 식감이 뛰어났습니다."
        plan = ReactionContextPlanner.plan(title=title, excerpt=excerpt)
        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "taste_reaction")

    # -------------------------------------------------------------
    # P1. 3단계 음식 연관성 검증 (check_food_relevance)
    # -------------------------------------------------------------

    def test_food_paraphrase_without_menu_name_is_relevant(self):
        """댓글에 메뉴명이 직접 없어도 본문과 감각적 단어가 일치하면 Tier B 감각 증거로 통과"""
        excerpt = "돈카츠 튀김옷이 아주 바삭하고 안심 부위는 촉촉하고 부드러웠다."
        draft = "튀김옷이 바삭해 보여서 식감이 정말 궁금하네요"
        is_rel, reason = check_food_relevance(
            draft=draft,
            excerpt=excerpt,
            verified_anchors=["돈카츠", "안심"],
            reaction_mode="taste_reaction",
        )
        self.assertTrue(is_rel)
        self.assertIn("tier_b_grounded_sensory", reason)

    def test_food_taste_signal_must_be_grounded_in_excerpt(self):
        """본문에 전혀 없는 맛/식감 어휘를 혼자 언급하고 매장 얘기만 하면 탈락"""
        excerpt = "이 식당은 매장이 넓고 테이블 간격이 쾌적하며 주차가 편리합니다."
        draft = "매장 인테리어가 깔끔하고 주차가 편해 보여서 좋네요"
        is_rel, reason = check_food_relevance(
            draft=draft,
            excerpt=excerpt,
            verified_anchors=["식당"],
            reaction_mode="detail_observation",
        )
        self.assertFalse(is_rel)
        self.assertEqual(reason, "exclusively_non_food")

    # -------------------------------------------------------------
    # P1. Context Selector 가중치 선별 검증
    # -------------------------------------------------------------

    def test_context_selector_preserves_food_anchor_and_taste_evidence(self):
        """글 끝부분에 중요한 음식/식감 문장이 있어도 preferred_anchors 가중치로 보존"""
        long_body = (
            "오늘은 날씨가 화창해서 기분이 참 좋았습니다.\n"
            "점심시간에 무엇을 먹을까 고민하다가 근처 골목길을 걸어보았습니다.\n"
            "골목 곳곳에 예쁜 꽃들이 피어있어서 사진도 몇 장 찍었습니다.\n"
            "한참을 걷다 보니 조용한 골목 모퉁이에 작은 가게가 보였습니다.\n"
            "가게 안에는 사람들이 몇 명 앉아 있었고 음악이 잔잔하게 흘러나왔습니다.\n"
            "자리에 앉아 메뉴판을 찬찬히 살펴보았습니다.\n"
            "특히 돈카츠 튀김옷이 아주 바삭했고 안심은 촉촉하면서 부드러웠어요.\n"
            "식사를 마치고 나오니 하루가 든든하게 채워진 느낌이었습니다."
        )
        # 예산이 300자로 빡빡한 상황
        res = select_comment_context(
            title="점심 맛집",
            body=long_body,
            max_chars=300,
            preferred_anchors=["돈카츠", "안심"],
            preferred_terms=["바삭", "촉촉"],
        )
        self.assertIn("돈카츠 튀김옷이 아주 바삭했고 안심은 촉촉", res.excerpt)
        self.assertFalse(res.needs_more_context)

    # -------------------------------------------------------------
    # P1. Context retry 시 StylePolicy 및 config 보존
    # -------------------------------------------------------------

    def test_style_policy_survives_context_retry(self):
        """update_excerpt를 통한 재시도 시에도 사용자의 style_config(웃음 허용 여부 등)가 보존"""
        ctx = GenerationContext(
            title="카페 후기",
            excerpt="소금빵이 맛있네요",
            preset="community",
            style="warm_short",
            content_focus="CAFE_DESSERT",
            verified_anchors=["소금빵"],
            secondary_anchors=[],
            style_config={"allow_soft_laughter": False, "allow_soft_emoji": False},
        )
        ctx.style_policy = CommentStylePolicy.from_context(
            preset=ctx.preset,
            config=ctx.style_config,
        )
        self.assertFalse(ctx.style_policy.allow_soft_laughter)

        # 본문 확장 업데이트 실행
        ctx.update_excerpt("소금빵 결이 쫄깃하고 버터 풍미가 진하네요.")
        # 업데이트 후에도 여전히 False로 보존되어야 함
        self.assertIsNotNone(ctx.style_policy)
        self.assertFalse(ctx.style_policy.allow_soft_laughter)

    # -------------------------------------------------------------
    # P2. 미래 의향 2연속 초과 방지 검증
    # -------------------------------------------------------------

    def test_future_interest_anti_repetition(self):
        """future_interest가 2회 연속 사용된 경우 다음 메뉴 글에서는 future_interest를 배제하고 observation으로 전환"""
        title = "판교 라멘 맛집"
        excerpt = "새로 오픈한 라멘집에 다녀왔습니다. 돈코츠라멘 메뉴를 판매하고 있습니다."

        # 최근 등록 2개가 future_interest인 경우
        recent_modes = ["future_interest", "future_interest"]
        plan = ReactionContextPlanner.plan(
            title=title,
            excerpt=excerpt,
            recent_reaction_modes=recent_modes,
        )
        self.assertNotEqual(plan.reaction_mode, "future_interest")
        self.assertEqual(plan.reaction_mode, "detail_observation")


if __name__ == "__main__":
    unittest.main()

