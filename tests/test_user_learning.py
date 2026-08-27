import os
import json
import unittest
import tempfile
from unittest.mock import MagicMock, patch
from app.models import FeedPost, FeedSourceType
from services.user_learning_service import UserLearningService, USER_LEARNING_FILE
from services.visited_comment_collector import VisitedCommentCollector, ACCUMULATED_COMMENTS_FILE
from services.comments.validators import PositiveSafetyValidator
from services.comments.intents import CommentCandidate, ReactionIntent, FirstPersonIntent


class TestUserLearningAndCorpus(unittest.TestCase):
    def setUp(self):
        # 임시 테스트 경로 설정
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.orig_user_file = USER_LEARNING_FILE
        self.orig_acc_file = ACCUMULATED_COMMENTS_FILE

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
        test_file = os.path.join(self.temp_dir.name, 'learning.json')
        with patch("services.user_learning_service.USER_LEARNING_FILE", test_file):
            if os.path.exists(test_file):
                os.remove(test_file)

            post = FeedPost(key="test_123", url="https://m.blog.naver.com/test/123", title="테스트 글", source=FeedSourceType.NEIGHBOR)
            UserLearningService.record_submission(
                post=post,
                initial_draft="초안 댓글입니다.",
                final_submitted="사용자가 직접 다듬은 멋진 댓글입니다 ㅎㅎ",
                category="FOOD"
            )

            self.assertTrue(os.path.exists(test_file))
            with open(test_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.assertEqual(len(data), 1)
                self.assertTrue(data[0]["is_user_edited"])
                self.assertEqual(data[0]["initial_draft"], "초안 댓글입니다.")
                self.assertEqual(data[0]["final_submitted"], "사용자가 직접 다듬은 멋진 댓글입니다 ㅎㅎ")

            if os.path.exists(test_file):
                os.remove(test_file)

    def test_visited_comment_collector_scrubs_privacy(self):
        """수집 시 닉네임, 연락처, 매크로를 정확히 걸러내는지 검증"""
        raw = "@홍길동 010-1234-5678 사진 비주얼이 너무 예뻐서 저장해뒀어요 ㅎㅎ"
        clean = VisitedCommentCollector.scrub_privacy(raw)
        self.assertNotIn("@홍길동", clean)
        self.assertNotIn("010-1234-5678", clean)
        self.assertIn("사진 비주얼이 너무 예뻐서 저장해뒀어요 ㅎㅎ", clean)


if __name__ == "__main__":
    unittest.main()
