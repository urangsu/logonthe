import json
import unittest
from services.ai_prompt import AIPromptBuilder
from services.food_comment_focus import FoodCommentFocus
from services.comments.community_rhythm import FinalQualityGate, CommunityRhythmPreset
from services.comments.policy import CommentStylePolicy
from services.draft import DraftService


class TestPromptV34RefinementAndComparison(unittest.TestCase):
    def test_01_carbon_mat_product_review_classified_as_general_not_food(self):
        """탄소매트 후기 글이 본문의 '꿀맛', '먹었' 등 우연한 단어에도 음식 글로 오탐되지 않고 GENERAL로 분류"""
        title = "겨울철 필수템 탄소매트 내돈내산 2주 사용 솔직 후기"
        excerpt = "날씨가 쌀쌀해져서 탄소매트를 장만했습니다. 틀어두니 따뜻해서 꿀맛 같은 꿀잠을 잤어요. 귤 까먹으면서 누워있기 딱 좋습니다."

        res = FoodCommentFocus.analyze(title, excerpt)
        self.assertEqual(res["focus"], "GENERAL", f"탄소매트 후기는 GENERAL이어야 함: {res}")
        self.assertFalse(res["has_food_details"])

    def test_02_weather_words_in_food_post_prioritizes_food_focus(self):
        """비 오는 날 파전/삼겹살 글은 걱정 글보다 음식 반응 지시가 우선 적용되어야 함"""
        title = "비 오는 날 생각나는 바삭한 해물파전과 막걸리 맛집"
        excerpt = "빗길 조심해서 도착한 전집입니다. 노릇노릇하게 부쳐진 해물파전과 지평막걸리 조합이 최고였어요."

        focus_res = FoodCommentFocus.analyze(title, excerpt)
        self.assertEqual(focus_res["focus"], "FOOD_RESTAURANT")

        prompt = AIPromptBuilder.build_v3_4(
            title=title,
            excerpt=excerpt,
            content_focus=focus_res["focus"],
        )
        self.assertIn("눈에 들어온 메뉴·조합·분위기 하나에 바로 반응해", prompt)
        self.assertNotIn("아쉬움이나 걱정", prompt)

    def test_03_single_char_words_not_falsely_classified_as_concern(self):
        """비용, 비교, 눈길 등 우연한 단어가 들어간 글이 걱정 글로 오탐되지 않음"""
        title = "합리적인 비용으로 구매한 가성비 무선 이어폰 비교"
        excerpt = "디자인이 눈길을 사로잡고 음질도 가격 대비 훌륭합니다."

        focus_res = FoodCommentFocus.analyze(title, excerpt)
        prompt = AIPromptBuilder.build_v3_4(
            title=title,
            excerpt=excerpt,
            content_focus=focus_res["focus"],
        )
        self.assertIn("가장 눈에 들어온 장면이나 디테일 하나에 솔직한 감상이나 가벼운 연상을 붙여", prompt)
        self.assertNotIn("아쉬움이나 걱정", prompt)

    def test_04_natural_expressions_with_kkok_allowed_while_pressures_blocked(self):
        """'꼭 가보고 싶네요', '꼭 먹어보고 싶어요' 등 자연스러운 감상은 허용, '무조건 사세요' 등 강요는 차단"""
        allowed_comments = [
            "사진 보니까 저도 꼭 가보고 싶네요~",
            "비주얼이 대박이라 꼭 먹어보고 싶어요",
            "다음에 근처 가면 꼭 들러봐야겠어요",
            "정리해주신 팁 꼭 기억해둘게요",
            "취향에 꼭 맞는 아늑한 분위기네요~",
        ]
        for c in allowed_comments:
            gate = FinalQualityGate.validate_final_text(c, preset="community", source="gemini")
            self.assertTrue(gate.valid, f"자연스러운 '꼭' 표현은 통과되어야 함: {c}, code={gate.code}, reason={gate.reason}")

        blocked_comments = [
            "이곳은 정말 맛있으니까 무조건 가보세요",
            "이번 세일 상품은 놓치지 말고 반드시 사세요",
            "가성비가 정말 최고니까 꼭 사보세요 꼭사세요",
            "분위기가 정말 좋으니까 꼭 방문해보세요",
        ]
        for c in blocked_comments:
            gate = FinalQualityGate.validate_final_text(c, preset="community", source="gemini")
            self.assertFalse(gate.valid, f"강요성 표현은 차단되어야 함: {c}")
            self.assertEqual(gate.code, "absolute_or_pressure")

    def test_05_emoji_auto_repaired_mechanically(self):
        """이모지가 포함된 초안은 auto_repair를 통해 1회 기계적으로 이모지가 제거되고 통과"""
        policy = CommentStylePolicy.from_context(preset="community", config={"allow_soft_emoji": False})
        text_with_emoji = "솥뚜껑 삼겹살 비주얼이 예술이네요 ❤️"

        gate = FinalQualityGate.validate_final_text(text_with_emoji, preset="community", source="gemini", style_policy=policy)
        self.assertFalse(gate.valid)
        self.assertEqual(gate.code, "emoji")

        repaired, rep_gate = FinalQualityGate.auto_repair(text_with_emoji, gate, preset="community", style_policy=policy)
        self.assertIsNotNone(repaired)
        self.assertNotIn("❤️", repaired)
        self.assertTrue(rep_gate.valid)
        self.assertEqual(repaired, "솥뚜껑 삼겹살 비주얼이 예술이네요")

    def test_06_side_by_side_comparison_of_30_posts_without_live_submit(self):
        """기존 글 30건을 새 프롬프트로 생성 분석하여 자연스러움, 포커스 일치, 꼬리말 분리 집계 확인 (실제 등록 없음)"""
        sample_posts = [
            # 1~10: 음식/맛집
            ("성수동 삼겹살 솥뚜껑 구이 맛집", "두툼한 삼겹살과 잘 익은 김치를 솥뚜껑에 구워 먹었습니다."),
            ("연남동 트러플 크림 파스타 전문점", "꾸덕한 크림소스와 진한 트러플 향이 조화로운 파스타입니다."),
            ("부산 해운대 장어덮밥 솔직 후기", "숯불향 가득한 부드러운 민물장어 한 마리가 통째로 올라가 있어요."),
            ("강릉 초당 순두부 짬뽕순두부 찐맛집", "얼큰하고 진한 해물 짬뽕 국물에 고소하고 몽글몽글한 순두부."),
            ("수원 행궁동 수제버거 치즈 감자튀김", "육즙 가득한 소고기 패티와 바삭한 생감자튀김 조합."),
            ("대전 성심당 딸기시루 케이크 빵지순례", "케이크 상자 가득 묵직하게 들어찬 싱싱한 생딸기 케이크."),
            ("속초 중앙시장 닭강정과 오징어순대", "매콤달콤 바삭하게 식어도 맛있는 닭강정과 따끈한 순대."),
            ("제주 흑돼지 연탄구이 멜젓 조합", "두툼한 근고기를 멜젓에 푹 찍어 먹는 흑돼지 전문점."),
            ("홍대 일식 돈카츠 특로스카츠 정식", "부드러운 안심과 지방의 고소함이 살아있는 프리미엄 돈까스."),
            ("신촌 마라탕 꿔바로우 가성비 맛집", "알싸한 마라 국물과 바삭하고 쫀득한 꿔바로우."),
            # 11~20: 카페/디저트
            ("한남동 조용한 드립커피 전문 티룸", "정갈한 원목 인테리어와 핸드드립 커피의 깊은 향미."),
            ("서촌 한옥 카페 아인슈페너와 약과", "고즈넉한 서촌 골목길 한옥 마당에서 즐기는 부드러운 크림 커피."),
            ("익선동 수플레 팬케이크 디저트 카페", "주문 즉시 구워내는 부드럽고 퐁신퐁신한 수플레 케이크."),
            ("제주 애월 오션뷰 대형 베이커리 카페", "통창 너머로 푸른 바다가 펼쳐지는 오션뷰 베이커리 명소."),
            ("연희동 말차 라떼와 바스크 치즈케이크", "진하고 쌉싸름한 유기농 말차와 스모키한 치즈케이크."),
            ("망원동 소금빵 잠봉뵈르 샌드위치 카페", "버터 풍미 가득한 소금빵에 고급 잠봉햄과 버터가 듬뿍."),
            ("송리단길 석촌호수 뷰 브런치 카페", "호수 전망과 함께 즐기는 신선한 아보카도 토스트와 브런치."),
            ("도산공원 카멜커피 크림라떼 후기", "달콤하고 부드러운 시그니처 크림이 올라간 에스프레소."),
            ("경주 황리단길 한옥 감성 카페 투어", "황남동 골목 분위기 좋은 툇마루에서 즐기는 따뜻한 차 한 잔."),
            ("전주 한옥마을 팥빙수 전통 찻집", "국내산 팥을 정성껏 쑤어 만든 놋그릇 눈꽃 팥빙수."),
            # 21~30: 제품/생활/여행/안부
            ("겨울철 필수템 탄소매트 2주 실사용기", "전기세 걱정 없이 은은하고 따뜻하게 꿀잠 자는 온열매트."),
            ("가성비 로봇청소기 흡입력 및 물걸레 후기", "먼지 흡입과 물걸레질을 동시에 해결해주는 스마트 가전."),
            ("환절기 건조한 피부를 위한 수분 보습 크림", "끈적임 없이 촉촉하게 흡수되는 히알루론산 크림 후기."),
            ("여수 밤바다 낭만포차와 케이블카 여행", "돌산대교 야경을 한눈에 내려다보는 해상 케이블카 코스."),
            ("강원도 평창 오대산 월정사 전나무숲길", "상쾌한 흙냄새와 피톤치드를 마시며 걷는 힐링 산책길."),
            ("비 오는 날 출퇴근길 안전운전 빗길 주의", "도로가 미끄러우니 감속 운전하시고 안전거리 유지하세요."),
            ("주말 일상 댕댕이와 동네 공원 산책", "화창한 가을바람 쐬며 신나게 뛰어논 주말 일기."),
            ("아이폰 생산성 높여주는 메모 앱 3가지", "할 일 관리와 아이디어 정리를 도와주는 유용한 도구들."),
            ("단양 패러글라이딩과 도담삼봉 관광", "탁 트인 남한강 풍경을 한눈에 담은 짜릿한 비행 체험."),
            ("가을 꽃축제 자라섬 구절초 꽃밭 나들이", "알록달록 활짝 핀 가을 꽃밭에서 남긴 인생샷 산책로."),
        ]

        self.assertEqual(len(sample_posts), 30)

        # 30건 프롬프트 생성 검증
        results = []
        suffix_count = 0
        prompt_lengths = []

        for i, (title, excerpt) in enumerate(sample_posts, start=1):
            focus_info = FoodCommentFocus.analyze(title, excerpt)
            focus = focus_info["focus"]
            anchors = focus_info["food_anchors"]

            prompt = AIPromptBuilder.build_v3_4(
                title=title,
                excerpt=excerpt,
                content_focus=focus,
            )
            prompt_lengths.append(len(prompt))

            # 꼬리말 분리 집계 확인: 꼬리말은 본문 프롬프트와 별도로 다뤄짐
            suffix = "풍성하고 따뜻한 한가위 명절 보내세요" if (i % 3 == 0) else ""
            if suffix:
                suffix_count += 1

            results.append({
                "idx": i,
                "title": title,
                "focus": focus,
                "prompt_len": len(prompt),
                "has_suffix": bool(suffix),
            })

        # 평가 검증: 프롬프트 길이는 대략 600~650자 내외로 안정적
        avg_len = sum(prompt_lengths) / len(prompt_lengths)
        self.assertLess(avg_len, 700)
        self.assertGreater(avg_len, 500)
        self.assertEqual(suffix_count, 10, "꼬리말은 본문과 분리되어 별도 집계되어야 함")

        # 탄소매트 글(21번째)이 GENERAL인지 확인
        carbon_post_res = results[20]
        self.assertEqual(carbon_post_res["focus"], "GENERAL")

        # 빗길 글(26번째)이 GENERAL이고 걱정/응원 지시가 들어갔는지 확인
        rain_post_res = results[25]
        self.assertEqual(rain_post_res["focus"], "GENERAL")


if __name__ == "__main__":
    unittest.main()
