import unittest
from services.draft import DraftService


class TestDraftCleaner(unittest.TestCase):
    def test_clean_gemini_ui_headers_and_strip_emoticons_and_kkok(self):
        # 1. UI 헤더 제거 및 꼭/이모지/웃는 이모티콘 자동 제거 검증
        raw = "Gemini의 응답\n너무 맛있어 보여요! 다음에 꼭 가봐야겠어요 ㅎㅎ 😊 :)"
        cleaned = DraftService.clean_ai_response(raw)
        self.assertEqual(cleaned, "너무 맛있어 보여요! 다음에 가봐야겠어요")
        self.assertNotIn("꼭", cleaned)
        self.assertNotIn("ㅎㅎ", cleaned)
        self.assertNotIn(":)", cleaned)
        self.assertNotIn("😊", cleaned)

        raw_en = "Gemini's Response:\n정말 멋진 여행지네요! ^^ 👍"
        cleaned_en = DraftService.clean_ai_response(raw_en)
        self.assertEqual(cleaned_en, "정말 멋진 여행지네요!")

    def test_strip_kkok_patterns(self):
        # '꼭' 단어가 포함된 다양한 문맥에서 '꼭'이 자동으로 제거되는지 검증
        raw1 = "사진 보니까 저도 꼭 먹어보고 싶네요!"
        self.assertEqual(DraftService.clean_ai_response(raw1), "사진 보니까 저도 먹어보고 싶네요!")

        raw2 = "다음에 여행 갈 때 꼭 참고해야겠어요 ㅎㅎ"
        self.assertEqual(DraftService.clean_ai_response(raw2), "다음에 여행 갈 때 참고해야겠어요")

    def test_reject_header_only_as_none(self):
        # 2. UI 헤더 단독으로 추출된 경우(본문 로딩 전) None을 반환하여 로컬 엔진으로 fallback
        self.assertIsNone(DraftService.clean_ai_response("Gemini의 응답"))
        self.assertIsNone(DraftService.clean_ai_response("Gemini's Response"))
        self.assertIsNone(DraftService.clean_ai_response("Gemini"))
        self.assertIsNone(DraftService.clean_ai_response(""))

    def test_clean_markdown_codeblocks_and_quotes(self):
        # 3. 마크다운 코드블록 및 따옴표 제거
        raw = "```markdown\n\"웨이팅 팁 덕분에 편하게 다녀올 수 있겠어요!\"\n```"
        self.assertEqual(DraftService.clean_ai_response(raw), "웨이팅 팁 덕분에 편하게 다녀올 수 있겠어요!")


if __name__ == "__main__":
    unittest.main()
