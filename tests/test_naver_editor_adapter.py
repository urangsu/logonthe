import unittest
from unittest.mock import MagicMock, patch
from naver.editor_adapter import CommentEditorAdapter


class TestNaverEditorAdapter(unittest.TestCase):
    def setUp(self):
        self.mock_page = MagicMock()
        self.mock_frame = MagicMock()
        self.mock_frame.name = "comment_iframe"
        self.mock_frame.url = "https://m.blog.naver.com/comment"

        self.mock_editor = MagicMock()
        self.mock_editor.count.return_value = 1
        self.mock_editor.is_visible.return_value = True
        self.mock_editor.evaluate.side_effect = lambda script, *args: "div"

        self.mock_submit_button = MagicMock()
        self.mock_submit_button.is_disabled.return_value = False

        self.editor_context = {
            "frame": self.mock_frame,
            "editor": self.mock_editor,
            "selector": "#naverComment__write_textarea",
            "frame_name": "comment_iframe",
            "frame_url": "https://m.blog.naver.com/comment"
        }

        self.submit_context = {
            "frame": self.mock_frame,
            "button": self.mock_submit_button,
            "selector": "button.u_cbox_btn_upload",
            "frame_name": "comment_iframe",
            "frame_url": "https://m.blog.naver.com/comment"
        }

    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_placeholder_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context")
    def test_nav_editor_001_overlay_placeholder_focus_fill_succeeds_without_click_intercept(
        self, mock_get_editor_ctx, mock_get_placeholder_ctx, mock_get_submit_ctx
    ):
        """NAV-EDITOR-001: .u_cbox_guide placeholder overlay가 존재해도 focus()->fill()로 마우스 가로채기 없이 1차 성공"""
        mock_get_editor_ctx.return_value = self.editor_context
        mock_get_submit_ctx.return_value = self.submit_context

        # Overlay placeholder is visible
        mock_placeholder = MagicMock()
        mock_placeholder.is_visible.return_value = True
        mock_get_placeholder_ctx.return_value = {
            "frame": self.mock_frame,
            "placeholder": mock_placeholder,
            "selector": ".u_cbox_guide[data-action='write#placeholder']"
        }

        # If editor.click() were called, it would fail due to pointer event interception.
        # But focus() and fill() succeed:
        self.mock_editor.inner_text.return_value = "명사십리 해수욕장 앞이라 주차 자리 넉넉해서 편하겠어요"

        ok = CommentEditorAdapter.set_text(self.mock_page, "명사십리 해수욕장 앞이라 주차 자리 넉넉해서 편하겠어요")

        self.assertTrue(ok)
        self.mock_editor.focus.assert_called_once()
        self.mock_editor.fill.assert_called_once_with("명사십리 해수욕장 앞이라 주차 자리 넉넉해서 편하겠어요")
        # editor.click was NOT called in Path A
        self.mock_editor.click.assert_not_called()

    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_placeholder_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context")
    def test_nav_editor_002_path_b_placeholder_fallback_on_path_a_failure(
        self, mock_get_editor_ctx, mock_get_placeholder_ctx, mock_get_submit_ctx
    ):
        """NAV-EDITOR-002: Path A fill 실패 시 Path B placeholder click 후 focus/fill 재시도 성공"""
        mock_get_editor_ctx.return_value = self.editor_context
        mock_get_submit_ctx.return_value = self.submit_context

        mock_placeholder = MagicMock()
        mock_placeholder.is_visible.return_value = True
        mock_get_placeholder_ctx.return_value = {
            "frame": self.mock_frame,
            "placeholder": mock_placeholder,
            "selector": ".u_cbox_guide[data-action='write#placeholder']"
        }

        # Path A fill raises Exception, Path B fill succeeds
        self.mock_editor.fill.side_effect = [Exception("Element detached or covered"), None]
        self.mock_editor.inner_text.return_value = "덮밥 비쥬얼이 참 좋네요"

        ok = CommentEditorAdapter.set_text(self.mock_page, "덮밥 비쥬얼이 참 좋네요")

        self.assertTrue(ok)
        mock_placeholder.click.assert_called_once()
        self.assertEqual(self.mock_editor.focus.call_count, 2)
        self.assertEqual(self.mock_editor.fill.call_count, 2)

    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_placeholder_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context")
    def test_nav_editor_003_path_c_exec_command_fallback_when_fill_fails_state(
        self, mock_get_editor_ctx, mock_get_placeholder_ctx, mock_get_submit_ctx
    ):
        """NAV-EDITOR-003: Path A, B 모두 실패 시 Path C execCommand insertText 및 input dispatch로 성공"""
        mock_get_editor_ctx.return_value = self.editor_context
        mock_get_submit_ctx.return_value = self.submit_context
        mock_get_placeholder_ctx.return_value = None

        # Path A fill raises Exception
        self.mock_editor.fill.side_effect = Exception("Fill error")
        # Path C evaluate succeeds and readback returns text
        self.mock_editor.inner_text.return_value = "제주 여행 코스로 딱이네요~"

        ok = CommentEditorAdapter.set_text(self.mock_page, "제주 여행 코스로 딱이네요~")

        self.assertTrue(ok)
        self.mock_editor.evaluate.assert_called()

    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_placeholder_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context")
    def test_nav_editor_004_fails_when_submit_button_remains_disabled(
        self, mock_get_editor_ctx, mock_get_placeholder_ctx, mock_get_submit_ctx
    ):
        """NAV-EDITOR-004: 텍스트는 들어갔으나 등록 버튼이 disabled 상태면 실패 반환 (framework state 미갱신 방어)"""
        mock_get_editor_ctx.return_value = self.editor_context
        mock_get_placeholder_ctx.return_value = None

        self.mock_submit_button.is_disabled.return_value = True
        mock_get_submit_ctx.return_value = self.submit_context
        self.mock_editor.inner_text.return_value = "파스타 소스가 참 맛있어 보여요"

        ok = CommentEditorAdapter.set_text(self.mock_page, "파스타 소스가 참 맛있어 보여요")

        self.assertFalse(ok)

    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_placeholder_context")
    @patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context")
    def test_nav_editor_005_fails_on_readback_mismatch(
        self, mock_get_editor_ctx, mock_get_placeholder_ctx, mock_get_submit_ctx
    ):
        """NAV-EDITOR-005: Read-back 텍스트가 기대값과 다르면 실패 반환"""
        mock_get_editor_ctx.return_value = self.editor_context
        mock_get_placeholder_ctx.return_value = None
        mock_get_submit_ctx.return_value = self.submit_context

        self.mock_editor.inner_text.return_value = "엉뚱한 텍스트"

        ok = CommentEditorAdapter.set_text(self.mock_page, "원래 입력하려던 텍스트")

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
