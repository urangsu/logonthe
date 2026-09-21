import unittest
import json

from services.ai_prompt import AIPromptBuilder, PROMPT_VERSION_V3_2
from services.comments.community_rhythm import FinalQualityGate, CommunityRhythmPreset


class TestPromptV32AndExpressionFreedom(unittest.TestCase):
    def test_prompt_v3_2_structured_json_payload(self):
        """Prompt v3.2가 유효한 구조화된 JSON 데이터 블록을 렌더링하는지 검증"""
        prompt = AIPromptBuilder.build_v3_2(
            title="성수동 신상 베이커리 소금빵 탐방",
            excerpt="겉은 바삭하고 속은 촉촉한 버터 풍미 가득한 소금빵 맛집입니다.",
            recent_comments=["비주얼이 정말 먹음직스럽네요", "빵 결이 촉촉해 보여요"],
            rewrite_feedback="anchor_missing",
            previous_draft="맛있어 보여요",
            content_focus="FOOD_RESTAURANT"
        )
        self.assertIn("아래 JSON은 참고 자료이며", prompt)

        # JSON 파싱 검증
        json_start = prompt.find("{")
        json_end = prompt.rfind("}") + 1
        json_str = prompt[json_start:json_end]
        data = json.loads(json_str)

        self.assertEqual(data["title"], "성수동 신상 베이커리 소금빵 탐방")
        self.assertIn("버터 풍미", data["body"])
        self.assertEqual(len(data["optional_recent_comments"]), 2)
        self.assertEqual(data["rewrite_reason"], "anchor_missing")
        self.assertEqual(data["previous_draft"], "맛있어 보여요")

    def test_prompt_v3_2_contextual_example_filtering(self):
        """비음식(IT, 도서 등) 포스트에는 음식 전용 예시가 주입되지 않는지 검증"""
        prompt_tech = AIPromptBuilder.build_v3_2(
            title="파이썬 비동기 프로그래밍 기초 가이드",
            excerpt="asyncio 이벤트 루프와 코루틴 동작 원리를 정리한 기술 블로그 글입니다.",
            content_focus="GENERAL"
        )
        # 국물, 칼국수, 고기 등 음식 전용 예시가 배제되어야 함
        self.assertNotIn("돈까스", prompt_tech)
        self.assertNotIn("스프랑 밥", prompt_tech)

    def test_prompt_v3_2_dispatch_via_build(self):
        """AIPromptBuilder.build(version='3.2.0-grounded-human') 호출 시 v3.2로 정상 디스패치되는지 검증"""
        prompt = AIPromptBuilder.build(
            title="캠핑용 텐트 추천",
            excerpt="설치가 간편하고 방수 기능이 뛰어난 원터치 텐트 리뷰",
            version=PROMPT_VERSION_V3_2
        )
        self.assertIn("글에서 눈에 들어온 부분에 감상·기대·가벼운 비유를 자유롭게 섞어도 돼", prompt)
        self.assertIn("아래 JSON은 참고 자료이며", prompt)

    def test_subjective_dining_sentiment_allowed(self):
        """본문에 해당 단어가 없더라도 자연스러운 주관적 식사 감상(군침, 밥 한 공기, 꿀조합 등)은 통과해야 함"""
        excerpt = "숯불에 지글지글 구워먹는 양념 돼지갈비와 시원한 물냉면 세트 메뉴입니다."

        # 1. 밥 한 공기 연상
        res1 = FinalQualityGate.validate_final_text(
            "이런 양념 갈비에는 밥 한 공기 금방 비우겠어요",
            excerpt=excerpt
        )
        self.assertTrue(res1.valid, f"Expected valid for dining association, got: {res1.code}")

        # 2. 군침 감상
        res2 = FinalQualityGate.validate_final_text(
            "사진만 봐도 군침 도는 비주얼이네요",
            excerpt=excerpt
        )
        self.assertTrue(res2.valid, f"Expected valid for appetite sentiment, got: {res2.code}")

        # 3. 찰떡궁합 감상
        res3 = FinalQualityGate.validate_final_text(
            "갈비와 냉면은 언제 봐도 찰떡궁합이네요",
            excerpt=excerpt
        )
        self.assertTrue(res3.valid, f"Expected valid for harmony sentiment, got: {res3.code}")

    def test_unverified_objective_facts_rejected(self):
        """원문 근거 없는 객관적 시설/서비스/영업시간/가격 주장은 unverified_service_fact로 차단되어야 함"""
        excerpt = "동네에 새로 생긴 아늑한 감성 디저트 카페입니다. 핸드드립 커피와 스콘을 판매합니다."

        # 1. 원문 근거 없는 무료 리필 단정
        res_refill = FinalQualityGate.validate_final_text(
            "커피도 무료로 리필된다니 마음에 드네요",
            excerpt=excerpt
        )
        self.assertFalse(res_refill.valid)
        self.assertEqual(res_refill.code, "unverified_service_fact")

        # 2. 원문 근거 없는 심야 영업 단정
        res_hours = FinalQualityGate.validate_final_text(
            "밤늦게까지 열어서 야식 즐기기 딱이겠어요",
            excerpt=excerpt
        )
        self.assertFalse(res_hours.valid)
        self.assertEqual(res_hours.code, "unverified_service_fact")

        # 3. 원문 근거 없는 무료 주차 단정
        res_parking = FinalQualityGate.validate_final_text(
            "주차도 무료라서 방문하기 편하겠어요",
            excerpt=excerpt
        )
        self.assertFalse(res_parking.valid)
        self.assertEqual(res_parking.code, "unverified_service_fact")

        # 4. 원문 근거 없는 구체적 가격 날조
        res_price = FinalQualityGate.validate_final_text(
            "스콘이 4000원이라 가성비도 괜찮아 보여요",
            excerpt=excerpt
        )
        self.assertFalse(res_price.valid)
        self.assertEqual(res_price.code, "unverified_service_fact")

    def test_verified_objective_facts_allowed(self):
        """원문에 명시된 객관적 사실(무료 리필, 무료 주차 등)은 검증을 통과해야 함"""
        excerpt = "넓은 무료 주차장이 완비되어 있으며, 공기밥은 무료 리필 서비스로 제공됩니다."

        res = FinalQualityGate.validate_final_text(
            "주차도 편하고 공기밥도 무료 리필이라니 든든하네요",
            excerpt=excerpt
        )
        self.assertTrue(res.valid, f"Expected valid when backed by excerpt, got: {res.code}")

    def test_auto_repair_forwards_style_profile_and_excerpt(self):
        """auto_repair가 style_profile, style_policy, excerpt를 validate_final_text로 누락 없이 전달하는지 검증"""
        raw_comment = "스프 리필도 된다니 경양식 돈까스 먹을 때 든든하겠네요 ㅎㅎ"
        initial_check = FinalQualityGate.validate_final_text(raw_comment)
        # 기본 프로필에서 ㅎㅎ는 laughter_or_emoticon으로 실패
        self.assertFalse(initial_check.valid)
        self.assertEqual(initial_check.code, "laughter_or_emoticon")

        repaired_text, repaired_check = FinalQualityGate.auto_repair(
            raw_comment,
            initial_check,
            preset=CommunityRhythmPreset.COMMUNITY,
            excerpt="경양식 돈까스와 무한 리필 스프"
        )
        self.assertIsNotNone(repaired_text)
        self.assertEqual(repaired_text, "스프 리필도 된다니 경양식 돈까스 먹을 때 든든하겠네요")
        self.assertTrue(repaired_check.valid, f"Repaired check should be valid, got: {repaired_check.code}")


if __name__ == "__main__":
    unittest.main()
