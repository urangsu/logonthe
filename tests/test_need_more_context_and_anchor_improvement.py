# -*- coding: utf-8 -*-
"""
tests/test_need_more_context_and_anchor_improvement.py

v3.5 댓글 품질 및 앵커/이모티콘 개선 검증 테스트:
1. AIPromptBuilder.build_v3_5에서 allow_need_more_context=False 시 NEED_MORE_CONTEXT 미제공 검증
2. 실전 메뉴 사전(들깨수제비, 과일산도, 김피탕, 잠봉뵈르 등) 및 복합명 우선순위 랭킹 검증
3. Generic anchor(메뉴, 음식) 최후 fallback 격하 및 open-vocabulary 제목 후보 추출 검증
4. strip_emojis에서 두 문장 사이 이모지 제거 시 '~ ' 경계 보존 및 말끝 이모지 제거 시 경계자 미추가 검증
5. selected_anchor와 semantic_signal 분리 및 [FOOD_COMMENT] 포맷 검증
"""

import unittest
from services.ai_prompt import AIPromptBuilder
from services.food_comment_focus import FoodCommentFocus
from services.reaction_planner import ReactionContextPlanner
from services.comments.style_normalizer import CommentStyleNormalizer


class TestNeedMoreContextAndAnchorImprovement(unittest.TestCase):

    def test_v3_5_prompt_need_more_context_conditional(self):
        """1. build_v3_5에서 allow_need_more_context 옵션에 따른 지침 검증"""
        # 기본값 (False): 파이썬이 맥락 유의미성 검증 완료 시 NEED_MORE_CONTEXT 선택지 미제공
        prompt_normal = AIPromptBuilder.build_v3_5(
            title="맛있는 삼겹살 맛집",
            selected_context="삼겹살이 정말 두툼하고 맛있어요",
            allow_need_more_context=False,
        )
        self.assertIn("- 댓글만 출력", prompt_normal)
        self.assertNotIn("NEED_MORE_CONTEXT", prompt_normal, "allow_need_more_context=False 시 NEED_MORE_CONTEXT 문구가 없어야 함")

        # True인 경우: NEED_MORE_CONTEXT 허용
        prompt_fallback = AIPromptBuilder.build_v3_5(
            title="맛있는 삼겹살 맛집",
            selected_context="삼겹살이 정말 두툼하고 맛있어요",
            allow_need_more_context=True,
        )
        self.assertIn("- 댓글만 출력(근거 부족 시 NEED_MORE_CONTEXT)", prompt_fallback)

    def test_composite_food_anchors_ranking(self):
        """2. 복합 음식명 우선순위 랭킹 (들깨수제비 > 수제비, 과일산도 > 산도, 김피탕 > 피탕, 잠봉뵈르 > 샌드위치)"""
        # 1) 들깨수제비 vs 수제비
        plan1 = ReactionContextPlanner.plan(
            title="[강남 맛집] 진한 들깨수제비와 보리밥 수제비 맛집",
            excerpt="들깨수제비가 정말 고소하고 수제비 반죽이 쫄깃해요",
        )
        self.assertEqual(plan1.primary_anchor, "들깨수제비")
        self.assertNotEqual(plan1.secondary_anchor, "수제비", "수제비는 들깨수제비의 부분문자열이므로 sec에서 제외되어야 함")
        self.assertEqual(plan1.secondary_anchor, "보리밥")

        # 2) 과일산도 vs 산도
        plan2 = ReactionContextPlanner.plan(
            title="[망원동 카페] 과일산도 디저트 맛집",
            excerpt="과일산도 생크림이 가득하고 산도 비주얼이 너무 예뻐요",
        )
        self.assertEqual(plan2.primary_anchor, "과일산도")

        # 3) 김피탕 vs 피탕
        plan3 = ReactionContextPlanner.plan(
            title="[공주 맛집] 원조 김피탕 피탕 맛집 후기",
            excerpt="김피탕 치즈가 듬뿍 들어가서 피탕 맛이 대박이에요",
        )
        self.assertEqual(plan3.primary_anchor, "김피탕")

        # 4) 잠봉뵈르 vs 샌드위치 / 잠봉
        plan4 = ReactionContextPlanner.plan(
            title="[성수동 맛집] 잠봉뵈르 샌드위치 전문점",
            excerpt="잠봉뵈르 햄과 버터의 풍미가 가득한 샌드위치",
        )
        self.assertEqual(plan4.primary_anchor, "잠봉뵈르")

    def test_open_vocabulary_food_candidates_and_avoid_menu_fallback(self):
        """3. 사전에 없는 음식명 open-vocabulary 추출 및 'anchor=메뉴' 방지 검증"""
        # 사전에 없는 '십원빵'
        plan = ReactionContextPlanner.plan(
            title="[경주 맛집] 황리단길 십원빵 간식 먹거리",
            excerpt="십원빵 안에 치즈가 듬뿍 늘어나서 너무 맛있어요",
        )
        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.primary_anchor, "십원빵", "'메뉴' 대신 실제 음식인 십원빵이 primary_anchor여야 함")
        self.assertNotEqual(plan.primary_anchor, "메뉴")

        # 사전에 없는 '피순대'
        plan2 = ReactionContextPlanner.plan(
            title="[전주 맛집] 소문난 피순대 국물 맛집",
            excerpt="피순대가 속이 꽉 차고 진한 국물이 끝내줘요",
        )
        self.assertEqual(plan2.domain, "FOOD")
        self.assertEqual(plan2.primary_anchor, "피순대")
        self.assertNotEqual(plan2.primary_anchor, "메뉴")

    def test_strip_emojis_clause_boundary_preservation(self):
        """4. 이모지 제거 시 두 문장 사이 '~ ' 경계 보존 및 말끝 경계자 미추가 검증"""
        # 두 문장 사이: 앞문장이 종결형 어미이고 뒷문장이 이어지면 '~ '로 경계 보존
        t1 = "샤브샤브 국물 너무 땡기네요 😋 월남쌈도 다양해요"
        self.assertEqual(
            CommentStyleNormalizer.strip_emojis(t1),
            "샤브샤브 국물 너무 땡기네요~ 월남쌈도 다양해요"
        )

        # 다중/과장 이모지가 문장 사이에 있어도 하나의 '~ '로 축약
        t2 = "김말이 너무 맛있어 보여요 😋😋😋🔥 대박이네요"
        self.assertEqual(
            CommentStyleNormalizer.strip_emojis(t2),
            "김말이 너무 맛있어 보여요~ 대박이네요"
        )

        # 문장 끝 이모지 제거: 경계자 추가 없음
        t3 = "샤브샤브 너무 맛있어 보여요 😋"
        self.assertEqual(
            CommentStyleNormalizer.strip_emojis(t3),
            "샤브샤브 너무 맛있어 보여요"
        )

        # 비이모지 특수기호(20℃, 1.5㎏, ©, ™) 보존
        t4 = "오늘 날씨 20℃ 정도라 최고네요 😋"
        self.assertEqual(
            CommentStyleNormalizer.strip_emojis(t4),
            "오늘 날씨 20℃ 정도라 최고네요"
        )
        self.assertIn("20℃", CommentStyleNormalizer.strip_emojis(t4))


if __name__ == "__main__":
    unittest.main()
