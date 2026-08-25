import unittest
from services.contextual_draft import ContextualDraftEngine
from services.comments.categories import CATEGORY_POLICIES


class TestCommentGrammar(unittest.TestCase):
    """
    Comment Grammar Gate:
    - 문자열 조합 오류(예: '먹어보다 싶네요', '가보다 싶네요') 0건 검증
    - META Subject(맛집, 후기 등) 단독 문장 조합 0건 검증
    """

    SAMPLE_POSTS = [
        ("광양 중마동 장어덮밥 히츠마부시 맛집 후기", "장어가 정말 부드럽고 오차즈케로 마무리하니 깔끔했습니다."),
        ("성수동 신상 디저트 카페 딸기라떼 맛집", "분위기가 너무 아늑하고 딸기라떼와 크로플 조합이 최고였습니다."),
        ("여수 힐링 여행 코스 산책하기 좋은 곳", "바다 풍경과 함께 산책로를 걸으니 힐링되는 기분이었습니다."),
        ("GS25 스폰지밥 랜덤키링 내돈내산", "편의점 들렀다가 너무 귀여워서 하나 사봤어요."),
        ("남자 시스루 쉐도우펌 헤어 스타일 추천", "자연스러운 컬감이 살아있는 시스루 쉐도우펌 후기입니다."),
        ("원룸 인테리어 조명 방꾸미기 팁", "조명 하나 바꿨을 뿐인데 방 분위기가 훨씬 아늑해졌어요."),
        ("아이패드 에어 6세대 실사용 리뷰", "화면 반응 속도가 빠르고 휴대성이 뛰어나네요.")
    ]

    def test_no_invalid_grammar_phrases(self):
        invalid_patterns = [
            "먹어보다 싶",
            "마셔보다 싶",
            "가보다 싶",
            "참고해보다 싶",
            "써보다 싶",
            "뽑아보다 싶",
            "따라 해보다 싶",
            "맛집 맛있어",
            "후기 맛있어",
            "추천 먹어보고"
        ]

        for title, excerpt in self.SAMPLE_POSTS:
            res = ContextualDraftEngine.generate(title, excerpt)
            for pattern in invalid_patterns:
                self.assertNotIn(
                    pattern, res.body,
                    f"Invalid grammar pattern '{pattern}' found in generated comment: '{res.body}' for post '{title}'"
                )

    def test_all_categories_generate_sufficient_candidates(self):
        for cat in CATEGORY_POLICIES:
            title = f"{cat} 관련 테스트 글"
            res = ContextualDraftEngine.generate(title, "본문 테스트 내용입니다.")
            self.assertTrue(len(res.body) > 10)


if __name__ == "__main__":
    unittest.main()
