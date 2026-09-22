import contextlib
import io
import threading
import unittest
from unittest.mock import patch

from app.run_control import RunControl
from naver.content_extractor import ContentContextExtractor
from scripts.compare_prompts import _evaluate_draft, _winner, _parse_args, run_comparison
from services.comments.community_rhythm import CommentDraftInspector, FinalQualityGate
from services.ai_prompt import AIPromptBuilder


class QualityRecoveryContracts(unittest.TestCase):
    def test_live_comparison_requires_explicit_model(self):
        with patch("sys.argv", ["compare_prompts.py"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                _parse_args()
        self.assertEqual(error.exception.code, 2)

    def test_default_prompt_allows_expression_without_forced_praise(self):
        prompt = AIPromptBuilder.build("긴 대기 끝에 아쉬웠던 식사", "한참 기다렸지만 기대보다 아쉬웠어요")
        self.assertIn("가벼운 비유", prompt)
        self.assertIn("정보를 다시 설명하기만 하지 말고", prompt)
        self.assertIn("억지로 긍정적인 반응을 붙이지 마", prompt)

    def test_long_intro_does_not_displace_core(self):
        raw = ("오늘 찾은 곳은 동네에 새로 생긴 가게였어요 " * 45)
        raw += "\n고등어구이는 껍질이 잘 구워졌고 반찬도 여러 가지 나왔어요"
        result = ContentContextExtractor.clean_text(raw, max_chars=700)
        self.assertIn("고등어구이", result)
        self.assertLessEqual(len(result), 700)

    def test_small_budget_is_respected(self):
        for budget in (0, 1, 3, 30):
            with self.subTest(budget=budget):
                text = ContentContextExtractor.clean_text("아주 긴 본문입니다 " * 40, budget)
                self.assertLessEqual(len(text), budget)

    def test_paid_parking_is_still_available(self):
        result = FinalQualityGate.validate("주차는 유료지만 주차 가능해서 편하겠어요", excerpt="주차는 유료입니다")
        self.assertTrue(result.valid, result.code)

    def test_negated_24_hours_is_not_evidence(self):
        result = FinalQualityGate.validate("24시간이라 시간 부담 없이 들르겠어요", excerpt="24시간 영업이 아니라 오후 8시에 마감합니다")
        self.assertFalse(result.valid)

    def test_free_coffee_is_not_free_parking(self):
        result = FinalQualityGate.validate("주차도 무료라 부담이 덜하겠어요", excerpt="커피는 무료입니다. 건물 뒤에 주차장이 있습니다")
        self.assertFalse(result.valid)

    def test_real_free_parking_is_allowed(self):
        result = FinalQualityGate.validate("주차도 무료라 부담이 덜하겠어요", excerpt="커피는 유료입니다. 무료 주차장이 있습니다")
        self.assertTrue(result.valid, result.code)

    def test_texture_paraphrase_is_allowed(self):
        result = CommentDraftInspector.inspect("겉이 바삭해서 한입 먹고 싶네요", excerpt="겉면이 바사삭하고 속은 부드러웠어요")
        self.assertTrue(result.passed, result.code)

    def test_missing_texture_is_still_rejected(self):
        result = CommentDraftInspector.inspect("겉이 바삭해서 한입 먹고 싶네요", excerpt="고등어구이를 주문했어요")
        self.assertFalse(result.passed)

    def test_second_worker_cannot_pass_paused_checkpoint(self):
        control = RunControl()
        first_done, second_done = threading.Event(), threading.Event()
        control.request_pause("test")
        first = threading.Thread(target=lambda: (control.checkpoint("first"), first_done.set()))
        second = threading.Thread(target=lambda: (control.checkpoint("second"), second_done.set()))
        first.start()
        try:
            self.assertTrue(control.pause_ack_event.wait(1))
            second.start()
            self.assertFalse(second_done.wait(0.1))
            self.assertFalse(first_done.is_set())
        finally:
            control.request_resume()
            first.join(1)
            if second.ident is not None:
                second.join(1)
        self.assertTrue(second_done.is_set())

    def test_evaluator_supplies_body_to_inspector(self):
        result = _evaluate_draft("겉이 바삭해서 한입 먹고 싶네요", "고등어구이를 주문했어요")
        self.assertFalse(result["passed"])
        self.assertEqual(result["code"], "unsupported_texture")

    def test_length_does_not_decide_quality(self):
        common = {"passed": True, "quality_gate_valid": True, "text": "text"}
        self.assertEqual(_winner(dict(common, length=40), dict(common, length=20)), "human_review_required")

    def test_dry_run_is_not_generation_failure_or_quality_comparison(self):
        with contextlib.redirect_stdout(io.StringIO()):
            results = run_comparison("unused", 1, True)
        self.assertEqual(results[0]["v3_0"]["code"], "not_run")
        self.assertIsNone(results[0]["v3_0"]["passed"])
        self.assertEqual(results[0]["winner"], "not_evaluated")
