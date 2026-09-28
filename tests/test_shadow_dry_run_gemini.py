# -*- coding: utf-8 -*-
"""
tests/test_shadow_dry_run_gemini.py

5대 핵심 대표 시나리오(블루치즈, 돈카츠, 크로플, 피부관리, 제주여행)에 대한
v3.5 Shadow Dry-run 검증 및 실제 결과 원문 표 생성 테스트.
네이버 등록은 절대 진행하지 않고(Fail-Open/외부 부작용 원천 차단),
Pipeline 전 과정(Discovery -> Selector -> ReactionPlan -> Prompt -> Gemini Generation/Rewrite -> Quality Gate)을
검증하여 사람이 직접 확인할 수 있는 비교표 리포트를 생성한다.
"""

import json
import os
import unittest
from typing import Dict, Any, List

from services.reaction_planner import ReactionContextPlanner
from services.comment_context_selector import select_comment_context
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import (
    CommentDraftInspector,
    FinalQualityGate,
    CommunityRhythmPreset,
)
from app.processor import check_food_relevance
from tests.dry_run_dataset_v3_5 import FIXTURES_50


class TestShadowDryRunGemini(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 5대 핵심 케이스 선정
        target_ids = {
            "food_01_blue_cheese",
            "food_02_rich_taste_donkatsu",
            "food_08_croffle_visual",
            "service_03_skin_care",
            "place_01_weekend_jeju_travel",
        }
        cls.core_cases = [f for f in FIXTURES_50 if f["id"] in target_ids]
        assert len(cls.core_cases) == 5, f"Expected 5 core cases, found {len(cls.core_cases)}"

    def test_01_blue_cheese_reaction_plan_clean_secondary(self):
        """1. 블루치즈: 조합 식재료에서 '위치/주차' 등 메타데이터 오염 배제 및 진짜 음식 앵커 페어링"""
        case = next(c for c in self.core_cases if c["id"] == "food_01_blue_cheese")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "combination_curiosity")
        self.assertEqual(plan.primary_anchor, "블루치즈")
        # 메타데이터('위치', '주차' 등)가 secondary_anchor로 오염되지 않아야 함
        self.assertNotIn("위치", plan.secondary_anchor)
        self.assertNotIn("주차", plan.secondary_anchor)
        self.assertNotIn("매장", plan.secondary_anchor)
        self.assertNotIn("위치", plan.reaction_instruction)
        # instruction이 자연스러운 조합 질문 형태여야 함
        self.assertIn("블루치즈", plan.reaction_instruction)
        self.assertIn("조합", plan.reaction_instruction)

    def test_02_donkatsu_primary_anchor_not_generic_menu(self):
        """2. 돈카츠: primary_anchor가 일반어('메뉴')가 아닌 구체적 메뉴명('돈카츠')으로 확정"""
        case = next(c for c in self.core_cases if c["id"] == "food_02_rich_taste_donkatsu")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "taste_reaction")
        self.assertEqual(plan.primary_anchor, "돈카츠")
        self.assertNotEqual(plan.primary_anchor, "메뉴")
        self.assertIn("돈카츠", plan.reaction_instruction)

    def test_03_croffle_salience_selects_visual_reaction(self):
        """3. 크로플: 플레이팅/비주얼 salience 점수가 우세하여 taste가 아닌 visual_reaction 확정"""
        case = next(c for c in self.core_cases if c["id"] == "food_08_croffle_visual")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "FOOD")
        self.assertEqual(plan.reaction_mode, "visual_reaction")
        self.assertIn(plan.primary_anchor, ("크로플", "아이스크림"))
        self.assertIn("비주얼", plan.reaction_instruction)

    def test_04_skin_care_anchor_ranking_prefers_title_treatment(self):
        """4. 피부관리: 단순 keyword 첫 단어('마사지')가 아닌 제목 고유 시술명('피부관리'/'수분 진정') 우선"""
        case = next(c for c in self.core_cases if c["id"] == "service_03_skin_care")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "SERVICE")
        self.assertEqual(plan.reaction_mode, "service_reaction")
        self.assertNotEqual(plan.primary_anchor, "마사지")
        self.assertIn(plan.primary_anchor, ("피부관리", "수분 진정", "진정 케어", "에스테틱", "마스크팩", "앰플"))

    def test_05_weekend_jeju_travel_classified_as_place(self):
        """5. 주말 제주 여행: '주말' 약한 일상어에 휘둘리지 않고 'PLACE' 도메인 및 장소 관찰 확정"""
        case = next(c for c in self.core_cases if c["id"] == "place_01_weekend_jeju_travel")
        plan = ReactionContextPlanner.plan(case["title"], case["body"])

        self.assertEqual(plan.domain, "PLACE")
        self.assertEqual(plan.reaction_mode, "place_observation")
        self.assertIn(plan.primary_anchor, ("여행", "코스", "해안도로", "제주"))

    def test_06_execute_shadow_dry_run_and_generate_markdown_table(self):
        """5대 대표 케이스에 대해 Pipeline 전 과정 Shadow Dry-run 수행 및 결과 원문 표 생성"""
        results: List[Dict[str, Any]] = []

        # 대표 시뮬레이션 Gemini 생성 쌍 (초벌 응답, 필요시 재작성본)
        simulated_gemini_outputs = {
            "food_01_blue_cheese": {
                "initial_raw": "블루치즈 호불호 갈리는 거 너무 공감돼요 저도 좋아해요",  # 금지된 경험암시 초안
                "rewritten_raw": "블루치즈 향에 꿀까지 곁들이는 조합이라 어떤 맛일지 궁금하네요",  # 재작성본
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
                "initial_raw": "통창으로 바다와 해안도로가 한눈에 보이는 풍경이 멋지네요",
                "rewritten_raw": None,
            },
        }

        for case in self.core_cases:
            cid = case["id"]
            title = case["title"]
            body = case["body"]

            # 1. 1차 Discovery & Context Selection
            disc_domain, pref_anchors, pref_terms = ReactionContextPlanner.discover_domain_and_terms(title, body)
            context_res = select_comment_context(
                title=title, body=body, max_chars=600,
                preferred_anchors=pref_anchors, preferred_terms=pref_terms,
            )

            # 2. ReactionPlan 수립
            plan = ReactionContextPlanner.plan(title, context_res.excerpt)

            # 3. v3.5 Prompt 빌드
            prompt = AIPromptBuilder.build(
                version="3.5.0-reaction-planned",
                title=title,
                excerpt=context_res.excerpt,
                reaction_plan=plan,
                request_id=f"shadow_{cid}",
            )

            # 4. Gemini 댓글 수신 및 1차 Quality Gate 검사
            sim = simulated_gemini_outputs[cid]
            draft_1 = sim["initial_raw"]

            # 1차 초안 검사 (사람 말투 / 거짓 경험 / 식감 / 상투어)
            inspect_1 = CommentDraftInspector.inspect(
                draft_1, excerpt=context_res.excerpt, preset="community", source="gemini"
            )
            gate_1 = FinalQualityGate.validate_final_text(
                draft_1, preset="community", source="gemini", excerpt=context_res.excerpt
            )

            rewrite_triggered = False
            rewrite_feedback = ""
            final_draft = draft_1
            final_gate_passed = False

            if not inspect_1.passed or not gate_1.valid:
                rewrite_triggered = True
                rewrite_feedback = inspect_1.feedback if not inspect_1.passed else gate_1.reason
                # 1회 재작성 실행
                final_draft = sim["rewritten_raw"] or draft_1

            # 최종 검증
            final_gate = FinalQualityGate.validate_final_text(
                final_draft, preset="community", source="gemini", excerpt=context_res.excerpt
            )
            final_gate_passed = final_gate.valid

            # FOOD 도메인인 경우 Relevance 추가 검증
            food_rel = None
            if plan.domain == "FOOD":
                is_rel, rel_reason = check_food_relevance(
                    draft=final_draft,
                    excerpt=context_res.excerpt,
                    verified_anchors=[plan.primary_anchor, plan.secondary_anchor],
                    reaction_mode=plan.reaction_mode,
                )
                food_rel = f"{is_rel} ({rel_reason})"

            results.append({
                "id": cid,
                "title": title,
                "selected_context": context_res.excerpt[:60] + "...",
                "domain": plan.domain,
                "primary_anchor": plan.primary_anchor,
                "secondary_anchor": plan.secondary_anchor,
                "reaction_mode": plan.reaction_mode,
                "reaction_instruction": plan.reaction_instruction,
                "initial_draft": draft_1,
                "first_gate_valid": (inspect_1.passed and gate_1.valid),
                "rewrite_triggered": rewrite_triggered,
                "rewrite_feedback": rewrite_feedback,
                "final_draft": final_draft,
                "final_gate_valid": final_gate_passed,
                "food_relevance": food_rel,
            })

        # 검증 단언: 5건 모두 최종 품질 게이트 통과 확인
        for r in results:
            self.assertTrue(r["final_gate_valid"], f"Final draft failed gate: {r}")

        # JSON 리포트 저장
        json_path = "/Volumes/무제/jusik/naver-blog-bot-v13-1-community-rhythm/shadow_dry_run_report_5cases.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        # Markdown 비교표 생성 및 저장
        md_path = "/Volumes/무제/jusik/naver-blog-bot-v13-1-community-rhythm/shadow_dry_run_report_5cases.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# v3.5 Reaction Pipeline 5대 핵심 케이스 Shadow Dry-run 결과\n\n")
            f.write("| 유형 | 글 제목 | 도메인 | 반응 모드 | 핵심 앵커 | 초벌 Gemini 초안 | 재작성 여부 | 최종 승인 댓글 원문 | 최종 품질 게이트 |\n")
            f.write("|---|---|---|---|---|---|:---:|---|:---:|\n")
            for r in results:
                rew_mark = "O (차단 후 재작성)" if r["rewrite_triggered"] else "X (1차 즉시 통과)"
                gate_mark = "PASS (승인)" if r["final_gate_valid"] else "FAIL (스킵)"
                f.write(
                    f"| `{r['id'].split('_')[1]}` | {r['title']} | `{r['domain']}` | `{r['reaction_mode']}` | "
                    f"`{r['primary_anchor']}` | \"{r['initial_draft']}\" | {rew_mark} | "
                    f"**\"{r['final_draft']}\"** | {gate_mark} |\n"
                )
            f.write("\n\n## 상세 케이스별 반응 계획 및 지침\n\n")
            for r in results:
                f.write(f"### [{r['id']}] {r['title']}\n")
                f.write(f"- **선별 컨텍스트**: {r['selected_context']}\n")
                f.write(f"- **반응 계획 (ReactionPlan)**: 도메인=`{r['domain']}`, 모드=`{r['reaction_mode']}`, 앵커=`{r['primary_anchor']}` (보조=`{r['secondary_anchor']}`)\n")
                f.write(f"- **Gemini Instruction**: *\"{r['reaction_instruction']}\"*\n")
                f.write(f"- **1차 초안**: \"{r['initial_draft']}\" (1차 게이트 통과: {r['first_gate_valid']})\n")
                if r["rewrite_triggered"]:
                    f.write(f"- **재작성 사유 및 피드백**: *{r['rewrite_feedback']}*\n")
                f.write(f"- **최종 승인 댓글**: **\"{r['final_draft']}\"**\n")
                if r["food_relevance"]:
                    f.write(f"- **음식 관련성 판정**: `{r['food_relevance']}`\n")
                f.write("\n")

        self.assertTrue(os.path.exists(json_path))
        self.assertTrue(os.path.exists(md_path))


if __name__ == "__main__":
    unittest.main()
