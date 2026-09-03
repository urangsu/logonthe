import unittest
from unittest.mock import MagicMock, patch
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import CommunityRhythmPreset
from services.my_blog_comment_thread_collector import BlogCommentNode, MyBlogCommentThreadCollector, CommentThreadCollectionResult
from services.my_blog_reply_service import MyBlogReplyService, ReplyQualityGate
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.gemini_extension_bridge import GeminiResult, GeminiResultStatus


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

    def test_reply_batch_prompt_and_parsing(self):
        prompt = MyBlogReplyService.build_batch_prompt(
            post_title="오늘의 일상",
            post_excerpt="날씨가 참 좋아서 산책을 다녀왔습니다.",
            target_comments=[
                {"comment_no": "101", "nickname": "이웃A", "text": "산책 사진 너무 힐링되네요!"},
                {"comment_no": "102", "nickname": "이웃B", "text": "어디 공원인가요?"}
            ]
        )
        self.assertIn("너는 네이버 블로그 작성자가 자기 글에 달린 독자 댓글에 답글을 쓰는 도우미야", prompt)
        self.assertIn('"comment_no": "101"', prompt)
        self.assertIn('"nickname": "이웃A"', prompt)

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

        # 6. 제외: P0-5 identity_valid=False
        c6 = BlogCommentNode(comment_no="unresolved_1", text="식별불가", identity_valid=False)
        self.assertFalse(c6.is_eligible_for_auto_reply)

    # ==================== GEM-REPLY-001 ~ 005 (배치 계약 테스트) ====================
    def test_gem_reply_001_command_construction(self):
        """GEM-REPLY-001: GeminiCommand.create 사용 및 navigation_version=1 포함 검증"""
        mock_bridge = MagicMock()
        mock_result = GeminiResult(
            request_id="test_req",
            post_key="test_key",
            navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text='[{"comment_no": "100", "status": "REPLY", "reply": "반갑습니다!"}]'
        )
        mock_bridge.wait_for_result.return_value = mock_result

        res = MyBlogReplyService.generate_replies(
            gemini_bridge=mock_bridge,
            post_title="제목",
            post_excerpt="본문",
            target_comments=[{"comment_no": "100", "nickname": "독자", "text": "댓글"}]
        )
        self.assertEqual(res["100"]["reply"], "반갑습니다!")
        published_cmd = mock_bridge.publish.call_args[0][0]
        self.assertEqual(published_cmd.navigation_version, 1)
        self.assertTrue(published_cmd.request_id.startswith("reply_"))

    def test_gem_reply_002_003_duplicate_and_unknown_rejection(self):
        """GEM-REPLY-002 & 003: 중복 및 모르는 comment_no는 필터링되고 요청된 ID만 exact 매핑"""
        mock_bridge = MagicMock()
        mock_bridge.wait_for_result.return_value = GeminiResult(
            request_id="test",
            post_key="test",
            navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text='[{"comment_no": "1", "status": "REPLY", "reply": "답글1"}, {"comment_no": "UNKNOWN", "status": "REPLY", "reply": "유령"}, {"comment_no": "1", "status": "REPLY", "reply": "중복"}]'
        )
        res = MyBlogReplyService.generate_replies(
            gemini_bridge=mock_bridge,
            post_title="제목",
            post_excerpt="본문",
            target_comments=[{"comment_no": "1", "nickname": "독자1", "text": "글1"}]
        )
        self.assertEqual(list(res.keys()), ["1"])
        self.assertNotIn("UNKNOWN", res)
        self.assertEqual(res["1"]["reply"], "답글1")

    def test_gem_reply_004_missing_id_selective_retry(self):
        """GEM-REPLY-004: 1차 배치에서 누락된 ID만 선별하여 새 request_id로 2차 재시도"""
        mock_bridge = MagicMock()
        res1 = GeminiResult(
            request_id="req1", post_key="p1", navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text='[{"comment_no": "1", "status": "REPLY", "reply": "답글1"}]'
        )
        res2 = GeminiResult(
            request_id="req2", post_key="p2", navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text='[{"comment_no": "2", "status": "REPLY", "reply": "답글2"}]'
        )
        mock_bridge.wait_for_result.side_effect = [res1, res2]

        targets = [
            {"comment_no": "1", "nickname": "독자1", "text": "글1"},
            {"comment_no": "2", "nickname": "독자2", "text": "글2"}
        ]
        res = MyBlogReplyService.generate_replies(
            gemini_bridge=mock_bridge, post_title="제목", post_excerpt="본문", target_comments=targets
        )
        self.assertEqual(res["1"]["reply"], "답글1")
        self.assertEqual(res["2"]["reply"], "답글2")
        # 2번 호출되었는지 확인
        self.assertEqual(mock_bridge.publish.call_count, 2)

    def test_gem_reply_005_need_more_context_expanded_retry(self):
        """GEM-REPLY-005: NEED_MORE_CONTEXT 발생 시 expanded context로 해당 ID 1회 retry"""
        mock_bridge = MagicMock()
        res1 = GeminiResult(
            request_id="req1", post_key="p1", navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text='[{"comment_no": "1", "status": "NEED_MORE_CONTEXT", "reply": ""}]'
        )
        res2 = GeminiResult(
            request_id="req2", post_key="p2", navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text='[{"comment_no": "1", "status": "REPLY", "reply": "확장 본문 확인 후 답글"}]'
        )
        mock_bridge.wait_for_result.side_effect = [res1, res2]

        targets = [{"comment_no": "1", "nickname": "질문자", "text": "주차 몇 대 되나요?"}]
        res = MyBlogReplyService.generate_replies(
            gemini_bridge=mock_bridge,
            post_title="제목",
            post_excerpt="기본본문",
            target_comments=targets,
            expanded_post_excerpt="주차장은 지하 2층까지 50대 무료 주차 가능합니다."
        )
        self.assertEqual(res["1"]["status"], "REPLY")
        self.assertEqual(res["1"]["reply"], "확장 본문 확인 후 답글")

    # ==================== THREAD-001 ~ 008 (수집 및 감사 판정 테스트) ====================
    @patch("services.my_blog_comment_thread_collector.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    def test_thread_001_002_hierarchy_and_author_reply(self, mock_open):
        """THREAD-001 & 002: 계층형 구조 및 작성자 기존 답글 매핑"""
        page_mock = MagicMock()
        page_mock.url = "https://m.blog.naver.com/myblog/123"
        page_mock.locator.return_value.first.count.return_value = 0
        page_mock.locator.return_value.first.is_visible.return_value = False

        # evaluate mock
        page_mock.evaluate.side_effect = [
            {"10", "11"},  # fingerprint
            {
                "displayedCount": 2,
                "comments": [
                    {
                        "commentNo": "10", "parentCommentNo": None, "isReply": False,
                        "nickname": "독자", "text": "좋은 글!", "blogId": "reader",
                        "date": "10분 전", "isMine": False, "isAuthor": False,
                        "isSecret": False, "isDeleted": False
                    },
                    {
                        "commentNo": "11", "parentCommentNo": "10", "isReply": True,
                        "nickname": "블로거", "text": "감사합니다!", "blogId": "myblog",
                        "date": "5분 전", "isMine": True, "isAuthor": True,
                        "isSecret": False, "isDeleted": False
                    }
                ]
            }
        ]

        result = MyBlogCommentThreadCollector.collect_threads(
            page=page_mock, blog_id="myblog", log_no="123"
        )
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.roots), 1)
        root = result.roots[0]
        self.assertEqual(root.comment_no, "10")
        self.assertEqual(root.existing_author_reply, "감사합니다!")
        self.assertFalse(root.is_eligible_for_auto_reply)  # 이미 답글 완료

    @patch("services.my_blog_comment_thread_collector.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    def test_thread_003_missing_comment_no_is_partial(self, mock_open):
        """THREAD-003: commentNo 미확보 시 unresolved_identity 및 PARTIAL 판정"""
        page_mock = MagicMock()
        page_mock.url = "https://m.blog.naver.com/myblog/123"
        page_mock.locator.return_value.first.count.return_value = 0
        page_mock.locator.return_value.first.is_visible.return_value = False

        page_mock.evaluate.side_effect = [
            set(),
            {
                "displayedCount": 1,
                "comments": [
                    {
                        "commentNo": None, "parentCommentNo": None, "isReply": False,
                        "nickname": "독자", "text": "식별 불가 댓글", "domIndex": 0
                    }
                ]
            }
        ]

        result = MyBlogCommentThreadCollector.collect_threads(
            page=page_mock, blog_id="myblog", log_no="123"
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.unresolved_identity_count, 1)
        self.assertFalse(result.roots[0].is_eligible_for_auto_reply)

    @patch("services.my_blog_comment_thread_collector.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    def test_thread_005_more_button_remains_is_partial(self, mock_open):
        """THREAD-005: 더보기 버튼이 남아있으면 PARTIAL 판정"""
        page_mock = MagicMock()
        page_mock.url = "https://m.blog.naver.com/myblog/123"
        # more_btn is visible
        page_mock.locator.return_value.first.count.return_value = 1
        page_mock.locator.return_value.first.is_visible.return_value = True

        page_mock.evaluate.side_effect = [
            {"1"},  # initial known_ids
            {"1", "2"},  # new_ids after more click
            {"displayedCount": 10, "comments": [{"commentNo": "1", "text": "글", "isReply": False}]}
        ]

        result = MyBlogCommentThreadCollector.collect_threads(
            page=page_mock, blog_id="myblog", log_no="123", max_more_clicks=1
        )
        self.assertEqual(result.status, "partial")

    @patch("services.my_blog_comment_thread_collector.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    def test_thread_006_expected_gt_zero_loaded_zero_is_failed(self, mock_open):
        """THREAD-006: expected > 0인데 loaded == 0이면 FAILED 판정"""
        page_mock = MagicMock()
        page_mock.url = "https://m.blog.naver.com/myblog/123"
        page_mock.locator.return_value.first.count.return_value = 0
        page_mock.locator.return_value.first.is_visible.return_value = False

        page_mock.evaluate.side_effect = [
            set(),
            {"displayedCount": 5, "comments": []}
        ]

        result = MyBlogCommentThreadCollector.collect_threads(
            page=page_mock, blog_id="myblog", log_no="123", expected_comment_count=5
        )
        self.assertEqual(result.status, "failed")

    # ==================== UI-REPLY-001 (최근 글 서비스 테스트) ====================
    def test_ui_reply_001_recent_posts_api(self):
        """UI-REPLY-001: MyBlogRecentPostService.fetch_recent_posts_api 호출 무결성"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"postList": [{"logNo": "123", "title": "%ED%85%8C%EC%8A%A4%ED%8A%B8", "commentCount": 3, "addDate": "2026.09.03."}]}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            posts = MyBlogRecentPostService.fetch_recent_posts_api("test_blog", max_count=10)
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0]["log_no"], "123")
            self.assertEqual(posts[0]["title"], "테스트")
            self.assertEqual(posts[0]["comment_count"], 3)


if __name__ == "__main__":
    unittest.main()
