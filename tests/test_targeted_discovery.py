import unittest
from unittest.mock import MagicMock, patch
from app.models import FeedPost, FeedSourceType
from app.processor import PostProcessor
from naver.content_extractor import PostContext
from naver.discovery.query_pool import QueryRotator, DISCOVERY_QUERIES
from naver.discovery.topic_filter import DiscoveryTopicFilter, TARGET_DISCOVERY_CATEGORIES, TopicDecision
from services.ai_prompt import AIPromptBuilder
from services.comments.validators import PositiveSafetyValidator
from services.comments.intents import CommentCandidate, ReactionIntent, FirstPersonIntent
from services.draft import DraftService


class TestTargetedDiscoveryV9(unittest.TestCase):
    def test_query_rotator_initialization_and_rotation(self):
        # 1. 특정 카테고리만 활성화했을 때 쿼리 풀 구성
        rotator = QueryRotator(
            enabled_categories=["FOOD", "CAFE"],
            custom_queries=["광양 맛집", "순천 카페"],
            posts_per_query=2
        )
        self.assertIn("광양 맛집", rotator.queries)
        self.assertIn("순천 카페", rotator.queries)
        for q in rotator.queries:
            self.assertTrue(any(q in DISCOVERY_QUERIES[cat] for cat in ["FOOD", "CAFE"]) or q in ["광양 맛집", "순천 카페"])

        # 2. 2개 포스트 기록 시 다음 쿼리로 자동 전환
        first_q = rotator.current_query
        switched_1 = rotator.record_post_found()
        self.assertFalse(switched_1)
        self.assertEqual(rotator.current_query, first_q)

        switched_2 = rotator.record_post_found()
        self.assertTrue(switched_2)
        # 로테이션 완료

    def test_topic_filter_whitelist_pass(self):
        # 생활형 카테고리 정상 통과
        pass_titles = [
            ("남해 독일마을 슈바인학센 맛집 내돈내산 후기", "FOOD"),
            ("성수동 신상 디저트 카페 딸기케이크 솔직후기", "CAFE"),
            ("아이랑 주말 나들이 순천만습지 갈대밭 산책", "PARENTING"),
            ("살림 꿀팁 다이소 주방용품 추천 정리 수납", "LIVING"),
            ("여수 1박 2일 가족여행 힐링 코스 추천", "TRAVEL"),
            ("주말 동네 산책하고 집밥 먹은 소소한 일상 기록", "LIFESTYLE"),
        ]
        for title, expected_cat in pass_titles:
            allowed, cat = DiscoveryTopicFilter.is_allowed(title)
            self.assertTrue(allowed, f"Should pass: {title} (got {cat})")

    def test_topic_filter_disallowed_and_it_banned(self):
        # IT/카메라/스마트폰/금융/이슈는 탈락
        drop_titles = [
            "아이폰 17 프로 출시일 카메라 성능 비교 분석",
            "소니 A7M4 탐론 망원 렌즈 첫인상 개봉기",
            "엔비디아 GPU 주가 전망 코인 반도체 수혜주",
            "2026 청년 월세 지원금 환급금 신청 방법",
        ]
        for title in drop_titles:
            allowed, reason = DiscoveryTopicFilter.is_allowed(title)
            self.assertFalse(allowed, f"Should drop: {title} (got {reason})")

    def test_topic_decision_reports_category_evidence_and_stage(self):
        decision = DiscoveryTopicFilter.evaluate(
            "요즘 시장 흐름 정리",
            "ETF와 금리, 주식 포트폴리오를 함께 살펴봤어요",
            stage="detail",
        )
        self.assertIsInstance(decision, TopicDecision)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.blocked_category, "finance")
        self.assertEqual(decision.stage, "detail")
        self.assertIn("ETF", decision.evidence)

    def test_ambiguous_substrings_do_not_false_positive(self):
        allowed_titles = [
            "바디워시 향 좋은 제품으로 욕실 정리",
            "여행 사진 남기기 좋은 제주 산책 코스",
            "애플파이 바삭하게 굽는 홈베이킹 레시피",
            "waiting for spring 주말 일상 기록",
            "산리오 랜덤박스 개봉기 키링 굿즈",
            "컬러 콘택트렌즈 착용 메이크업 후기",
        ]
        for title in allowed_titles:
            decision = DiscoveryTopicFilter.evaluate(title, stage="card")
            self.assertTrue(decision.allowed, f"false positive: {title} / {decision}")

    def test_detail_filter_blocks_all_mutating_actions(self):
        post = FeedPost(
            key="tech:1", source=FeedSourceType.RECOMMENDATION,
            url="https://m.blog.naver.com/tech/1", title="주말 기록",
        )
        page = MagicMock()
        processor = PostProcessor(
            config={"topic_filter_enabled": True},
            like_enabled=True,
            comment_enabled=True,
            gemini_web_enabled=False,
        )
        with patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.interruptible_wait"), \
             patch("app.processor.ContentContextExtractor.extract", return_value=PostContext(
                 title="주말 기록", excerpt="소니 미러리스 카메라 렌즈와 촬영 장비를 비교했어요"
             )), \
             patch("app.processor.LikeTransactionService.resolve_like_state") as like_state, \
             patch("app.processor.CommentInteractionService.open_comment_layer") as comment_open:
            result = processor.process(page, post)
        self.assertEqual(result.like_result.error, "topic_blocked:camera")
        self.assertEqual(result.comment_result.error, "topic_blocked:camera")
        like_state.assert_not_called()
        comment_open.assert_not_called()

    def test_validator_rejects_ai_summary_and_exaggerations(self):
        # AI 요약 어투 및 유행어 차단
        bad_comments = [
            "전체적으로 메뉴 구성이 알차고 공간 분위기가 좋네요.",
            "무엇보다 짚불향이 솔솔 나서 정말 매력적이네요.",
            "메뉴 구성과 팁 정리가 잘 되어 인상적이네요.",
            "유익한 정보와 알찬 정보 가득해서 한눈에 쏙 들어오네요.",
            "완성도가 높은 구성이라 눈길을 끄네요.",
            "딸기라떼 비주얼 완전 취저네요! 대박이에요.",
            "오픈런 팁 완전 강추합니다! 안 가볼 수 없네요.",
            "저도 예전에 먹어봤는데 너무 맛있더라구요!"
        ]
        for c in bad_comments:
            cand = CommentCandidate(
                body=c,
                category="FOOD",
                reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE,
                subject="음식",
                template_id="test"
            )
            valid, reason = PositiveSafetyValidator.validate_candidate(cand)
            self.assertFalse(valid, f"Should reject: '{c}' (reason: {reason})")

    def test_validator_accepts_natural_grounded_comments(self):
        # 담백하고 자연스러운 1~2문장 통과
        good_comments = [
            "우대갈비 비주얼 좋네요. 짚불향까지 난다니 저도 한번 먹어보고 싶어요.",
            "필라프까지 같이 나오는 구성이 좋네요. 저도 다음에 먹어봐야겠어요.",
            "딸기케이크 크림이 정말 부드러워 보여요. 근처 가면 들러보고 싶네요.",
            "아이와 함께 다녀오신 풍경이 너무 따뜻하고 평화로워 보여요."
        ]
        for c in good_comments:
            cand = CommentCandidate(
                body=c,
                category="FOOD",
                reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE,
                subject="음식",
                template_id="test"
            )
            valid, reason = PositiveSafetyValidator.validate_candidate(cand)
            self.assertTrue(valid, f"Should accept: '{c}' (got reject reason: {reason})")

    def test_prompt_builder_v6_rules(self):
        prompt = AIPromptBuilder.build(
            title="남해 독일마을 소세지 플래터 맛집 후기",
            excerpt="독일식 수제 소세지와 바삭한 감자튀김, 시원한 흑맥주 조합이 훌륭했습니다."
        )
        self.assertNotIn("찐이웃", prompt)
        self.assertNotIn("~더라구요!", prompt)
        self.assertNotIn("~못 참죠", prompt)
        self.assertNotIn("~취저예요", prompt)
        self.assertIn("전체적으로", prompt)  # As a banned list item in prompt
        self.assertIn("인상적이네요", prompt) # As a banned list item in prompt
        self.assertIn("꼭", prompt)         # As a banned list item in prompt


if __name__ == "__main__":
    unittest.main()
