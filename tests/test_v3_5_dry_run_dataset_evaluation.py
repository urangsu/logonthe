# -*- coding: utf-8 -*-
"""
v3.5 Reaction Pipeline 50-Item Dry-run Benchmark & Quality KPI Evaluation
"""

import json
import os
import unittest
from services.reaction_planner import ReactionContextPlanner
from services.comment_context_selector import select_comment_context
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import FinalQualityGate, CommentDraftInspector
from app.processor import check_food_relevance
from tests.dry_run_dataset_v3_5 import FIXTURES_50


class TestV35DryRunDatasetEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = FIXTURES_50

    def test_01_domain_classification_accuracy_kpi(self):
        """KPI: 도메인 분류 정확도 >= 95% (목표 50개 중 48개 이상 정확)"""
        total = len(self.fixtures)
        correct = 0
        mismatches = []

        for item in self.fixtures:
            plan = ReactionContextPlanner.plan(
                title=item["title"],
                excerpt=item["body"],
            )
            if plan.domain == item["expected_domain"]:
                correct += 1
            else:
                mismatches.append(
                    f"[{item['id']}] Expected {item['expected_domain']}, got {plan.domain} (title={item['title']})"
                )

        accuracy = correct / total
        self.assertGreaterEqual(
            accuracy,
            0.95,
            f"Domain accuracy {accuracy:.1%} is below 95% threshold! Mismatches: {mismatches}",
        )

    def test_02_food_reaction_mode_and_priority_kpi(self):
        """KPI: FOOD 음식 중심 반응률 >= 90% 및 블루치즈/식감 우선순위 검증"""
        food_items = [f for f in self.fixtures if f["category"] == "FOOD"]
        self.assertEqual(len(food_items), 20)

        correct_modes = 0
        for item in food_items:
            plan = ReactionContextPlanner.plan(
                title=item["title"],
                excerpt=item["body"],
            )
            self.assertEqual(plan.domain, "FOOD")
            # 모드가 허용된 reaction_mode 범위 내인지 확인
            self.assertIn(
                plan.reaction_mode,
                ["taste_reaction", "combination_curiosity", "visual_reaction", "future_interest"],
            )
            # 블루치즈 fixture는 combination_curiosity 필수
            if item["id"] == "food_01_blue_cheese":
                self.assertEqual(plan.reaction_mode, "combination_curiosity")
                self.assertEqual(plan.primary_anchor, "블루치즈")
            # 성수동 돈카츠 fixture는 taste_reaction 필수
            elif item["id"] == "food_02_rich_taste_donkatsu":
                self.assertEqual(plan.reaction_mode, "taste_reaction")
            # 2개 음식 앵커 삼겹살 fixture는 combo가 아닌 taste_reaction 필수
            elif item["id"] == "food_03_two_items_taste_priority":
                self.assertEqual(plan.reaction_mode, "taste_reaction")

            if plan.reaction_mode == item.get("expected_mode"):
                correct_modes += 1

        food_mode_accuracy = correct_modes / len(food_items)
        self.assertGreaterEqual(
            food_mode_accuracy,
            0.90,
            f"FOOD mode accuracy {food_mode_accuracy:.1%} is below 90% threshold",
        )

    def test_03_implied_shared_experience_zero_percent_kpi(self):
        """KPI: 경험 암시 = 0% (차단율 100%)"""
        blocked_total = 0
        blocked_success = 0

        for item in self.fixtures:
            for draft in item.get("sample_blocked_drafts", []):
                blocked_total += 1
                res = FinalQualityGate.validate_final_text(draft, preset="community", source="gemini")
                if not res.valid and res.code in {"implied_shared_experience", "fake_experience"}:
                    blocked_success += 1

        self.assertGreater(blocked_total, 0)
        self.assertEqual(
            blocked_success,
            blocked_total,
            f"Failed to block some implied shared experience drafts! ({blocked_success}/{blocked_total})",
        )

    def test_04_allowed_drafts_quality_pass_kpi(self):
        """KPI: 정상 관찰/궁금증/미래의향 댓글 통과율 100%"""
        allowed_total = 0
        allowed_success = 0
        failed_drafts = []

        for item in self.fixtures:
            for draft in item.get("sample_allowed_drafts", []):
                allowed_total += 1
                res = FinalQualityGate.validate_final_text(draft, preset="community", source="gemini")
                if res.valid:
                    allowed_success += 1
                else:
                    failed_drafts.append(f"[{item['id']}] '{draft}': {res.code} - {res.reason}")

        self.assertGreater(allowed_total, 0)
        self.assertEqual(
            allowed_success,
            allowed_total,
            f"Some allowed drafts were incorrectly blocked! Failed: {failed_drafts}",
        )

    def test_05_food_relevance_three_tier_kpi(self):
        """KPI: 댓글 핵심 소재 적합률 >= 90% (메뉴명 없는 감각적 패러프레이징 허용)"""
        food_items = [f for f in self.fixtures if f["category"] == "FOOD"]
        relevance_passes = 0
        total_checks = 0

        for item in food_items:
            for draft in item.get("sample_allowed_drafts", []):
                total_checks += 1
                is_rel, reason = check_food_relevance(
                    draft=draft,
                    excerpt=item["body"],
                    verified_anchors=[item.get("expected_anchor", "")],
                    reaction_mode=item.get("expected_mode", "taste_reaction"),
                )
                if is_rel:
                    relevance_passes += 1

        relevance_rate = relevance_passes / total_checks
        self.assertGreaterEqual(
            relevance_rate,
            0.90,
            f"Food relevance rate {relevance_rate:.1%} is below 90% threshold",
        )

    def test_06_future_interest_anti_repetition_streak_kpi(self):
        """KPI: 동일 reaction (future_interest) 연속 2회 초과 반복 없음"""
        # 연속 5개 글이 단순 메뉴만 있는 글이라도 3연속 future_interest가 나오지 않아야 함
        recent_history = []
        for i in range(5):
            plan = ReactionContextPlanner.plan(
                title=f"식당 메뉴 소개 {i}",
                excerpt=f"이 집의 메뉴는 불고기 정식입니다. 가격은 12000원입니다.",
                recent_reaction_modes=recent_history,
            )
            recent_history.append(plan.reaction_mode)

        # 3연속 future_interest 검사
        has_3_streak = False
        for idx in range(len(recent_history) - 2):
            if (
                recent_history[idx] == "future_interest"
                and recent_history[idx + 1] == "future_interest"
                and recent_history[idx + 2] == "future_interest"
            ):
                has_3_streak = True
                break

        self.assertFalse(has_3_streak, f"Found 3 consecutive future_interest modes: {recent_history}")

    def test_07_generate_and_save_dry_run_report(self):
        """전체 50개 데이터셋에 대한 v3.5 ReactionPlan 및 프롬프트 생성 결과 JSON 리포트 저장"""
        report_entries = []

        for item in self.fixtures:
            context_res = select_comment_context(
                title=item["title"],
                body=item["body"],
                max_chars=600,
            )
            plan = ReactionContextPlanner.plan(
                title=item["title"],
                excerpt=context_res.excerpt,
            )
            prompt = AIPromptBuilder.build(
                version="3.5.0-reaction-planned",
                title=item["title"],
                excerpt=context_res.excerpt,
                reaction_plan=plan,
                request_id=f"dry_run_{item['id']}",
            )

            report_entries.append({
                "id": item["id"],
                "category": item["category"],
                "title": item["title"],
                "selected_context_len": len(context_res.excerpt),
                "needs_more_context": context_res.needs_more_context,
                "domain": plan.domain,
                "reaction_mode": plan.reaction_mode,
                "primary_anchor": plan.primary_anchor,
                "secondary_anchor": plan.secondary_anchor,
                "evidence": plan.evidence,
                "instruction": plan.reaction_instruction,
                "prompt_sample": prompt[:200] + "...",
            })

        output_json = "/Volumes/무제/jusik/naver-blog-bot-v13-1-community-rhythm/dry_run_report_v3_5.json"
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report_entries, f, ensure_ascii=False, indent=2)

        output_md = "/Volumes/무제/jusik/naver-blog-bot-v13-1-community-rhythm/dry_run_report_v3_5.md"
        with open(output_md, "w", encoding="utf-8") as f:
            f.write("# v3.5 Reaction Pipeline 50-Item Dry-run Benchmark Report\n\n")
            f.write("| No | ID | Category | Domain | Mode | Primary Anchor | Needs More Context |\n")
            f.write("|---|---|---|---|---|---|---|\n")
            for idx, r in enumerate(report_entries, 1):
                f.write(f"| {idx} | {r['id']} | {r['category']} | {r['domain']} | {r['reaction_mode']} | {r['primary_anchor']} | {r['needs_more_context']} |\n")
            f.write("\n## Detailed Reaction Plans\n\n")
            for r in report_entries:
                f.write(f"### [{r['id']}] {r['title']}\n")
                f.write(f"- **Domain / Mode**: `{r['domain']}` / `{r['reaction_mode']}`\n")
                f.write(f"- **Primary / Secondary Anchor**: `{r['primary_anchor']}` / `{r['secondary_anchor']}`\n")
                f.write(f"- **Evidence**: {r['evidence']}\n")
                f.write(f"- **Instruction**: {r['instruction']}\n\n")

        self.assertTrue(os.path.exists(output_json))
        self.assertTrue(os.path.exists(output_md))
        self.assertEqual(len(report_entries), 50)


if __name__ == "__main__":
    unittest.main()
