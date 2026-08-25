import unittest
from services.draft import DraftService


class TestDraftCleaner(unittest.TestCase):
    def test_clean_gemini_ui_headers(self):
        # 1. UI 헤더와 본문이 같이 들어온 경우 헤더만 깔끔하게 제거
        raw = "Gemini의 응답\n너무 맛있어 보여요! 플래터 구성이 알차네요 ㅎㅎ"
        self.assertEqual(DraftService.clean_ai_response(raw), "너무 맛있어 보여요! 플래터 구성이 알차네요 ㅎㅎ")

        raw_en = "Gemini's Response:\n정말 멋진 여행지네요!"
        self.assertEqual(DraftService.clean_ai_response(raw_en), "정말 멋진 여행지네요!")

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
