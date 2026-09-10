import unittest
from services.comments.community_rhythm import (
    CommentDraftInspector,
    FinalQualityGate,
    CommunityRhythmPreset,
)


class TestCommentQualityRegression(unittest.TestCase):
    """문체 검사와 사실 검사 분리 및 정밀 결합 검사 단위 테스트"""

    def test_01_single_clause_reaction_with_johgetseoyo_passes(self):
        """'좋겠어요'가 단문 반응일 때는 정상 통과 (예: '밥 양을 고를 수 있어서 좋겠어요')"""
        comment = "밥 양을 고를 수 있어서 좋겠어요"
        excerpt = "공기밥 양을 보통과 곱빼기 중에서 고를 수 있는 점이 마음에 들었습니다."
        res = CommentDraftInspector.inspect(comment, excerpt=excerpt)
        self.assertTrue(res.passed, f"Expected PASS for single-clause reaction, got: {res.code} - {res.feedback}")

    def test_02_appended_habitual_johgetseoyo_fails(self):
        """앞 문장/절에서 이미 감탄했는데 뒤에 사족으로 덧붙인 '좋겠어요'는 반려"""
        # 1. 2문장 형태
        comment_multi = "돈까스 비쥬얼 진짜 바삭하네요! 저도 먹으면 좋겠어요"
        res_multi = CommentDraftInspector.inspect(comment_multi)
        self.assertFalse(res_multi.passed)
        self.assertEqual(res_multi.code, "repetitive_tail")
        self.assertIn("중복 마무리", res_multi.feedback)

        # 2. 1문장 내 앞선 완결 감탄 뒤 덧붙임 형태
        comment_single_appended = "비쥬얼 진짜 예술이네요 나중에 먹으면 좋겠어요"
        res_single = CommentDraftInspector.inspect(comment_single_appended)
        self.assertFalse(res_single.passed)
        self.assertEqual(res_single.code, "repetitive_tail")

    def test_03_shared_ending_different_structure_passes(self):
        """앞 댓글들과 같은 어미(~네요)가 나왔어도 내용과 문장 구조가 다르면 통과"""
        recent = [
            "주차 공간이 넓어서 편하네요~",
            "웨이팅 30분이나 기다리셨다니 대단하네요",
        ]
        candidate = "수제 패티 육즙이 알차네요~"
        res = CommentDraftInspector.inspect(candidate, recent_comments=recent)
        self.assertTrue(res.passed, f"Expected PASS for different content/structure ending in ~네요, got: {res.code}")

    def test_04_shared_ending_with_structural_clone_fails(self):
        """어미와 문장 구조/평가 표현까지 복제된 댓글은 반려"""
        recent = [
            "돈까스 소스가 진짜 맛있네요~",
            "스프 리필 되는거 너무 좋네요~",
        ]
        # '너무 좋네요' 감상 어구 및 템플릿 복제
        candidate = "창가 자리에서 먹으면 분위기 너무 좋네요~"
        res = CommentDraftInspector.inspect(candidate, recent_comments=recent)
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "repetition_detected")

    def test_05_side_dish_attribute_transfer_to_main_menu_fails(self):
        """본문이 반찬(깍두기)의 속성으로 쓴 '아삭'을 메인 메뉴(갈비탕)의 속성으로 왜곡하면 반려"""
        excerpt = "깍두기가 아삭아삭하고 정말 맛있었어요. 갈비탕 국물이 진국이네요."
        candidate = "갈비탕이 아삭해서 맛있겠네요~"
        res = CommentDraftInspector.inspect(candidate, excerpt=excerpt)
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "mismatched_attribute_target")
        self.assertIn("반찬", res.feedback)

    def test_06_negation_inversion_fails(self):
        """본문의 부정 서술(바삭하지 않다)을 긍정(바삭하다)으로 왜곡하면 반려"""
        excerpt = "튀김옷이 바삭하지 않아서 살짝 아쉬웠습니다."
        candidate = "튀김이 바삭해서 정말 맛있겠네요~"
        res = CommentDraftInspector.inspect(candidate, excerpt=excerpt)
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "negation_inversion")
        self.assertIn("부정적으로 서술된 속성", res.feedback)

    def test_07_invented_causality_fails(self):
        """조리법과 식감이 본문에 인과관계 없이 따로 언급되었는데 임의로 연결하면 반려"""
        excerpt = "삼겹살을 숯불에 구웠습니다. 안심 부위는 촉촉하고 부드러웠어요."
        # '구워서 더 촉촉'은 본문에 없는 인과 연결
        candidate = "구워서 더 촉촉하겠네요~"
        res = CommentDraftInspector.inspect(candidate, excerpt=excerpt)
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "invented_causality")
        self.assertIn("인과관계", res.feedback)

    def test_08_user_direct_edit_exempt_from_ai_style_restrictions(self):
        """사용자가 직접 수정한 댓글(source == 'user_edit')은 AI 문체 제약(물결표 2개 초과 등) 면제"""
        user_edited = "정말 맛있어 보이네요~~~ 다음에 꼭 가보겠습니다!"

        # 1. FinalQualityGate 통과 검증
        gate_res = FinalQualityGate.validate_final_text(
            user_edited, preset=CommunityRhythmPreset.COMMUNITY, source="user_edit"
        )
        self.assertTrue(gate_res.valid, f"Expected valid for user_edit, got: {gate_res.code} - {gate_res.reason}")

        # 2. CommentDraftInspector 통과 검증
        inspect_res = CommentDraftInspector.inspect(user_edited, source="user_edit")
        self.assertTrue(inspect_res.passed, f"Expected inspect passed for user_edit, got: {inspect_res.code}")


if __name__ == "__main__":
    unittest.main()
