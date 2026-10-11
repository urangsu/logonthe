import unittest
from services.reaction_planner import ReactionContextPlanner, ReactionPlan
from services.style_service import StylePlanService
from services.ai_prompt import AIPromptBuilder, PROMPT_VERSION_V3_5
from services.comments.community_rhythm import FinalQualityGate, CommunityRhythmPreset


class TestV35ReactionPipelineAndQuality(unittest.TestCase):
    def test_toothpaste_is_product_even_when_sweet_flavor_is_described(self):
        title = "달콤한 어린이 치약 사용기"
        excerpt = "딸기맛이라 달콤하고 부드러워 아이가 거부감 없이 양치해요"
        plan = ReactionContextPlanner.plan(title, excerpt)
        self.assertEqual(plan.domain, "PRODUCT")
        self.assertEqual(plan.reaction_mode, "feature_reaction")
        domain, anchors, _ = ReactionContextPlanner.discover_domain_and_terms(title, excerpt)
        self.assertEqual(domain, "PRODUCT")
        self.assertIn("치약", anchors)

    def test_food_review_with_incidental_toothpaste_reference_remains_food(self):
        plan = ReactionContextPlanner.plan("민트초코 아이스크림 맛집", "달콤하고 부드러운 아이스크림이에요 치약 같다는 사람도 있지만 디저트로 먹었어요")
        self.assertEqual(plan.domain, "FOOD")

    def test_01_style_service_completely_free_of_empathy_word(self):
        """1. StylePlanService 및 reaction_candidates에 '공감' 단어가 전혀 포함되지 않음"""
        for reaction in StylePlanService.REACTION_TYPES_COMMUNITY:
            self.assertNotIn("공감", reaction)
        for reaction in StylePlanService.REACTION_TYPES_THOUGHTFUL:
            self.assertNotIn("공감", reaction)
        for reaction in StylePlanService.REACTION_TYPES_ENTHUSIASTIC:
            self.assertNotIn("공감", reaction)

        plan = StylePlanService.select_style_plan(post_key="test_blog:1", preset="community", seed=42)
        self.assertNotIn("공감", plan.reaction_type)
        self.assertNotIn("reaction_mode", plan.to_dict())

    def test_02_implied_shared_experience_gate_blocks_ai_empathy(self):
        """2. implied_shared_experience 품질게이트가 AI 초안의 경험 암시 및 공감 표현을 차단"""
        blocked_samples = [
            "블루치즈 호불호 갈리는 거 너무 공감돼요",
            "글 보니까 완전 공감되네요",
            "완전 공감합니다 진짜 그래요",
            "저도 그래요 항상 그렇더라고요",
            "저도 같은 생각이에요",
            "그 느낌 알 것 같아요",
            "그 맛 알죠 진짜 대박인데",
            "저도 이런 거 좋아해요 취향저격이네요",
            "저도 이런 스타일 좋아해요",
            "호불호 갈리는 부분 공감 가네요",
        ]
        for text in blocked_samples:
            res = FinalQualityGate.validate_final_text(text, preset="community", source="gemini")
            self.assertFalse(res.valid, f"'{text}'는 차단되어야 합니다.")
            self.assertEqual(res.code, "implied_shared_experience")

    def test_03_observations_and_future_intent_are_permitted(self):
        """3. 관찰자 시선의 감상, 궁금증 및 미래 의향 표현은 안전하게 통과"""
        allowed_samples = [
            "블루치즈 들어간 조합이라 맛이 꽤 궁금하네요",
            "향이 진한 편이라 취향은 확실히 나뉘겠네요",
            "재료 조합이 꽤 독특해 보여요",
            "다음에 근처 가면 한번 먹어보고 싶네요",
            "사진 보니까 한번 맛보고 싶어지네요",
            "이런 메뉴는 나중에 꼭 직접 맛보고 싶네요~",
            "바삭바삭한 식감이 사진으로도 잘 느껴지네요",
        ]
        for text in allowed_samples:
            res = FinalQualityGate.validate_final_text(text, preset="community", source="gemini")
            self.assertTrue(res.valid, f"'{text}'는 통과되어야 합니다 (code={res.code}, reason={res.reason})")

    def test_04_user_edits_permit_empathy_word(self):
        """4. 사람이 직접 수정한 댓글(user_edit)에는 '공감' 단어 허용"""
        user_text = "작성자님 말씀에 정말 공감돼서 댓글 남겨요"
        res = FinalQualityGate.validate_final_text(user_text, preset="community", source="user_edit")
        self.assertTrue(res.valid, f"사용자 수정본은 공감 표현이 허용되어야 함: {res.code}")

    def test_05_reaction_planner_food_combination_curiosity(self):
        """5. 블루치즈 등 독특한 조합/재료 글은 combination_curiosity 모드로 계획 수립"""
        title = "연남동 피자 맛집 특이한 블루치즈 고르곤졸라"
        excerpt = "블루치즈 특유의 진한 향 때문에 호불호가 갈릴 수 있지만 꿀에 찍어먹으니 조합이 좋네요."

        plan = ReactionContextPlanner.plan(title, excerpt)
        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "combination_curiosity")
        self.assertIn("블루치즈", plan.primary_anchor)
        self.assertIn("짧고 솔직하게 반응해", plan.reaction_instruction)
        self.assertNotIn("관찰자 입장", plan.reaction_instruction)

    def test_06_reaction_planner_domains(self):
        """6. PRODUCT, SERVICE, PLACE, PERSONAL, GENERAL 도메인별 계획 수립 및 맞춤 지침 확인"""
        # PRODUCT
        plan_prod = ReactionContextPlanner.plan(
            title="가성비 좋은 흡입력 무선청소기 2주 사용기",
            excerpt="디자인이 깔끔하고 틈새 먼지 청소할 때 흡입력이 만족스럽네요."
        )
        self.assertEqual(plan_prod.domain, "PRODUCT")
        self.assertEqual(plan_prod.reaction_mode, "feature_reaction")
        self.assertIn("기능이나 디자인", plan_prod.reaction_instruction)

        # SERVICE
        plan_svc = ReactionContextPlanner.plan(
            title="강남역 깔끔한 체형교정 에스테틱 관리",
            excerpt="프라이빗 룸에서 전문 테라피스트 분이 체형 측정을 꼼꼼하게 진행해주셨어요."
        )
        self.assertEqual(plan_svc.domain, "SERVICE")
        self.assertEqual(plan_svc.reaction_mode, "service_reaction")

        # PLACE
        plan_place = ReactionContextPlanner.plan(
            title="제주도 애월 해변 일몰 산책로 코스 여행",
            excerpt="탁 트인 바다 전망대와 해안 도로 드라이브 코스가 정말 시원했습니다."
        )
        self.assertEqual(plan_place.domain, "PLACE")
        self.assertEqual(plan_place.reaction_mode, "place_observation")
        self.assertIn("풍경이나 코스", plan_place.reaction_instruction)

        # PERSONAL
        plan_personal = ReactionContextPlanner.plan(
            title="오늘 하루 일기 퇴근길 댕댕이와 산책",
            excerpt="퇴근하고 집에 오니 반갑게 맞이해주는 댕댕이 덕분에 피로가 싹 풀리네요."
        )
        self.assertEqual(plan_personal.domain, "PERSONAL")
        self.assertEqual(plan_personal.reaction_mode, "writer_feeling")

        # GENERAL
        plan_gen = ReactionContextPlanner.plan(
            title="새로운 프로그래밍 언어 문법 정리 노트",
            excerpt="타입 시스템과 컴파일러 옵션 설정에 대한 세부 사항을 정리했습니다."
        )
        self.assertEqual(plan_gen.domain, "GENERAL")
        self.assertEqual(plan_gen.reaction_mode, "detail_observation")

    def test_07_prompt_v3_5_is_short_and_isolated(self):
        """7. v3.5 프롬프트는 4단 구조로 짧고, 다른 카테고리 규칙이 주입되지 않음"""
        title = "연남동 피자 맛집 특이한 블루치즈"
        excerpt = "블루치즈 특유의 진한 향 때문에 호불호가 갈릴 수 있는 피자입니다."

        prompt = AIPromptBuilder.build(
            title=title,
            excerpt=excerpt,
            version=PROMPT_VERSION_V3_5,
        )

        self.assertIn("네이버 블로그 글에 남길 자연스러운 짧은 댓글 하나를 작성해", prompt)
        self.assertIn("이번 글의 반응 방향:", prompt)
        self.assertIn("근거:", prompt)
        self.assertIn("블루치즈", prompt)
        # 타 카테고리 규칙 불포함
        self.assertNotIn("제품이면", prompt)
        self.assertNotIn("서비스면", prompt)
        self.assertNotIn("여행이면", prompt)
        self.assertNotIn("[음식 글 우선 규칙]", prompt)
        # 전체 길이가 매우 간결함 (500자 이하)
        self.assertLess(len(prompt), 500)

    def test_v35_style_policy_and_one_reviewed_example_reach_active_prompt(self):
        from services.comments.policy import CommentStylePolicy
        policy = CommentStylePolicy.from_context(config={"allow_soft_emoji": True})
        prompt = AIPromptBuilder.build(
            title="만두 후기", excerpt="만두피가 얇고 속이 꽉 찼어요",
            corpus_examples=["만두피 얇은 거 좋네요 ㅎㅎ", "두 번째 예시는 주입하지 않음"],
            style_policy=policy, version=PROMPT_VERSION_V3_5,
        )
        self.assertIn(policy.target_length_desc, prompt)
        self.assertIn("웃음·이모지는 어울리면 합계 1개까지", prompt)
        self.assertIn("만두피 얇은 거 좋네요 ㅎㅎ", prompt)
        self.assertNotIn("두 번째 예시는 주입하지 않음", prompt)
        self.assertIn("소재나 문장을 복사하지 마", prompt)
        self.assertIn("말이 끝나면 끝내", prompt)
        self.assertNotIn("이번 글의 반응 방향", prompt)
        self.assertEqual(AIPromptBuilder.select_v3_5_style_examples(["x" * 101, "짧은 예시"]), ["짧은 예시"])

    def test_cooking_cadence_is_food_only_and_keeps_factual_boundary(self):
        food = AIPromptBuilder.build_v3_5(
            title="김밥 후기", selected_context="김밥에 달걀이 많이 들어 있어요",
            reaction_instruction="달걀 구성에 반응해", content_focus="FOOD_RESTAURANT",
        )
        ordinary = AIPromptBuilder.build_v3_5(
            title="비 오는 추석", selected_context="귀경길 비 예보가 있어요",
            reaction_instruction="비 소식에 아쉬움을 표현해", content_focus="GENERAL",
        )
        self.assertIn("달걀 구성에 반응해", food)
        self.assertIn("읽고 든 느낌 한 가지만", food)
        self.assertNotIn("음식 글도", ordinary)
        self.assertIn("없는 사실·사진 묘사·방문·시식 경험은 만들지 마", food)
        self.assertNotIn("더쿠", food)
        self.assertNotIn("관찰자 입장", food)

    def test_repeated_laughter_changes_guidance_not_registered_text(self):
        from services.comments.policy import CommentStylePolicy
        policy = CommentStylePolicy.from_context(config={"allow_soft_emoji": True})
        prompt = AIPromptBuilder.build_v3_5(
            title="만두 후기", selected_context="만두피가 얇고 속이 꽉 찼어요",
            style_policy=policy, recent_comments=["만두 좋네요 ㅎㅎ", "속이 꽉 찼네요 ㅎㅎ"],
            corpus_examples=["맛 궁금하네요 ㅎㅎ", "이 조합 좋네요 😊"],
        )
        self.assertIn("표지는 이번엔 쉬어가자", prompt)
        self.assertNotIn("맛 궁금하네요 ㅎㅎ", prompt)
        self.assertNotIn("이 조합 좋네요 😊", prompt)
        self.assertIn("없어도 좋아", prompt)
        self.assertLess(len(prompt), 900)
        restrained = AIPromptBuilder.build_v3_5(
            title="반려견이 무지개다리를 건넜어요", selected_context="오래 함께한 강아지를 떠나보냈어요",
            style_policy=policy, recent_comments=["ㅎㅎ", "ㅎㅎ"],
        )
        self.assertNotIn("이 조합 좋네요", restrained)
        self.assertNotIn("이번엔 쉬어가자", restrained)

    def test_08_food_focus_miss_code_category_and_feedback(self):
        """8. food_focus_miss 코드가 fact_violation으로 분류되고 전용 재작성 피드백이 생성되는지 확인"""
        from services.comments.community_rhythm import _CODE_CATEGORIES
        from app.processor import build_quality_rewrite_feedback
        from services.comments.community_rhythm import FinalQualityResult

        self.assertEqual(_CODE_CATEGORIES["food_focus_miss"], "fact_violation")
        self.assertEqual(_CODE_CATEGORIES["implied_shared_experience"], "fact_violation")

        dummy_food_miss = FinalQualityResult(
            valid=False, code="food_focus_miss", reason="miss", text="", normalized_text="",
            preset="community", source="gemini", length=0, quality_band="acceptable", length_score=1.0
        )
        feedback_food = build_quality_rewrite_feedback(dummy_food_miss)
        self.assertIn("음식/메뉴/맛에 대한 구체적인 디테일", feedback_food)
        self.assertIn("매장/인테리어/일반 분위기 반응 지양", feedback_food)

        dummy_implied = FinalQualityResult(
            valid=False, code="implied_shared_experience", reason="implied", text="", normalized_text="",
            preset="community", source="gemini", length=0, quality_band="acceptable", length_score=1.0
        )
        feedback_implied = build_quality_rewrite_feedback(dummy_implied)
        self.assertIn("공감돼요", feedback_implied)
        self.assertIn("관찰자의 시선", feedback_implied)

    def test_09_generation_context_and_context_selector_integration(self):
        """9. GenerationContext 및 update_excerpt가 comment_context_selector와 ReactionPlan에 정식 연결됨을 검증"""
        from app.processor import GenerationContext
        from app.models import FeedPost, FeedSourceType
        from app.processor import PostProcessor

        gen_ctx = GenerationContext(
            title="성수동 소금빵 베이커리 카페",
            excerpt="안녕하세요 오늘도 좋은 하루입니다.\n소금빵 결이 쫄깃하고 버터 향이 가득하네요.\n공감 댓글 부탁드립니다.",
            preset="community",
            style="warm_short",
            content_focus="CAFE_DESSERT",
            verified_anchors=["소금빵"],
            secondary_anchors=[],
        )
        # 초기화 및 프롬프트 생성 검증
        prompt = gen_ctx.build_prompt()
        self.assertIn("소금빵", prompt)

        # 본문 업데이트 시 context_selector를 통해 노이즈가 제거되고 ReactionPlan이 자동 갱신됨
        raw_long_body = (
            "안녕하세요 반갑습니다! 서이추 환영해요.\n"
            "이번에 연남동에서 먹은 고르곤졸라 피자 블루치즈 향이 독특해서 호불호가 갈리겠어요.\n"
            "꿀에 찍어먹으니 단짠 조합이 환상적이었습니다.\n"
            "블로그 이웃 신청 공감 부탁드립니다."
        )
        gen_ctx.update_excerpt(raw_long_body)
        self.assertNotIn("서이추 환영", gen_ctx.selected_context)
        self.assertIn("블루치즈", gen_ctx.selected_context)
        self.assertIsNotNone(gen_ctx.reaction_plan)
        self.assertEqual(gen_ctx.reaction_plan.domain, "FOOD")
        self.assertEqual(gen_ctx.reaction_plan.primary_anchor, "블루치즈")
        self.assertEqual(gen_ctx.reaction_plan.reaction_mode, "combination_curiosity")


if __name__ == "__main__":
    unittest.main()
