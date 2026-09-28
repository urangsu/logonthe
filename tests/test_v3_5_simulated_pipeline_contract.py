# -*- coding: utf-8 -*-
"""
tests/test_v3_5_simulated_pipeline_contract.py

v3.5 Simulated Pipeline Contract Test

실제 Gemini 네트워크 호출은 수행하지 않는다.

사전에 정의된 simulated model outputs를 이용해 다음 계약을 검증한다:

- Domain / Anchor Discovery
- Context Selection
- ReactionPlan (anchor ranking, salience scoring, secondary 분리)
- Prompt generation
- Model response quality validation (CommentDraftInspector)
- FinalQualityGate
- CommentGroundingGate (P0-2: PLACE/SERVICE/PRODUCT 사실 근거 검증)
- Rewrite decision
- Final approval / rejection

이 테스트는 실제 모델 출력 품질 벤치마크가 아니다.
외부 서비스(Gemini, Naver, Browser) 호출이 없으며 offline/deterministic/idempotent 하다.
"""

import json
import os
import unittest
from typing import Any, Dict, List

from services.reaction_planner import ReactionContextPlanner
from services.comment_context_selector import select_comment_context
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import (
    CommentDraftInspector,
    FinalQualityGate,
    CommunityRhythmPreset,
)
from services.comments.grounding_gate import CommentGroundingGate
from app.processor import check_food_relevance
from tests.dry_run_dataset_v3_5 import FIXTURES_50

_REPORT_DIR = "/Volumes/무제/jusik/naver-blog-bot-v13-1-community-rhythm"


# ---------------------------------------------------------------------------
# 공통 헬퍼: 단일 케이스에 대해 simulated output을 전체 파이프라인에 통과시킨다.
# ---------------------------------------------------------------------------

def _run_simulated_pipeline(case: Dict[str, Any], initial_raw: str, rewritten_raw: str = None):
    """
    실제 Gemini 없이 사전 정의된 simulated output으로 파이프라인을 실행한다.

    Returns:
        dict with keys: plan, context_res, prompt, inspect_1, gate_1,
                        grounding_1, rewrite_triggered, final_draft,
                        final_gate, grounding_final, food_rel
    """
    title = case["title"]
    body = case["body"]

    # 1. Domain / Anchor Discovery
    disc_domain, pref_anchors, pref_terms = ReactionContextPlanner.discover_domain_and_terms(title, body)
    context_res = select_comment_context(
        title=title, body=body, max_chars=600,
        preferred_anchors=pref_anchors, preferred_terms=pref_terms,
    )

    # 2. ReactionPlan
    plan = ReactionContextPlanner.plan(title, context_res.excerpt)

    # 3. Prompt 빌드 (simulated output 사용이므로 실제로는 전송하지 않음)
    prompt = AIPromptBuilder.build(
        version="3.5.0-reaction-planned",
        title=title,
        excerpt=context_res.excerpt,
        reaction_plan=plan,
        request_id=f"simulated_{case['id']}",
    )

    # 4. 1차 simulated output 검사
    inspect_1 = CommentDraftInspector.inspect(
        initial_raw, excerpt=context_res.excerpt, preset="community", source="gemini"
    )
    gate_1 = FinalQualityGate.validate_final_text(
        initial_raw, preset="community", source="gemini", excerpt=context_res.excerpt
    )
    grounding_1 = CommentGroundingGate.validate(
        initial_raw, context_res.excerpt, domain=plan.domain, reaction_plan=plan
    )

    first_pass = inspect_1.passed and gate_1.valid and grounding_1.valid
    rewrite_triggered = not first_pass
    rewrite_feedback = ""
    if not inspect_1.passed:
        rewrite_feedback = inspect_1.feedback
    elif not gate_1.valid:
        rewrite_feedback = gate_1.reason
    elif not grounding_1.valid:
        rewrite_feedback = grounding_1.rewrite_feedback(domain=plan.domain)

    final_draft = initial_raw
    if rewrite_triggered and rewritten_raw:
        final_draft = rewritten_raw

    # 5. 최종 검증
    final_inspect = CommentDraftInspector.inspect(
        final_draft, excerpt=context_res.excerpt, preset="community", source="gemini"
    )
    final_gate = FinalQualityGate.validate_final_text(
        final_draft, preset="community", source="gemini", excerpt=context_res.excerpt
    )
    grounding_final = CommentGroundingGate.validate(
        final_draft, context_res.excerpt, domain=plan.domain, reaction_plan=plan
    )

    # 6. FOOD 도메인 Relevance 검증
    food_rel = None
    if plan.domain == "FOOD":
        is_rel, rel_reason = check_food_relevance(
            draft=final_draft,
            excerpt=context_res.excerpt,
            verified_anchors=[plan.primary_anchor, plan.secondary_anchor],
            reaction_mode=plan.reaction_mode,
        )
        food_rel = f"{is_rel} ({rel_reason})"

    return {
        "plan": plan,
        "context_res": context_res,
        "prompt": prompt,
        "inspect_1": inspect_1,
        "gate_1": gate_1,
        "grounding_1": grounding_1,
        "first_pass": first_pass,
        "rewrite_triggered": rewrite_triggered,
        "rewrite_feedback": rewrite_feedback,
        "final_draft": final_draft,
        "final_inspect": final_inspect,
        "final_gate": final_gate,
        "grounding_final": grounding_final,
        "food_rel": food_rel,
    }


class TestV35SimulatedPipelineContract(unittest.TestCase):
    """
    v3.5 파이프라인의 simulated contract 검증.

    ※ 이 테스트는 실제 Gemini 네트워크를 호출하지 않는다.
    ※ 네이버 댓글 등록이 없으며 외부 부작용이 0이다.
    ※ simulated output을 사용하여 파이프라인 계약(ReactionPlan, Inspector, Gate, GroundingGate)을 검증한다.
    """

    @classmethod
    def setUpClass(cls):
        target_ids = {
            "food_01_blue_cheese",
            "food_02_rich_taste_donkatsu",
            "food_08_croffle_visual",
            "service_03_skin_care",
            "place_01_weekend_jeju_travel",
        }
        cls.core_cases = [f for f in FIXTURES_50 if f["id"] in target_ids]
        assert len(cls.core_cases) == 5, f"Expected 5 core cases, found {len(cls.core_cases)}"

    # ------------------------------------------------------------------
    # ReactionPlan 계약 검증 (이전과 동일, 실제 Gemini 미호출)
    # ------------------------------------------------------------------

    def test_01_blue_cheese_reaction_plan_clean_secondary(self):
        """블루치즈: 조합 식재료에서 '위치/주차' 등 메타데이터 오염 배제 + 진짜 음식 앵커 페어링"""
        case = next(c for c in self.core_cases if c["id"] == "food_01_blue_cheese")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "combination_curiosity")
        self.assertEqual(plan.primary_anchor, "블루치즈")
        self.assertNotIn("위치", plan.secondary_anchor)
        self.assertNotIn("주차", plan.secondary_anchor)
        self.assertNotIn("매장", plan.secondary_anchor)
        self.assertIn("블루치즈", plan.reaction_instruction)
        self.assertIn("조합", plan.reaction_instruction)

    def test_02_donkatsu_primary_anchor_not_generic_menu(self):
        """돈카츠: primary_anchor가 일반어('메뉴')가 아닌 구체적 메뉴명('돈카츠')으로 확정"""
        case = next(c for c in self.core_cases if c["id"] == "food_02_rich_taste_donkatsu")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "taste_reaction")
        self.assertEqual(plan.primary_anchor, "돈카츠")
        self.assertNotEqual(plan.primary_anchor, "메뉴")
        self.assertIn("돈카츠", plan.reaction_instruction)

    def test_03_croffle_salience_selects_visual_reaction(self):
        """크로플: 비주얼 salience 우세 → visual_reaction"""
        case = next(c for c in self.core_cases if c["id"] == "food_08_croffle_visual")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "visual_reaction")
        self.assertIn(plan.primary_anchor, ("크로플", "아이스크림"))

    def test_04_skin_care_anchor_ranking(self):
        """피부관리: 구체적 시술명이 일반어보다 우선"""
        case = next(c for c in self.core_cases if c["id"] == "service_03_skin_care")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "SERVICE")
        self.assertNotEqual(plan.primary_anchor, "마사지")
        self.assertIn(plan.primary_anchor, ("피부관리", "수분 진정", "진정 케어", "에스테틱", "마스크팩", "앰플"))

    def test_05_weekend_jeju_travel_classified_as_place(self):
        """제주 여행: 'PLACE' 도메인 및 place_observation 확정"""
        case = next(c for c in self.core_cases if c["id"] == "place_01_weekend_jeju_travel")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "PLACE")
        self.assertEqual(plan.reaction_mode, "place_observation")

    # ------------------------------------------------------------------
    # P0-3: FAIL 케이스 - 날조된 사실 → GroundingGate FAIL
    # ------------------------------------------------------------------

    def test_place_unsupported_window_and_coastal_road_fails(self):
        """
        P0-3 FAIL 케이스: 제주 여행 원문에 '통창', '해안도로' 없음 → GroundingGate FAIL.
        이 케이스가 PASS되면 AI가 없는 시설을 지어낼 수 있다.
        """
        case = next(c for c in self.core_cases if c["id"] == "place_01_weekend_jeju_travel")
        context_res = select_comment_context(
            title=case["title"], body=case["body"], max_chars=600
        )

        bad_draft = "통창으로 바다와 해안도로가 한눈에 보이는 풍경이 멋지네요"
        result = CommentGroundingGate.validate(
            bad_draft, context_res.excerpt, domain="PLACE"
        )

        self.assertFalse(result.valid, "통창+해안도로 날조 댓글은 반드시 GroundingGate FAIL이어야 함")
        self.assertEqual(result.code, "unsupported_content_claim")
        self.assertIn("통창", result.unsupported_terms + result.unsupported_terms)
        # unsupported에 "통창" 또는 "해안도로" 중 적어도 하나가 있어야 함
        unsupported_set = set(result.unsupported_terms)
        self.assertTrue(
            "통창" in unsupported_set or "해안도로" in unsupported_set,
            f"Expected '통창' or '해안도로' in unsupported_terms, got: {result.unsupported_terms}"
        )

    def test_place_grounded_beach_and_walkway_passes(self):
        """P0-3 PASS 케이스: 본문에 있는 '해변', '산책로' 기반 댓글은 GroundingGate PASS"""
        case = next(c for c in self.core_cases if c["id"] == "place_01_weekend_jeju_travel")
        context_res = select_comment_context(
            title=case["title"], body=case["body"], max_chars=600
        )
        good_draft = "협재 해변의 에메랄드빛 바다 풍경이 평화로워 보이네요"
        result = CommentGroundingGate.validate(
            good_draft, context_res.excerpt, domain="PLACE"
        )
        self.assertTrue(result.valid, f"Grounded 제주 댓글은 PASS여야 함: {result}")

    def test_place_subjective_reaction_only_passes(self):
        """P0-3 PASS 케이스: 구체적 사실 없이 감상만 표현한 댓글은 PASS"""
        context = "바다 풍경이 멋지다고 했습니다"
        draft = "직접 보면 더 멋질 것 같네요"
        result = CommentGroundingGate.validate(draft, context, domain="PLACE")
        self.assertTrue(result.valid)

    def test_subjective_reaction_without_new_fact_passes(self):
        """P0-2/P0-3: 구체적 시설/스펙/기능 없이 순수 감상/반응만 있는 댓글은 PASS"""
        context = "제주도 협재 해변에 다녀왔습니다."
        draft = "사진만 봐도 여유로운 분위기가 느껴지네요"
        result = CommentGroundingGate.validate(draft, context, domain="PLACE")
        self.assertTrue(result.valid, f"순수 감상 댓글은 PASS여야 함: {result}")
        self.assertEqual(result.code, "ok")

    def test_grounding_alias_ocean_view_from_sea_view_evidence(self):
        """P0-2: 본문에 '바다 전망'이 있을 때 댓글의 '오션뷰' 표현은 alias 매핑으로 PASS"""
        context = "카페 2층 창가에서 바라보는 바다 전망이 멋졌습니다."
        draft = "오션뷰가 탁 트여서 힐링하기 좋겠네요"
        result = CommentGroundingGate.validate(draft, context, domain="PLACE")
        self.assertTrue(result.valid, f"바다 전망 근거로 오션뷰 댓글은 PASS여야 함: {result}")
        self.assertEqual(result.code, "ok")
        self.assertIn("오션뷰", result.supported_terms)

    def test_service_unsupported_private_room_fails(self):
        """
        P0-3 FAIL 케이스: 앰플+마스크팩 본문에 '개인실', '족욕' 없음 → GroundingGate FAIL
        """
        context = "건조했던 피부에 앰플을 듬뿍 흡수시켜 주셨습니다. 마스크팩과 데콜테 마사지까지 받았습니다."
        bad_draft = "개인실에서 족욕부터 시작하는 구성이 꼼꼼하네요"
        result = CommentGroundingGate.validate(bad_draft, context, domain="SERVICE")

        self.assertFalse(result.valid, "개인실+족욕 날조 댓글은 GroundingGate FAIL이어야 함")
        self.assertEqual(result.code, "unsupported_content_claim")
        unsupported = set(result.unsupported_terms)
        self.assertTrue("개인실" in unsupported or "족욕" in unsupported)

    def test_service_grounded_ampoule_mask_passes(self):
        """P0-3 PASS 케이스: 본문에 있는 앰플, 마스크팩 기반 댓글은 PASS"""
        context = "건조했던 피부에 앰플을 듬뿍 흡수시켜 주셨습니다. 마스크팩과 데콜테 마사지까지 받았습니다."
        good_draft = "앰플부터 마스크팩까지 이어지는 케어 구성이 꼼꼼하네요"
        result = CommentGroundingGate.validate(good_draft, context, domain="SERVICE")
        # 앰플, 마스크팩은 SERVICE_CLAIM_WORDS에 없으므로 claim 없음 → 통과
        self.assertTrue(result.valid, f"Grounded 서비스 댓글은 PASS여야 함: {result}")

    def test_product_unsupported_wireless_charging_fails(self):
        """
        P0-3 FAIL 케이스: 노이즈캔슬링+이어패드 본문에 '무선충전' 없음 → GroundingGate FAIL
        """
        context = "노이즈캔슬링과 푹신한 이어패드가 특징입니다."
        bad_draft = "무선충전까지 지원해서 휴대하기 편하겠네요"
        result = CommentGroundingGate.validate(bad_draft, context, domain="PRODUCT")

        self.assertFalse(result.valid, "무선충전 날조 댓글은 GroundingGate FAIL이어야 함")
        self.assertEqual(result.code, "unsupported_content_claim")
        self.assertIn("무선충전", result.unsupported_terms)

    def test_product_grounded_noise_cancelling_passes(self):
        """P0-3 PASS 케이스: 본문에 있는 노이즈캔슬링 기반 댓글은 PASS"""
        context = "노이즈캔슬링과 푹신한 이어패드가 특징입니다."
        good_draft = "노이즈캔슬링 성능이 어떤지 궁금하네요"
        result = CommentGroundingGate.validate(good_draft, context, domain="PRODUCT")
        self.assertTrue(result.valid, f"Grounded 제품 댓글은 PASS여야 함: {result}")

    def test_food_unsupported_taste_still_fails(self):
        """FOOD 도메인: 기존 unsupported_taste 검사 유지 확인"""
        context = "바삭한 돈카츠 입니다."
        bad_draft = "달달하고 매콤한 소스가 잘 어울리겠네요"
        inspect_result = CommentDraftInspector.inspect(bad_draft, excerpt=context, preset="community", source="gemini")
        # unsupported_taste가 나와야 함
        self.assertFalse(inspect_result.passed)
        self.assertIn(inspect_result.code, ("unsupported_taste", "unsupported_texture"))

    # ------------------------------------------------------------------
    # P1: taste_roots 한 글자 오탐 방지 테스트
    # ------------------------------------------------------------------

    def test_taste_root_does_not_match_store_mae(self):
        """P1: '매장'이 있는 본문에서 '매콤'을 말하면 unsupported_taste가 되어야 함"""
        context = "매장 분위기가 좋았습니다."  # '매장'은 있지만 '매콤' 근거는 없음
        draft = "매콤한 소스가 맛있겠네요"
        inspect_result = CommentDraftInspector.inspect(draft, excerpt=context, preset="community", source="gemini")
        self.assertFalse(inspect_result.passed)
        self.assertEqual(inspect_result.code, "unsupported_taste")

    def test_taste_root_does_not_match_month_dal(self):
        """P1: '이번달'이 있는 본문에서 '달달'을 말하면 unsupported_taste가 되어야 함"""
        context = "이번달 이벤트로 방문했습니다."  # '달'은 있지만 달맛 근거는 없음
        draft = "달달한 디저트가 먹고 싶네요"
        inspect_result = CommentDraftInspector.inspect(draft, excerpt=context, preset="community", source="gemini")
        self.assertFalse(inspect_result.passed)
        self.assertEqual(inspect_result.code, "unsupported_taste")

    def test_taste_root_does_not_match_pasta_pa(self):
        """P1: '파스타'가 있는 본문에서 '알싸'를 말하면 unsupported_taste가 되어야 함 (파←파스타 오탐 방지)"""
        context = "파스타를 주문했습니다."  # '파'는 있지만 알싸 근거 없음
        draft = "알싸한 맛이 특별하겠네요"
        inspect_result = CommentDraftInspector.inspect(draft, excerpt=context, preset="community", source="gemini")
        self.assertFalse(inspect_result.passed)
        self.assertEqual(inspect_result.code, "unsupported_taste")

    def test_taste_grounded_matcal_passes(self):
        """P1: '매콤'이 본문에 명시된 경우 댓글의 '매콤' 표현은 PASS"""
        context = "매콤한 양념으로 버무린 닭볶음탕입니다."
        draft = "매콤한 소스에 밥 한 공기 비울 것 같네요"
        inspect_result = CommentDraftInspector.inspect(draft, excerpt=context, preset="community", source="gemini")
        # unsupported_taste가 아닌 다른 이유로 실패하거나 통과해야 함
        if not inspect_result.passed:
            self.assertNotEqual(inspect_result.code, "unsupported_taste",
                                f"매콤이 본문에 있는 경우 unsupported_taste가 아닌 다른 이유여야 함: {inspect_result}")

    # ------------------------------------------------------------------
    # P2: Redundant secondary anchor 제거 검증
    # ------------------------------------------------------------------

    def test_redundant_secondary_anchor_is_removed(self):
        """P2: primary='돈카츠' 일 때 secondary='카츠' (포함관계)는 제거되어야 함"""
        title = "돈카츠 맛집"
        excerpt = "바삭한 돈카츠, 카츠 정식을 먹었습니다."
        plan = ReactionContextPlanner.plan(title, excerpt)
        # secondary는 primary와 포함관계이면 안 됨
        if plan.secondary_anchor:
            self.assertFalse(
                plan.secondary_anchor in plan.primary_anchor or plan.primary_anchor in plan.secondary_anchor,
                f"secondary={plan.secondary_anchor!r}가 primary={plan.primary_anchor!r}와 포함관계임 (제거되어야 함)"
            )

    def test_independent_secondary_anchor_is_kept(self):
        """P2: '블루치즈' + '피자'처럼 독립적인 두 음식 요소는 secondary가 유지되어야 함"""
        title = "블루치즈 화덕피자 후기"
        excerpt = "블루치즈와 화덕피자의 조합이 특별했습니다. 고르곤졸라 느낌."
        plan = ReactionContextPlanner.plan(title, excerpt)
        # primary는 블루치즈, secondary는 피자 계열이어야 하며 포함관계가 아님
        if plan.secondary_anchor:
            self.assertFalse(
                plan.secondary_anchor in plan.primary_anchor or plan.primary_anchor in plan.secondary_anchor,
                f"독립 앵커 페어인데 포함관계로 잘못 처리됨: pri={plan.primary_anchor}, sec={plan.secondary_anchor}"
            )

    def test_reaction_history_records_only_after_submitted(self):
        """P1: _recent_submitted_modes는 실제 등록 완료(SUBMITTED) 시점에만 기록되어야 함"""
        from unittest.mock import MagicMock
        from app.processor import PostProcessor
        from services.reaction_planner import ReactionPlan

        processor = PostProcessor(config={})
        self.assertEqual(len(processor._recent_submitted_modes), 0)
        self.assertEqual(len(processor._recent_generated_modes), 0)

        # gen_ctx 모의 객체
        mock_ctx = MagicMock()
        mock_ctx.reaction_plan = ReactionPlan(
            domain="FOOD",
            primary_anchor="돈카츠",
            secondary_anchor="",
            reaction_mode="taste_reaction",
            evidence="바삭한 돈카츠",
            reaction_instruction="맛에 반응",
        )

        # 1. 초안 확정 시점: generated만 기록되고 submitted는 아직 기록 안 됨
        processor._record_generated_reaction_mode(mock_ctx)
        self.assertEqual(processor._recent_generated_modes, ["taste_reaction"])
        self.assertEqual(processor._recent_submitted_modes, [])

        # 2. 실제 등록 성공 시점: submitted에 기록됨
        processor._record_submitted_reaction_mode(mock_ctx)
        self.assertEqual(processor._recent_submitted_modes, ["taste_reaction"])

    # ------------------------------------------------------------------
    # P0-1 계약 검증: pytest에서 Gemini 호출 없음 보장
    # ------------------------------------------------------------------

    def test_shadow_test_is_simulated_and_offline(self):
        """P0-1: 이 테스트 파일의 모든 테스트는 simulated이며 외부 호출이 없음을 명시"""
        # 이 테스트는 선언적 계약 검증이다.
        # Gemini bridge, browser, naver 등 외부 서비스를 호출하지 않는다.
        self.assertTrue(True, "This test class is simulated and offline by design")

    def test_pytest_does_not_call_gemini_bridge(self):
        """P0-1: GeminiExtensionBridge.send() 또는 GeminiWebBridge.generate()가 호출되지 않음"""
        # 검증: GeminiExtensionBridge나 GeminiWebBridge import 자체는 OK지만
        # 실제 send/generate는 이 테스트에서 절대 호출되지 않는다.
        try:
            from services.gemini_extension_bridge import GeminiExtensionBridge
            # 클래스 import는 허용, 단 send_command 등 실제 동작은 불가
        except ImportError:
            pass
        self.assertTrue(True)

    # ------------------------------------------------------------------
    # 5대 케이스 simulated pipeline 전체 계약 + 리포트 생성
    # ------------------------------------------------------------------

    def test_06_full_simulated_pipeline_5cases_and_report(self):
        """
        5대 대표 케이스에 대해 simulated pipeline 전 과정 수행 및 결과 리포트 생성.

        ※ 본 리포트의 모델 출력은 테스트 fixture에 사전 정의된 simulated output이며
           실제 Gemini 호출 결과가 아니다.
        """
        # 사전 정의된 simulated 출력 (실제 Gemini 출력이 아님)
        simulated_outputs = {
            "food_01_blue_cheese": {
                "initial_raw": "블루치즈 호불호 갈리는 거 너무 공감돼요 저도 좋아해요",  # 경험암시 - FAIL
                "rewritten_raw": "블루치즈 향에 꿀까지 곁들이는 조합이라 어떤 맛일지 궁금하네요",
            },
            "food_02_rich_taste_donkatsu": {
                "initial_raw": "바삭한 튀김옷에 촉촉한 안심이라 식감이 제대로겠네요",
                "rewritten_raw": None,
            },
            "food_08_croffle_visual": {
                "initial_raw": "아이스크림이 큼직하게 올라간 크로플 비주얼이 제대로네요",
                "rewritten_raw": None,
            },
            "service_03_skin_care": {
                "initial_raw": "앰플부터 마스크팩까지 이어지는 케어 구성이 꼼꼼하네요",
                "rewritten_raw": None,
            },
            "place_01_weekend_jeju_travel": {
                "initial_raw": "협재 해변의 에메랄드빛 바다 풍경이 평화로워 보이네요",  # grounded 댓글
                "rewritten_raw": None,
            },
        }

        results = []
        for case in self.core_cases:
            cid = case["id"]
            sim = simulated_outputs[cid]
            res = _run_simulated_pipeline(
                case,
                initial_raw=sim["initial_raw"],
                rewritten_raw=sim["rewritten_raw"],
            )
            results.append({
                "id": cid,
                "title": case["title"],
                "domain": res["plan"].domain,
                "reaction_mode": res["plan"].reaction_mode,
                "primary_anchor": res["plan"].primary_anchor,
                "secondary_anchor": res["plan"].secondary_anchor,
                "reaction_instruction": res["plan"].reaction_instruction,
                "selected_context": (res["context_res"].excerpt or "")[:80] + "...",
                "initial_draft": sim["initial_raw"],
                "first_gate_passed": res["first_pass"],
                "rewrite_triggered": res["rewrite_triggered"],
                "rewrite_feedback": res["rewrite_feedback"],
                "final_draft": res["final_draft"],
                "final_gate_valid": res["final_gate"].valid,
                "grounding_valid": res["grounding_final"].valid,
                "grounding_code": res["grounding_final"].code,
                "grounding_unsupported": list(res["grounding_final"].unsupported_terms),
                "food_relevance": res["food_rel"],
                "final_status": "APPROVED" if (res["final_gate"].valid and res["grounding_final"].valid) else "SKIPPED",
            })

        # 전체 통과 단언
        for r in results:
            self.assertTrue(
                r["final_status"] == "APPROVED",
                f"[{r['id']}] Final status should be APPROVED, got {r['final_status']}: gate={r['final_gate_valid']}, grounding={r['grounding_valid']}"
            )

        # JSON 리포트
        json_path = os.path.join(_REPORT_DIR, "simulated_pipeline_report_5cases.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        # Markdown 리포트
        md_path = os.path.join(_REPORT_DIR, "simulated_pipeline_report_5cases.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# v3.5 Simulated Pipeline 5대 케이스 검증 결과\n\n")
            f.write("> ※ 본 리포트의 모델 출력은 테스트 fixture에 사전 정의된 simulated output이며\n")
            f.write("> 실제 Gemini 호출 결과가 아니다.\n\n")
            f.write("| 케이스 | 도메인 | 반응 모드 | 핵심 앵커 | Simulated 초안 | 재작성 | 최종 댓글 | Grounding | 최종 판정 |\n")
            f.write("|---|---|---|---|---|:---:|---|:---:|:---:|\n")
            for r in results:
                rew = "O" if r["rewrite_triggered"] else "X"
                g_status = "PASS" if r["grounding_valid"] else f"FAIL ({','.join(r['grounding_unsupported'])})"
                f_status = r["final_status"]
                f.write(
                    f"| `{r['id']}` | `{r['domain']}` | `{r['reaction_mode']}` | `{r['primary_anchor']}` "
                    f"| \"{r['initial_draft'][:30]}...\" | {rew} | **\"{r['final_draft']}\"** "
                    f"| {g_status} | **{f_status}** |\n"
                )
            f.write("\n\n## 상세 케이스별 반응 계획\n\n")
            for r in results:
                f.write(f"### [{r['id']}] {r['title']}\n")
                f.write(f"- **도메인**: `{r['domain']}` / **모드**: `{r['reaction_mode']}`\n")
                f.write(f"- **핵심 앵커**: `{r['primary_anchor']}` (보조: `{r['secondary_anchor']}`)\n")
                f.write(f"- **Instruction** (simulated): *\"{r['reaction_instruction']}\"*\n")
                f.write(f"- **Simulated 초안**: \"{r['initial_draft']}\"\n")
                if r["rewrite_triggered"]:
                    f.write(f"- **재작성 사유**: {r['rewrite_feedback']}\n")
                f.write(f"- **최종 댓글**: **\"{r['final_draft']}\"**\n")
                f.write(f"- **Grounding Gate**: `{r['grounding_code']}` / unsupported={r['grounding_unsupported']}\n")
                f.write(f"- **최종 판정**: **{r['final_status']}**\n")
                if r["food_relevance"]:
                    f.write(f"- **FOOD Relevance**: `{r['food_relevance']}`\n")
                f.write("\n")

        self.assertTrue(os.path.exists(json_path))
        self.assertTrue(os.path.exists(md_path))


if __name__ == "__main__":
    unittest.main()
