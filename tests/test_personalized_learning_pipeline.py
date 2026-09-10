import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.models import FeedPost, FeedSourceType
from services.ai_prompt import AIPromptBuilder
from services.user_learning_service import (
    UserLearningService,
    CorpusSource,
    PersonalizedStyleProfile,
)


class TestPersonalizedLearningPipeline(unittest.TestCase):
    """개인화 학습 데이터셋 연결 및 동적 예시/문체 프로필 파이프라인 테스트"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_corpus_file = os.path.join(self.tmp_dir.name, "user_learning_corpus.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_corpus_source_classification_and_auto_submit_exclusion(self):
        """auto_submit 출처는 학습 예시 및 문체 프로필 계산에서 엄격히 배제되는지 검증"""
        raw_corpus = [
            # 1. user_edit: 사용자가 직접 수정한 좋은 예시
            {
                "timestamp": "2026-09-01 12:00:00",
                "category": "FOOD",
                "anchor": "파스타",
                "source": "gemini",
                "decision": "edited",
                "decision_origin": "user",
                "initial_draft": "진짜 파스타 너무 맛있어 보이네요 완전 최고예요",
                "final_submitted": "파스타 면이 알덴테라 식감이 남다르겠네요~",
                "is_user_edited": True,
            },
            # 2. auto_submit: 검토 없이 자동 등록된 댓글 (반드시 배제되어야 함)
            {
                "timestamp": "2026-09-01 12:05:00",
                "category": "FOOD",
                "anchor": "피자",
                "source": "auto_submit",
                "decision": "adopted",
                "decision_origin": "auto_submit",
                "initial_draft": "자동 생성된 피자 댓글입니다",
                "final_submitted": "자동 등록된 피자 댓글이라 학습에 쓰이면 안 됩니다",
                "is_user_edited": False,
            },
            # 3. user_adopted: 사용자 검토 후 승인
            {
                "timestamp": "2026-09-01 12:10:00",
                "category": "FOOD",
                "anchor": "리조또",
                "source": "gemini",
                "decision": "adopted",
                "decision_origin": "user",
                "initial_draft": "트러플 향 가득한 리조또라니 풍미가 좋겠어요",
                "final_submitted": "트러플 향 가득한 리조또라니 풍미가 좋겠어요",
                "is_user_edited": False,
            },
            # 4. skipped: 건너뛴 초안 (배제)
            {
                "timestamp": "2026-09-01 12:15:00",
                "category": "FOOD",
                "source": "gemini",
                "decision": "skipped",
                "decision_origin": "user",
                "initial_draft": "건너뛴 댓글",
                "final_submitted": "",
                "is_user_edited": False,
            },
        ]

        with patch("services.user_learning_service.USER_LEARNING_FILE", self.test_corpus_file):
            with open(self.test_corpus_file, "w", encoding="utf-8") as f:
                json.dump(raw_corpus, f, ensure_ascii=False)

            cleaned = UserLearningService.get_cleaned_corpus()
            # auto_submit과 skipped는 배제되어 2개만 남아야 함 (user_edit, user_adopted)
            self.assertEqual(len(cleaned), 2)
            for item in cleaned:
                self.assertNotEqual(item["source_type"], CorpusSource.AUTO_SUBMIT.value)
                self.assertIn(item["source_type"], (CorpusSource.USER_EDIT.value, CorpusSource.USER_ADOPTED.value))

    def test_hygiene_filtering_removes_junk_ads_and_test_text(self):
        """테스트 문구, 광고/홍보 문구, 무의미한 텍스트가 정제 필터에서 제거되는지 검증"""
        dirty_corpus = [
            # 테스트 문구
            {"decision": "edited", "final_submitted": "테스트 댓글 내용입니다~", "is_user_edited": True},
            {"decision": "edited", "final_submitted": "mock sample test string!", "is_user_edited": True},
            # 광고/스팸
            {"decision": "edited", "final_submitted": "원고료 지원받아 작성한 솔직한 리뷰입니다 https://blog.naver.com", "is_user_edited": True},
            {"decision": "edited", "final_submitted": "체험단 신청은 카톡 오픈채팅으로 문의주세요", "is_user_edited": True},
            # 매크로 클리셰
            {"decision": "edited", "final_submitted": "좋은 포스팅 잘보고 갑니다~ 서이추 신청해요!", "is_user_edited": True},
            # 너무 짧거나 긴 텍스트
            {"decision": "edited", "final_submitted": "짧음", "is_user_edited": True},
            {"decision": "edited", "final_submitted": "a" * 120, "is_user_edited": True},
            # 정상적인 정제 대상
            {"decision": "edited", "final_submitted": "웨이팅 30분 만에 입장하셨다니 인기 많은 곳인가 봐요~", "is_user_edited": True},
            {"decision": "adopted", "final_submitted": "치즈 돈까스 치즈가 진짜 듬뿍 들어가서 맛있어 보이네요!", "is_user_edited": False, "decision_origin": "user"},
        ]

        cleaned = UserLearningService.get_cleaned_corpus(dirty_corpus)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["final_submitted"], "웨이팅 30분 만에 입장하셨다니 인기 많은 곳인가 봐요~")
        self.assertEqual(cleaned[1]["final_submitted"], "치즈 돈까스 치즈가 진짜 듬뿍 들어가서 맛있어 보이네요!")

    def test_personalized_style_profile_computation(self):
        """문체 프로필(평균 길이, 물결표 비율, 종결어미, 수정 경향) 계산 검증"""
        corpus = [
            {
                "decision": "edited",
                "source_type": "user_edit",
                "initial_draft": "정말 너무 맛있는 감자탕이라 완전 대박이네요 꼭 가봐야겠어요",
                "final_submitted": "감자탕에 시래기가 듬뿍 들어가서 든든하겠네요~",
            },
            {
                "decision": "edited",
                "source_type": "user_edit",
                "initial_draft": "진짜 최고로 멋진 뷰 맛집이라서 엄청 추천합니다",
                "final_submitted": "오션뷰 좌석이라 노을 보면서 식사하기 딱 좋겠네요~",
            },
        ]
        profile = UserLearningService.compute_style_profile(corpus)
        self.assertIsInstance(profile, PersonalizedStyleProfile)
        self.assertEqual(profile.total_samples, 2)
        self.assertEqual(profile.user_edit_count, 2)
        self.assertEqual(profile.tilde_ratio, 1.0)
        self.assertIn("~네요", profile.top_endings)
        # 길이 단축 및 수식어 절제 경향 감지
        self.assertTrue(len(profile.user_edit_tendency) > 0)

    def test_dynamic_example_selection_and_statistics(self):
        """카테고리/앵커 일치 우선 선별 및 자료 수 통계(전체/정제/수정본/참조) 검증"""
        raw_corpus = [
            {
                "category": "CAFE",
                "anchor": "소금빵",
                "decision": "edited",
                "final_submitted": "갓 구운 소금빵 버터 동굴이 제대로네요~",
                "is_user_edited": True,
            },
            {
                "category": "FOOD",
                "anchor": "냉면",
                "decision": "edited",
                "final_submitted": "살얼음 동동 띄운 냉면 육수가 시원해 보이네요",
                "is_user_edited": True,
            },
            {
                "category": "FOOD",
                "anchor": "돈까스",
                "decision": "adopted",
                "decision_origin": "user",
                "final_submitted": "바삭한 등심 돈까스 소스가 독특해 보여요~",
                "is_user_edited": False,
            },
            # auto_submit (배제)
            {
                "category": "FOOD",
                "anchor": "라면",
                "decision": "adopted",
                "decision_origin": "auto_submit",
                "final_submitted": "자동 등록 라면 댓글입니다",
                "is_user_edited": False,
            },
        ]

        examples, stats = UserLearningService.select_dynamic_examples(
            category="FOOD",
            anchors=["냉면"],
            limit=2,
            raw_entries=raw_corpus,
        )

        self.assertEqual(stats["total_raw"], 4)
        self.assertEqual(stats["cleaned"], 3)  # auto_submit 제외
        self.assertEqual(stats["user_edits"], 2)
        self.assertEqual(stats["referenced"], 2)
        self.assertFalse(stats["is_fallback"])
        # '냉면' 앵커와 FOOD 일치하는 user_edit이 최우선 선별
        self.assertEqual(examples[0], "살얼음 동동 띄운 냉면 육수가 시원해 보이네요")

    def test_fallback_when_corpus_empty_or_insufficient(self):
        """코퍼스가 비었거나 정제 결과가 부족할 때 기본 예시로 안전하게 fallback되는지 검증"""
        examples, stats = UserLearningService.select_dynamic_examples(
            category="FOOD",
            anchors=[],
            limit=3,
            raw_entries=[],
        )
        self.assertTrue(stats["is_fallback"])
        self.assertEqual(stats["total_raw"], 0)
        self.assertEqual(stats["cleaned"], 0)
        self.assertEqual(len(examples), 3)
        # 기본 예시 사용 확인
        self.assertIn("스프랑 밥 무한리필이라니 경양식 돈까스 먹을 때 최고네요~", examples[0])

    def test_prompt_builder_factuality_boundary_and_personalized_integration(self):
        """프롬프트 빌더에 예시/사실 경계문 및 개인화 예시/문체 프로필이 정상 반영되는지 검증"""
        custom_examples = [
            "수제 패티 육즙이 가득해서 수제버거 맛집 느낌 제대로네요~",
            "감자튀김에 트러플 마요 소스 조합이 독특해 보여요",
        ]
        profile = PersonalizedStyleProfile(
            version="v2.0-test",
            total_samples=10,
            avg_length=35.5,
            top_endings=["~네요", "~요"],
            user_edit_tendency=["길이 단축 (간결한 1문장 선호)"],
        )

        prompt = AIPromptBuilder.build(
            title="성수동 수제버거 맛집 탐방",
            excerpt="패티 두께가 2cm에 달하고 트러플 감자튀김이 일품입니다.",
            corpus_examples=custom_examples,
            style_profile=profile,
        )

        # 1. 버전 확인
        self.assertEqual(AIPromptBuilder.PROMPT_VERSION, "2.1.0-personalized")

        # 2. 사실/예시 분리 경계문 확인
        self.assertIn("[말투 참고 예시]", prompt)
        self.assertIn("예시의 음식·장소·경험은 현재 글의 사실 근거로 사용하지 않는다", prompt)

        # 3. 주입된 동적 예시 확인
        self.assertIn("수제 패티 육즙이 가득해서 수제버거 맛집 느낌 제대로네요~", prompt)
        self.assertIn("감자튀김에 트러플 마요 소스 조합이 독특해 보여요", prompt)

        # 4. 주입된 개인화 문체 프로필 확인
        self.assertIn("개인화 문체 기준(vv2.0-test)", prompt)
        self.assertIn("평균 35자 내외", prompt)
        self.assertIn("선호 종결어미(~네요, ~요)", prompt)
        self.assertIn("사용자 수정 경향 반영: 길이 단축 (간결한 1문장 선호)", prompt)


if __name__ == "__main__":
    unittest.main()
