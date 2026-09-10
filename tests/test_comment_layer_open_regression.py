import unittest
from unittest.mock import MagicMock, patch
from naver.resolver import MobileDOMResolver, CommentOpenContext, CommentDOMContext
from naver.interaction import CommentInteractionService


class TestCommentLayerOpenRegression(unittest.TestCase):
    """
    P0 Regression Test Matrix: OPEN-001 ~ OPEN-010
    Verifies robust comment layer opening, multi-frame context discovery,
    candidate inventory prioritization, and distinct failure codes.
    """

    def setUp(self):
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False
        self.mock_page.frames = [self.mock_page]
        self.mock_page.url = "https://m.blog.naver.com/test/12345"

    # -------------------------------------------------------------------------
    # OPEN-001: First selector candidate hidden, second candidate visible
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible")
    def test_open_001_first_selector_hidden_second_selector_visible_clicks_visible_and_ready(self, mock_editor_vis):
        """OPEN-001: 첫 번째 selector 후보 hidden, 두 번째 후보 visible -> visible 후보 클릭 -> READY"""
        # Editor not open initially, but becomes open after click
        mock_editor_vis.side_effect = [False, True, True]

        hidden_btn = MagicMock()
        hidden_btn.count.return_value = 1
        hidden_btn.is_visible.return_value = False
        hidden_btn.is_enabled.return_value = True

        visible_btn = MagicMock()
        visible_btn.count.return_value = 1
        visible_btn.is_visible.return_value = True
        visible_btn.is_enabled.return_value = True
        visible_btn.bounding_box.return_value = {"x": 10, "y": 500, "width": 60, "height": 40}
        visible_btn.inner_text.return_value = "댓글 5"
        visible_btn.get_attribute.return_value = "댓글 열기"

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = hidden_btn
                return mock_loc
            elif selector == "button.Interact__comment_btn--Wbuoq":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = visible_btn
                return mock_loc
            else:
                mock_loc.count.return_value = 0
                mock_loc.first.count.return_value = 0
                return mock_loc

        self.mock_page.locator.side_effect = locator_router

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertTrue(success)
        self.assertEqual(reason, "ready")
        hidden_btn.click.assert_not_called()
        visible_btn.click.assert_called_once()

    # -------------------------------------------------------------------------
    # OPEN-002: Same selector nth(0) hidden / nth(1) visible
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible")
    def test_open_002_same_selector_nth_0_hidden_nth_1_visible_selects_nth_1(self, mock_editor_vis):
        """OPEN-002: 같은 selector에서 nth(0) hidden / nth(1) visible -> nth(1) 선택"""
        mock_editor_vis.side_effect = [False, True, True]

        btn_nth0 = MagicMock()
        btn_nth0.is_visible.return_value = False
        btn_nth0.is_enabled.return_value = True

        btn_nth1 = MagicMock()
        btn_nth1.is_visible.return_value = True
        btn_nth1.is_enabled.return_value = True
        btn_nth1.bounding_box.return_value = {"x": 20, "y": 600, "width": 50, "height": 30}
        btn_nth1.inner_text.return_value = "댓글 12"

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 2
                mock_loc.nth.side_effect = lambda idx: btn_nth0 if idx == 0 else btn_nth1
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        self.mock_page.locator.side_effect = locator_router

        # Verify context resolver chooses index 1
        ctx = MobileDOMResolver.get_comment_open_context(self.mock_page)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.index, 1)
        self.assertEqual(ctx.button, btn_nth1)

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)
        self.assertTrue(success)
        self.assertEqual(reason, "ready")
        btn_nth0.click.assert_not_called()
        btn_nth1.click.assert_called_once()

    # -------------------------------------------------------------------------
    # OPEN-003: Comment opener in child frame
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible")
    def test_open_003_comment_opener_in_child_frame_acquires_frame_context_and_ready(self, mock_editor_vis):
        """OPEN-003: comment opener가 child frame에 존재 -> frame context 획득 -> READY"""
        mock_editor_vis.side_effect = [False, True, True]

        main_frame = MagicMock()
        main_frame.url = "https://m.blog.naver.com/test/12345"
        main_frame.name = ""

        # Main frame has no opener
        main_loc = MagicMock()
        main_loc.count.return_value = 0
        main_loc.first.count.return_value = 0
        main_frame.locator.return_value = main_loc

        # Child frame has visible opener
        child_frame = MagicMock()
        child_frame.url = "https://m.blog.naver.com/comment_frame"
        child_frame.name = "comment_iframe"

        child_btn = MagicMock()
        child_btn.is_visible.return_value = True
        child_btn.is_enabled.return_value = True
        child_btn.bounding_box.return_value = {"x": 5, "y": 100, "width": 80, "height": 35}

        def child_locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = child_btn
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        child_frame.locator.side_effect = child_locator_router
        self.mock_page.frames = [main_frame, child_frame]

        ctx = MobileDOMResolver.get_comment_open_context(self.mock_page)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.frame, child_frame)
        self.assertEqual(ctx.button, child_btn)

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)
        self.assertTrue(success)
        self.assertEqual(reason, "ready")
        child_btn.click.assert_called_once()

    # -------------------------------------------------------------------------
    # OPEN-004: Editor already open/visible
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible", return_value=True)
    def test_open_004_editor_already_open_ready_without_clicking_opener(self, mock_editor_vis):
        """OPEN-004: editor가 이미 열린 상태 -> opener 클릭 없이 READY"""
        mock_btn = MagicMock()
        mock_btn.count.return_value = 1
        mock_btn.is_visible.return_value = True
        self.mock_page.locator.return_value.count.return_value = 1
        self.mock_page.locator.return_value.nth.return_value = mock_btn

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertTrue(success)
        self.assertEqual(reason, "ready")
        # Opener button must NOT be clicked when editor is already open!
        mock_btn.click.assert_not_called()

    # -------------------------------------------------------------------------
    # OPEN-005: DOM replacement during click
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible")
    def test_open_005_click_dom_replacement_re_resolves_and_succeeds(self, mock_editor_vis):
        """OPEN-005: 클릭 도중 DOM replacement -> re-resolve 1회 -> READY"""
        mock_editor_vis.side_effect = [False, False, True, True]

        stale_btn = MagicMock()
        stale_btn.is_visible.return_value = True
        stale_btn.is_enabled.return_value = True
        stale_btn.bounding_box.return_value = {"x": 10, "y": 200, "width": 60, "height": 30}
        stale_btn.click.side_effect = Exception("Target element is detached from document")

        fresh_btn = MagicMock()
        fresh_btn.is_visible.return_value = True
        fresh_btn.is_enabled.return_value = True
        fresh_btn.bounding_box.return_value = {"x": 10, "y": 200, "width": 60, "height": 30}

        calls = [0]

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                calls[0] += 1
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = stale_btn if calls[0] <= 1 else fresh_btn
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        self.mock_page.locator.side_effect = locator_router

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertTrue(success)
        self.assertEqual(reason, "ready")
        fresh_btn.click.assert_called_once()

    # -------------------------------------------------------------------------
    # OPEN-006: Editor lazy renders after click
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible")
    def test_open_006_editor_lazy_renders_after_click_ready_before_timeout(self, mock_editor_vis):
        """OPEN-006: 버튼 클릭 성공 후 editor lazy render 2회 폴링 후 등장 -> timeout 전 READY"""
        # Initially False, then after 2 polling cycles becomes True
        mock_editor_vis.side_effect = [False, False, False, True]

        btn = MagicMock()
        btn.is_visible.return_value = True
        btn.is_enabled.return_value = True
        btn.bounding_box.return_value = {"x": 10, "y": 300, "width": 50, "height": 30}

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = btn
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        self.mock_page.locator.side_effect = locator_router

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertTrue(success)
        self.assertEqual(reason, "ready")
        btn.click.assert_called_once()

    # -------------------------------------------------------------------------
    # OPEN-007: Login required
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible", return_value=False)
    def test_open_007_login_required_detected(self, mock_editor_vis):
        """OPEN-007: login-required -> comment_login_required"""
        btn = MagicMock()
        btn.is_visible.return_value = True
        btn.is_enabled.return_value = True
        btn.bounding_box.return_value = {"x": 10, "y": 300, "width": 50, "height": 30}

        login_box = MagicMock()
        login_box.count.return_value = 1
        login_box.is_visible.return_value = True
        login_box.inner_text.return_value = "로그인 후 댓글을 작성할 수 있습니다."

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = btn
                return mock_loc
            elif ".u_cbox_type_logged_out" in selector or "로그인" in selector:
                mock_loc.count.return_value = 1
                mock_loc.first = login_box
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        self.mock_page.locator.side_effect = locator_router

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertFalse(success)
        self.assertEqual(reason, "comment_login_required")

    # -------------------------------------------------------------------------
    # OPEN-008: Comments disabled
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible", return_value=False)
    def test_open_008_comments_disabled_detected(self, mock_editor_vis):
        """OPEN-008: comments disabled -> comment_disabled"""
        btn = MagicMock()
        btn.is_visible.return_value = True
        btn.is_enabled.return_value = True
        btn.bounding_box.return_value = {"x": 10, "y": 300, "width": 50, "height": 30}

        disabled_box = MagicMock()
        disabled_box.count.return_value = 1
        disabled_box.is_visible.return_value = True
        disabled_box.inner_text.return_value = "댓글을 작성할 수 없습니다."

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = btn
                return mock_loc
            elif ".u_cbox_none" in selector or "비활성화" in selector or "댓글을 작성할 수 없습니다" in selector:
                mock_loc.count.return_value = 1
                mock_loc.first = disabled_box
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        self.mock_page.locator.side_effect = locator_router

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertFalse(success)
        self.assertEqual(reason, "comment_disabled")

    # -------------------------------------------------------------------------
    # OPEN-009: Candidate 0 -> comment_open_button_not_found
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible", return_value=False)
    def test_open_009_candidate_zero_returns_comment_open_button_not_found(self, mock_editor_vis):
        """OPEN-009: candidate 0 -> comment_open_button_not_found -> generic timeout 금지"""
        mock_loc = MagicMock()
        mock_loc.count.return_value = 0
        mock_loc.first.count.return_value = 0
        self.mock_page.locator.return_value = mock_loc

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertFalse(success)
        self.assertEqual(reason, "comment_open_button_not_found")

    # -------------------------------------------------------------------------
    # OPEN-010: Candidate visible but click exception
    # -------------------------------------------------------------------------
    @patch("naver.interaction.CommentEditorAdapter.is_visible", return_value=False)
    def test_open_010_candidate_visible_click_exception_returns_comment_open_click_failed(self, mock_editor_vis):
        """OPEN-010: candidate visible but click exception -> comment_open_click_failed -> exception evidence 보존"""
        btn = MagicMock()
        btn.is_visible.return_value = True
        btn.is_enabled.return_value = True
        btn.bounding_box.return_value = {"x": 10, "y": 300, "width": 50, "height": 30}
        btn.click.side_effect = Exception("Pointer intercept: element blocked by modal overlay")

        def locator_router(selector):
            mock_loc = MagicMock()
            if selector == "button[data-click-area='pst.re']":
                mock_loc.count.return_value = 1
                mock_loc.nth.return_value = btn
                return mock_loc
            mock_loc.count.return_value = 0
            mock_loc.first.count.return_value = 0
            return mock_loc

        self.mock_page.locator.side_effect = locator_router

        success, reason = CommentInteractionService.open_comment_layer(self.mock_page)

        self.assertFalse(success)
        self.assertEqual(reason, "comment_open_click_failed")


if __name__ == "__main__":
    unittest.main()
