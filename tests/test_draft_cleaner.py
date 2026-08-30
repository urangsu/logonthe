import unittest
from services.draft import DraftService
from services.comments.community_rhythm import FinalQualityGate, CommunityRhythmPreset


class TestDraftCleaner(unittest.TestCase):
    def test_clean_gemini_ui_headers_and_quotes(self):
        # 1. UI 헤더 제거 및 따옴표/마크다운 제거 검증
        raw = "Gemini의 응답:\n\"너무 맛있어 보이네요 다음에 가보고싶어요~\""
        cleaned = DraftService.clean_ai_response(raw)
        self.assertEqual(cleaned, "너무 맛있어 보이네요 다음에 가보고싶어요~")

        gate_res = FinalQualityGate.validate_final_text(cleaned, preset="community", source="gemini")
        self.assertTrue(gate_res.valid)

        raw_en = "Gemini's Response:\n'경치 너무 이쁜데요 나중에 가봐야겠어요~'"
        cleaned_en = DraftService.clean_ai_response(raw_en)
        self.assertEqual(cleaned_en, "경치 너무 이쁜데요 나중에 가봐야겠어요~")
        gate_res_en = FinalQualityGate.validate_final_text(cleaned_en, preset="community", source="gemini")
        self.assertTrue(gate_res_en.valid)

    def test_clean_markdown_codeblocks_and_quotes(self):
        # 2. 마크다운 코드블록 및 따옴표 제거
        raw = "```markdown\n\"웨이팅 팁 덕분에 편하게 다녀올 수 있겠네요~\"\n```"
        self.assertEqual(DraftService.clean_ai_response(raw), "웨이팅 팁 덕분에 편하게 다녀올 수 있겠네요~")

    def test_reject_header_only_as_none(self):
        # 3. UI 헤더 단독으로 추출된 경우(본문 로딩 전) None을 반환하여 로컬 엔진으로 fallback
        self.assertIsNone(DraftService.clean_ai_response("Gemini의 응답"))
        self.assertIsNone(DraftService.clean_ai_response("Gemini's Response"))
        self.assertIsNone(DraftService.clean_ai_response("Gemini"))
        self.assertIsNone(DraftService.clean_ai_response(""))

    def test_quality_gate_rejects_emojis_and_formal_endings_without_silent_deletion(self):
        # 4. 금지어/이모지는 DraftService가 임의 삭제하지 않고 원본 보존하여 FinalQualityGate가 확실히 거부
        raw_dirty = "Gemini의 응답\n너무 맛있어 보여요! 다음에 꼭 가봐야겠어요 ㅎㅎ 😊 :)"
        cleaned = DraftService.clean_ai_response(raw_dirty)
        self.assertIn("꼭", cleaned)
        self.assertIn("😊", cleaned)

        gate_res = FinalQualityGate.validate_final_text(cleaned, preset="community", source="gemini")
        self.assertFalse(gate_res.valid)
        self.assertIn(gate_res.code, {"hard_banned_emoticon", "hard_banned_pressure_word", "hard_banned_period", "absolute_or_pressure", "laughter_or_emoticon"})


if __name__ == "__main__":
    unittest.main()
