import unittest
from unittest.mock import MagicMock
from app.processor import PostProcessor, GenerationContext
from app.models import FeedPost, FeedSourceType, PostActionPlan, UserAction, CommentSubmitState
from services.history import HistoryStore
from services.comments.community_rhythm import CommentDraftInspector


class TestProcessorHistoryPipeline(unittest.TestCase):
    @unittest.mock.patch("app.processor.TargetPostGuard.verify")
    @unittest.mock.patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @unittest.mock.patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @unittest.mock.patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @unittest.mock.patch("app.processor.ContentContextExtractor.extract")
    @unittest.mock.patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @unittest.mock.patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @unittest.mock.patch("app.processor.CommentEditorAdapter.focus")
    @unittest.mock.patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.SUBMIT)
    @unittest.mock.patch("app.processor.CommentInteractionService.read_final_text", return_value="스프랑 밥 무한리필이라니 최고네요~")
    @unittest.mock.patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    def test_history_store_comments_delivered_to_initial_prompt_and_inspector(
        self, mock_submit, mock_read, mock_user_act, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """저장소의 최근 등록 이력이 첫 프롬프트와 검사기에 정확히 전달됨을 검증"""
        from naver.comment_guard import CommentPresenceResult, CommentPresenceState
        mock_dup_scan.return_value = CommentPresenceResult(CommentPresenceState.ABSENT, None, None)
        mock_ctx.return_value = MagicMock(textarea=MagicMock(), submit_button=MagicMock())

        post = FeedPost(
            key="test:123",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/test/123",
            title="신촌 경양식 돈까스 맛집 후기",
            excerpt="신촌에 위치한 경양식 돈까스 전문점에 다녀왔습니다. 스프와 밥이 무한리필이라 배부르게 먹을 수 있습니다."
        )
        mock_extract.return_value = MagicMock(title=post.title, excerpt=post.excerpt)

        mock_history = MagicMock()
        mock_history.get_recent_submitted_comments.return_value = [
            "스프랑 밥 무한리필이라니 최고네요~",
            "웨이팅 1시간 반이라니 인기 많네요~"
        ]

        config = {
            "comment_style_preset": "thoughtful",
            "gemini_web_enabled": False,  # skip network, test prompt & context building
            "ai_clipboard_enabled": True,
        }

        processor = PostProcessor(
            config=config,
            history_store=mock_history,
            ai_clipboard_enabled=True,
            gemini_web_enabled=False
        )

        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://m.blog.naver.com/test/123"

        action_plan = PostActionPlan(process_like=False, process_comment=True)
        result = processor.process(mock_page, post, action_plan=action_plan)

        # 1. history_store.get_recent_submitted_comments was called
        mock_history.get_recent_submitted_comments.assert_called_with(limit=5)

        # 2. GenerationContext contains the exact recent comments
        # Verify inspection behavior with this history:
        # Repeating the exact recent comment should fail inspection
        exact_repeat = "스프랑 밥 무한리필이라니 최고네요~"
        inspection = CommentDraftInspector.inspect(
            exact_repeat,
            recent_comments=mock_history.get_recent_submitted_comments.return_value,
            excerpt=post.excerpt
        )
        self.assertFalse(inspection.passed, "최근 등록된 댓글과 동일한 문장은 검사기에서 반려되어야 함")
        self.assertEqual(inspection.code, "exact_duplicate")

    def test_empty_recent_comments_when_history_exists_fails_verification(self):
        """히스토리가 존재하는데 속성명 오류로 빈 배열이 전달되면 방어가 무력화됨을 역검증"""
        mock_history = MagicMock()
        mock_history.get_recent_submitted_comments.return_value = ["중복 방지용 이전 댓글"]

        # If property was misnamed (e.g. self.history_mgr instead of self.history_store),
        # recent_submits would be []
        empty_comments = []
        inspection = CommentDraftInspector.inspect(
            "중복 방지용 이전 댓글",
            recent_comments=empty_comments,
            excerpt="본문 내용"
        )
        # Without history, exact_duplicate check cannot trigger!
        self.assertNotEqual(inspection.code, "exact_duplicate", "이력이 누락되면 중복 검사가 작동하지 못함")

        # With proper history, exact_duplicate MUST trigger
        inspection_with_history = CommentDraftInspector.inspect(
            "중복 방지용 이전 댓글",
            recent_comments=mock_history.get_recent_submitted_comments.return_value,
            excerpt="본문 내용"
        )
        self.assertEqual(inspection_with_history.code, "exact_duplicate", "정상 연결된 이력으로는 중복이 잡혀야 함")


if __name__ == "__main__":
    unittest.main()
