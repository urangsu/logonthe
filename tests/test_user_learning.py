import os
import json
import unittest
import tempfile
from unittest.mock import MagicMock, patch
from app.models import FeedPost, FeedSourceType
from services.user_learning_service import UserLearningService, USER_LEARNING_FILE
from services.comments.validators import PositiveSafetyValidator
from services.comments.intents import CommentCandidate, ReactionIntent, FirstPersonIntent


class TestUserLearningAndCorpus(unittest.TestCase):
    def setUp(self):
        # 임시 테스트 경로 설정
        self.orig_user_file = USER_LEARNING_FILE

    def test_length_validator_enforces_100_char_hard_max(self):
        """댓글 길이 정책(v7): 100자 이하는 통과, 101자 이상은 reject 검증"""
        # 1. 50자 (정상 통과)
        cand_ok = CommentCandidate(
            body="탐론 렌즈 가성비 구성이 알차네요! 펜탁스 색감도 너무 예뻐요.",
            category="IT_GADGET",
            reaction_intent=ReactionIntent.DETAIL_PRAISE,
            first_person_intent=FirstPersonIntent.NONE,
            subject="탐론",
            template_id="test"
        )
        valid, reason = PositiveSafetyValidator.validate_candidate(cand_ok)
        self.assertTrue(valid)

        # 2. 101자 (거부)
        cand_over = CommentCandidate(
            body="탐론 70-300mm 망원 렌즈 가성비 구성이 정말 알차 보이네요! 펜탁스 바디에 마운트한 사진 보니까 색감도 너무 예쁘게 잘 나오는 것 같아요. 다음에 출사 갈 때 저도 다뤄보고 싶네요. 사진 결과물이 아주 만족스러워 보입니다.",
            category="IT_GADGET",
            reaction_intent=ReactionIntent.DETAIL_PRAISE,
            first_person_intent=FirstPersonIntent.NONE,
            subject="탐론",
            template_id="test"
        )
        self.assertTrue(len(cand_over.body) > 100)
        valid_over, reason_over = PositiveSafetyValidator.validate_candidate(cand_over)
        self.assertFalse(valid_over)
        self.assertIn("length_out_of_bounds", reason_over)

    def test_user_learning_service_records_edit(self):
        """사용자가 수정한 최종 댓글이 학습용 JSON 파일에 올바르게 축적되는지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            test_file = os.path.join(tmp, "user_learning.json")
            with patch("services.user_learning_service.USER_LEARNING_FILE", test_file):
                post = FeedPost(key="test_123", url="https://m.blog.naver.com/test/123", title="테스트 글", source=FeedSourceType.NEIGHBOR)
                UserLearningService.record_submission(
                    post=post,
                    initial_draft="초안 댓글입니다.",
                    final_submitted="사용자가 직접 다듬은 멋진 댓글입니다 ㅎㅎ",
                    category="FOOD",
                    anchor="딸기라떼",
                    evidence_span="생딸기 딸기라떼",
                    source="gemini",
                )

                self.assertTrue(os.path.exists(test_file))
                with open(test_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.assertEqual(len(data), 1)
                    self.assertTrue(data[0]["is_user_edited"])
                    self.assertEqual(data[0]["anchor"], "딸기라떼")
                    self.assertEqual(data[0]["source"], "gemini")
                    self.assertEqual(data[0]["decision"], "edited")
                    self.assertIn("post_key_hash", data[0])
                    self.assertNotIn("post_url", data[0])
                    self.assertNotIn("post_title", data[0])

    def test_records_skip_and_infers_only_grounded_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = os.path.join(tmp, "user_learning.json")
            with patch("services.user_learning_service.USER_LEARNING_FILE", test_file):
                post = FeedPost(
                    key="test_456", url="https://m.blog.naver.com/test/456",
                    title="성수 딸기라떼 카페", excerpt="생딸기와 크림이 올라간 딸기라떼",
                    source=FeedSourceType.NEIGHBOR,
                )
                UserLearningService.record_decision(
                    post=post,
                    initial_draft="딸기라떼 색감이 너무 이쁘네요~",
                    source="gemini",
                    decision="skipped",
                    rejection_reason="user_skip",
                )
                with open(test_file, "r", encoding="utf-8") as handle:
                    entry = json.load(handle)[0]
                self.assertEqual(entry["decision"], "skipped")
                self.assertEqual(entry["anchor"], "딸기라떼")
                self.assertEqual(entry["final_submitted"], "")
                self.assertNotIn("post_url", entry)


if __name__ == "__main__":
    unittest.main()
