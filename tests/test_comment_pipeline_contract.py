"""
Comment Pipeline Contract Tests (COMMENT-001 ~ COMMENT-005)
Verifies the exact DOM resolver, server duplicate scan, secret comment,
and AI draft gate behavior in PostProcessor without regression.
"""
import unittest
from unittest.mock import MagicMock, patch, call
from app.models import (
    FeedPost, FeedSourceType, CommentSubmitState, LikeState, PostActionPlan, UserAction
)
from app.processor import PostProcessor
from naver.comment_guard import CommentPresenceState, CommentPresenceResult


class TestCommentPipelineContract(unittest.TestCase):
    def setUp(self):
        self.config = {
            "comment_enabled": True,
            "like_enabled": False,
            "secret_comment": False,
            "ai_clipboard_enabled": False,
            "gemini_web_enabled": False,
            "comment_style_preset": "community",
            "comment_template": "좋은 글 잘 보았습니다."
        }
        self.post = FeedPost(
            key="test:12345",
            url="https://m.blog.naver.com/test/12345",
            source=FeedSourceType.NEIGHBOR,
            title="테스트 포스팅 제목",
            blog_id="test",
            log_no="12345"
        )
        self.mock_page = MagicMock()

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="파스타 면발 식감이랑 소스가 진짜 군침도네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    def test_comment_001_open_layer_absent_draft_path(
        self, mock_submit, mock_read, mock_user_act, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """COMMENT-001: open_comment_layer=True -> resolver -> duplicate ABSENT -> draft & submit path"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="테스트 포스팅", excerpt="제주 한림 협재 해수욕장 근처 맛있는 파스타 맛집에 다녀왔습니다. 해물 파스타 짱이에요.")

        processor = PostProcessor(self.config, like_enabled=False, comment_enabled=True, gemini_web_enabled=False)
        res = processor.process(self.mock_page, self.post)

        mock_open.assert_called_once()
        mock_ctx.assert_called_once_with(self.mock_page)
        mock_dup_scan.assert_called_once_with(self.mock_page, stop_event=processor.stop_event)
        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertEqual(res.comment_result.submitted_text, "파스타 면발 식감이랑 소스가 진짜 군침도네요~")

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="텐동 튀김 비쥬얼 진짜 예술이네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    def test_comment_002_child_frame_resolver_passed_to_duplicate_guard(
        self, mock_submit, mock_read, mock_user_act, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """COMMENT-002: child frame resolver -> duplicate guard receives child frame"""
        child_frame = MagicMock()
        mock_ctx.return_value = {"frame": child_frame, "root": child_frame}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="테스트 포스팅", excerpt="광안리 해수욕장 근처 텐동 맛집 방문 후기입니다.")

        processor = PostProcessor(self.config, like_enabled=False, comment_enabled=True, gemini_web_enabled=False)
        res = processor.process(self.mock_page, self.post)

        mock_dup_scan.assert_called_once_with(child_frame, stop_event=processor.stop_event)
        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.MobileDOMResolver.get_secret_comment_checkbox")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.SUBMIT)
    @patch("app.processor.CommentInteractionService.read_final_text", return_value="올레시장 맛있는거 진짜 많죠 넘 좋네요~")
    @patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED)
    def test_comment_003_secret_comment_runs_get_secret_checkbox(
        self, mock_submit, mock_read, mock_user_act, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_secret_cb, mock_ctx, mock_open, mock_guard
    ):
        """COMMENT-003: secret_comment=True -> get_secret_comment_checkbox 실행"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="테스트 포스팅", excerpt="제주 서귀포 올레시장 맛집 다녀온 솔직 후기입니다.")
        checkbox_elem = MagicMock()
        checkbox_elem.count.return_value = 1
        mock_secret_cb.return_value = checkbox_elem

        processor = PostProcessor(self.config, like_enabled=False, comment_enabled=True, secret_comment=True, gemini_web_enabled=False)
        res = processor.process(self.mock_page, self.post)

        mock_secret_cb.assert_called_once_with(self.mock_page)
        checkbox_elem.click.assert_called_once()
        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.CommentInteractionService.wait_for_user_action")
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    def test_comment_004_duplicate_present_zero_gemini_zero_editor_submitted(
        self, mock_submit, mock_user_act, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """COMMENT-004: duplicate=PRESENT -> Gemini 0회 / editor 0회 / SUBMITTED 동기화"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(
            state=CommentPresenceState.PRESENT,
            confidence="high",
            comment_text="기존 등록된 내 댓글입니다."
        )

        mock_gemini_bridge = MagicMock()
        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_gemini_bridge
        )
        res = processor.process(self.mock_page, self.post)

        # AI prompt or interaction should NEVER be called
        mock_gemini_bridge.publish.assert_not_called()
        mock_user_act.assert_not_called()
        mock_submit.assert_not_called()
        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertEqual(res.comment_result.submitted_text, "기존 등록된 내 댓글입니다.")

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.CommentInteractionService.wait_for_user_action")
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    def test_comment_005_duplicate_unknown_zero_gemini_skipped(
        self, mock_submit, mock_user_act, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """COMMENT-005: duplicate=UNKNOWN -> Gemini 0회 / SKIPPED"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.UNKNOWN, confidence="low")

        mock_gemini_bridge = MagicMock()
        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_gemini_bridge
        )
        res = processor.process(self.mock_page, self.post)

        mock_gemini_bridge.publish.assert_not_called()
        mock_user_act.assert_not_called()
        mock_submit.assert_not_called()
        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        self.assertEqual(res.comment_result.error, "server_duplicate_check_unknown")

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    def test_comment_006_skip_to_next_post_command(
        self, mock_submit, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """COMMENT-006: UI 다음 글로 넘어가기(SKIP_POST) 명령 시 에디터 등록 생략 및 SKIPPED 반환"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="테스트 포스팅", excerpt="제주 서귀포 맛집 다녀온 후기입니다.")

        from services.clipboard_bridge import ClipboardCommandBridge
        bridge = ClipboardCommandBridge()
        bridge.send_skip_post()

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            command_bridge=bridge
        )
        res = processor.process(self.mock_page, self.post)

        mock_submit.assert_not_called()
        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)


if __name__ == "__main__":
    unittest.main()
