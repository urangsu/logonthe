import unittest

from services.food_comment_focus import FoodCommentFocus
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import CommunityRhythmPreset


class TestFoodCommentFocus(unittest.TestCase):
    def test_food_001_restaurant_menu_beats_parking(self):
        """FOOD-001: In restaurant post, menu anchors take priority over parking."""
        title = "완도 명사십리 맛집 해물뚝배기 전복 가득한 곳"
        excerpt = "명사십리 해수욕장 바로 앞이라 주차장 넓고 편해요. 전복 해물뚝배기 국물이 정말 진하고 해물이 푸짐합니다."
        res = FoodCommentFocus.analyze(title, excerpt)
        self.assertEqual(res["focus"], "FOOD_RESTAURANT")
        self.assertTrue(res["has_food_details"])
        self.assertIn("전복", res["food_anchors"])
        self.assertIn("해물뚝배기", res["food_anchors"])
        self.assertIn("주차", res["secondary_anchors"])
        # Food anchors must be present and distinct from parking
        self.assertTrue(len(res["food_anchors"]) > 0)

    def test_food_002_menu_beats_location(self):
        """FOOD-002: Menu dish beats location/accessibility."""
        title = "강남역 11번 출구 맛집 치즈카츠 돈까스 후기"
        excerpt = "강남역 11번 출구에서 도보 3분 거리라 위치가 정말 좋습니다. 치즈카츠 치즈가 듬뿍 들어가서 비주얼 최고예요."
        res = FoodCommentFocus.analyze(title, excerpt)
        self.assertEqual(res["focus"], "FOOD_RESTAURANT")
        self.assertIn("치즈카츠", res["food_anchors"])
        self.assertIn("위치", res["secondary_anchors"])

    def test_food_003_cafe_dessert_beats_interior(self):
        """FOOD-003: Cafe dessert beats interior/atmosphere."""
        title = "성수동 베이커리 카페 소금빵 맛있는 곳"
        excerpt = "인테리어가 감성적이고 통창 뷰가 예뻐요. 소금빵 결이 살아있고 딸기라떼 색감도 너무 예쁘네요."
        res = FoodCommentFocus.analyze(title, excerpt)
        self.assertEqual(res["focus"], "CAFE_DESSERT")
        self.assertIn("소금빵", res["food_anchors"])
        self.assertIn("딸기라떼", res["food_anchors"])
        self.assertIn("인테리어", res["secondary_anchors"])

    def test_food_004_convenience_store_dessert_beats_store_accessibility(self):
        """FOOD-004: Convenience store dessert beats store accessibility."""
        title = "GS25 신상디저트 한정선 요거트찹쌀떡ㅣ우리동네 편의점에서 만난 요거트 젤라또"
        excerpt = "집 앞 GS25 편의점에서 바로 살 수 있어서 편해요. 요거트찹쌀떡과 요거트 젤라또 조합이 신선합니다."
        res = FoodCommentFocus.analyze(title, excerpt)
        self.assertEqual(res["focus"], "FOOD_PRODUCT")
        self.assertTrue(any("찹쌀떡" in a or "젤라또" in a for a in res["food_anchors"]))

    def test_food_005_no_food_detail_place_fallback_allowed(self):
        """FOOD-005: When no food details exist in a restaurant/cafe post, place fallback is allowed."""
        title = "분위기 좋은 성수동 모임 장소 추천"
        excerpt = "내부 인테리어가 감성적이고 단체석이 잘 마련되어 있어서 모임하기 좋았어요. 주차도 쾌적합니다."
        res = FoodCommentFocus.analyze(title, excerpt)
        # Without food keywords, it's GENERAL or has 0 food details
        self.assertFalse(res["has_food_details"])
        self.assertEqual(len(res["food_anchors"]), 0)

    def test_food_006_hallucinated_taste_texture_prohibited_in_prompt(self):
        """FOOD-006: Prompt instructions explicitly forbid inventing taste/texture without text evidence."""
        prompt = AIPromptBuilder.build(
            title="신메뉴 리조또 후기",
            excerpt="신메뉴 새우 비스크 크림 리조또를 먹었습니다.",
            content_focus="FOOD_RESTAURANT"
        )
        self.assertIn("[음식 글 우선 규칙]", prompt)
        self.assertIn("맛이나 식감은 본문에서 직접 확인된 경우에만 말하고", prompt)
        self.assertIn("본문에 없는 맛/식감", prompt)
        self.assertIn("지어내지 마", prompt)

    def test_food_007_exact_menu_noun_retained_naturally(self):
        """FOOD-007: General post should not have food focus rules injected."""
        prompt_general = AIPromptBuilder.build(
            title="청주 베이비페어 아기옷 쇼핑 후기",
            excerpt="유모차 카시트 구경하고 아기옷 쇼핑하고 왔어요.",
            content_focus="GENERAL"
        )
        self.assertNotIn("[음식 글 우선 규칙]", prompt_general)


if __name__ == "__main__":
    unittest.main()
