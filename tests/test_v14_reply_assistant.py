import unittest
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import CommunityRhythmPreset
from services.my_blog_comment_thread_collector import BlogCommentNode
from services.my_blog_reply_service import MyBlogReplyService


class TestV14ReplyAssistant(unittest.TestCase):

    def test_v10_prompt_builder_structure(self):
        prompt = AIPromptBuilder.build(
            title="광주 맛집 파리바게트 신제품",
            excerpt="신선한 바게트와 샐러드가 준비되어 있습니다.",
            preset=CommunityRhythmPreset.THOUGHTFUL,
            content_focus="FOOD_RESTAURANT",
            verified_anchors=["바게트", "샐러드"]
        )
        self.assertIn("너는 네이버 블로그 이웃 댓글 초안 작성기야", prompt)
        self.assertIn("NEED_MORE_CONTEXT", prompt)
        self.assertIn("[검증된 앵커]", prompt)
        self.assertIn("바게트, 샐러드", prompt)
        self.assertIn("[콘텐츠 분류]", prompt)
        self.assertIn("FOOD_RESTAURANT", prompt)
        self.assertIn("30~80자", prompt)

    def test_reply_batch_prompt_and_parsing(self):
        prompt = MyBlogReplyService.build_batch_prompt(
            post_title="오늘의 일상",
            post_excerpt="날씨가 참 좋아서 산책을 다녀왔습니다.",
            target_comments=[
                {"comment_no": "101", "nickname": "이웃A", "text": "산책 사진 너무 힐링되네요!"},
                {"comment_no": "102", "nickname": "이웃B", "text": "어디 공원인가요?"}
            ]
        )
        self.assertIn("너는 네이버 블로그 작성자가 자기 글에 달린 댓글에 답글을 쓰는 도우미야", prompt)
        self.assertIn("[101] (이웃A): 산책 사진 너무 힐링되네요!", prompt)
        self.assertIn("[102] (이웃B): 어디 공원인가요?", prompt)

        # JSON 파싱 테스트
        sample_ai_response = """
```json
[
  {
    "comment_no": "101",
    "status": "REPLY",
    "reply": "사진 좋게 봐주셔서 감사합니다! 오늘 날씨가 참 맑더라구요~"
  },
  {
    "comment_no": "102",
    "status": "NEED_MORE_CONTEXT",
    "reply": ""
  }
]
```
"""
        parsed = MyBlogReplyService.parse_batch_response(sample_ai_response)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["comment_no"], "101")
        self.assertEqual(parsed[0]["status"], "REPLY")
        self.assertIn("사진 좋게 봐주셔서", parsed[0]["reply"])
        self.assertEqual(parsed[1]["comment_no"], "102")
        self.assertEqual(parsed[1]["status"], "NEED_MORE_CONTEXT")

    def test_comment_node_eligibility(self):
        # 1. 대상: 일반 외부 미답글
        c1 = BlogCommentNode(comment_no="1", text="좋은 글이네요!", nickname="독자1")
        self.assertTrue(c1.is_eligible_for_auto_reply)

        # 2. 제외: 대댓글
        c2 = BlogCommentNode(comment_no="2", text="대댓글입니다", is_reply=True)
        self.assertFalse(c2.is_eligible_for_auto_reply)

        # 3. 제외: 내 댓글 / 작성자 댓글
        c3 = BlogCommentNode(comment_no="3", text="내 댓글", is_mine=True)
        self.assertFalse(c3.is_eligible_for_auto_reply)

        # 4. 제외: 비밀댓글
        c4 = BlogCommentNode(comment_no="4", text="비밀", is_secret=True)
        self.assertFalse(c4.is_eligible_for_auto_reply)

        # 5. 제외: 이미 작성자 답글이 있는 경우
        c5 = BlogCommentNode(comment_no="5", text="원댓글", author_replies=["감사합니다"])
        self.assertFalse(c5.is_eligible_for_auto_reply)
        self.assertEqual(c5.existing_author_reply, "감사합니다")

    def test_v10_prompt_core_and_secondary_anchors(self):
        prompt = AIPromptBuilder.build(
            title="수제 카츠 전문점 리뷰",
            excerpt="바삭한 치즈카츠와 주차장 정보 안내",
            preset=CommunityRhythmPreset.THOUGHTFUL,
            content_focus="FOOD_RESTAURANT",
            verified_anchors=["치즈카츠"],
            secondary_anchors=["주차장"]
        )
        self.assertIn("음식/핵심: 치즈카츠", prompt)
        self.assertIn("보조: 주차장", prompt)
        self.assertIn("핵심 앵커가 하나 이상 있으면 보조 앵커만으로 댓글을 만들지 마", prompt)


if __name__ == "__main__":
    unittest.main()
