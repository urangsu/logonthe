import unittest
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import (
    CommentDraftInspector,
    FinalQualityGate,
    CommunityRhythmPreset,
)
from app.models import StylePlan


class TestHumanTonePromptAndInspector(unittest.TestCase):
    """실제 사람 말투 반영 프롬프트 및 5단계 검사기 단위 테스트"""

    def test_01_prompt_builder_structure_and_examples(self):
        """프롬프트 구조가 사용자 지시서 표준과 대표 예시 3개를 정확히 포함하는지 확인"""
        prompt = AIPromptBuilder.build(
            title="수유 맛집 돈까스 방문기",
            excerpt="경양식 돈까스인데 스프와 밥이 무한리필입니다.",
        )
        # 1. 핵심 섹션 존재 여부
        self.assertIn("[말투 기준]", prompt)
        self.assertIn("[작성 방식]", prompt)
        self.assertIn("[말투 참고 예시]", prompt)
        self.assertIn("[사실 기준]", prompt)
        self.assertIn("[데이터]", prompt)
        self.assertIn("[출력]", prompt)

        # 2. 대표 예시 3개 확인
        self.assertIn("스프랑 밥 무한리필이라니 경양식 돈까스 먹을 때 최고네요~", prompt)
        self.assertIn("텐동 튀김 비쥬얼 진짜 예술이네요~", prompt)
        self.assertIn("올레시장 맛있는거 진짜 많죠 넘 좋네요~", prompt)

        # 3. 습관적 중복 덧붙임 방지 지침 확인
        self.assertIn("좋겠어요, 참고해야겠어요, 기억해둬야겠네요, 한번 가봐야겠어요, 도움이 될 것 같아요", prompt)

    def test_02_prompt_builder_rewrite_feedback(self):
        """1회 재작성 피드백이 프롬프트에 올바르게 주입되는지 확인"""
        feedback = "마지막 문장에 습관적인 중복 마무리('가봐야겠')가 붙어 있습니다."
        prompt = AIPromptBuilder.build(
            title="테스트 제목",
            excerpt="테스트 본문 내용",
            rewrite_feedback=feedback,
            recent_repeats="가봐야겠",
        )
        self.assertIn("[수정 요청 (1회 재작성)]", prompt)
        self.assertIn(feedback, prompt)
        self.assertIn("가봐야겠", prompt)

    def test_03_inspector_stage1_short_text(self):
        """1단계: 10자 미만 너무 짧은 텍스트 반려"""
        res = CommentDraftInspector.inspect("와 대박")
        self.assertFalse(res.passed)
        self.assertEqual(res.stage, 1)
        self.assertEqual(res.code, "too_short")

    def test_04_inspector_stage2_fake_experience(self):
        """2단계: 거짓 방문/사용 경험 반려"""
        res = CommentDraftInspector.inspect("저도 지난번에 가봤는데 돈까스 진짜 맛있더라구요")
        self.assertFalse(res.passed)
        self.assertEqual(res.stage, 2)
        self.assertEqual(res.code, "fake_experience")
        self.assertIn("거짓 경험", res.feedback)

    def test_05_inspector_stage3_explanatory_tone(self):
        """3단계: 설명조/평가조 어구 반려"""
        res = CommentDraftInspector.inspect("돈까스 부분을 이렇게 풀어주니 새롭게 보이네요~")
        self.assertFalse(res.passed)
        self.assertEqual(res.stage, 3)
        self.assertEqual(res.code, "explanatory_tone")
        self.assertIn("설명조", res.feedback)

    def test_06_inspector_stage3_repetitive_tail(self):
        """3단계: 앞 문장과 겹치는 습관적 중복 마무리 반려"""
        res = CommentDraftInspector.inspect("돈까스 비쥬얼 진짜 먹음직스럽네요 다음에 꼭 가봐야겠어요")
        self.assertFalse(res.passed)
        self.assertEqual(res.stage, 3)
        self.assertEqual(res.code, "repetitive_tail")
        self.assertIn("중복 마무리", res.feedback)

    def test_07_inspector_stage4_recent_repetition(self):
        """4단계: 최근 댓글과 지나친 어미/어구 유사도 반려"""
        recent = [
            "돈까스 소스가 진짜 맛있네요~",
            "스프 리필 되는거 너무 좋네요~",
        ]
        res = CommentDraftInspector.inspect(
            "창가 자리에서 먹으면 분위기 너무 좋네요~",
            recent_comments=recent,
        )
        self.assertFalse(res.passed)
        self.assertEqual(res.stage, 4)
        self.assertEqual(res.code, "repetition_detected")

    def test_08_inspector_stage5_formal_register(self):
        """5단계: 딱딱한 격식체 종결형 반려"""
        res = CommentDraftInspector.inspect("스프와 밥이 무한리필이라니 참 마음에 듭니다")
        self.assertFalse(res.passed)
        self.assertEqual(res.stage, 5)
        self.assertEqual(res.code, "formal_register")

    def test_09_inspector_passes_natural_human_comment(self):
        """자연스러운 구어체 단문 댓글 정상 통과"""
        res = CommentDraftInspector.inspect("스프랑 밥 무한리필이라니 경양식 돈까스 먹을 때 최고네요~")
        self.assertTrue(res.passed)
        self.assertEqual(res.stage, 0)
        self.assertEqual(res.code, "ok")

    def test_10_user_edit_exempt_from_strict_ai_rules(self):
        """사용자가 직접 수정한 댓글은 마침표, 온점, 물결표 등이 있어도 통과"""
        user_edited_text = "스프 무한리필 완전 혜자네요... 꼭 가보겠습니다!"
        # AI 초안 검사(gemini source)에서는 마침표나 격식체가 걸리지만
        gate_res = FinalQualityGate.validate_final_text(
            user_edited_text, preset=CommunityRhythmPreset.COMMUNITY, source="user_edit"
        )
        self.assertTrue(gate_res.valid, f"Expected valid for user_edit, got: {gate_res.code} - {gate_res.reason}")


if __name__ == "__main__":
    unittest.main()
