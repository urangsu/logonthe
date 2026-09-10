import unittest
from unittest.mock import MagicMock, patch
import threading
import time

from playwright.sync_api import Page
from app.models import (
    FeedPost, PostProcessResult, LikeProcessResult, CommentProcessResult,
    UserAction, CommentSubmitState, LikeState, PostActionPlan,
    SubmitOrigin, CommentSubmitOutcome
)
from naver.interaction import CommentInteractionService
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from services.like_transaction import LikeConfidence
from app.processor import PostProcessor


class TestEnterManualSubmitRegression(unittest.TestCase):
    def setUp(self):
        self.mock_page = MagicMock(spec=Page)
        self.mock_page.is_closed.return_value = False
        self.post = FeedPost(
            key="test_user:12345",
            source="recommendation",
            url="https://m.blog.naver.com/test_user/12345",
            title="파주 장어 맛집 갈릴리농원",
            excerpt="파주에서 유명한 장어 맛집 갈릴리농원 다녀왔어요."
        )
        self.config = {
            "comment_enabled": True,
            "auto_comment_submit": False,
            "auto_comment_delay_min": 5.0,
            "auto_comment_delay_max": 6.0,
            "comment_style_preset": "community",
        }

    def test_enter_001_draft_as_is_enter_submits_with_one_python_click(self):
        """ENTER-001: 초안 그대로 Enter → USER_ENTER → Python click 1회 → SUBMITTED"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        eval_scripts = []
        def mock_eval(script, *args):
            eval_scripts.append(script)
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": "장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~"}
            return None

        self.mock_page.evaluate.side_effect = mock_eval
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                "장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            self.assertEqual(btn_mock.click.call_count, 1)

    def test_enter_002_user_edit_dirty_true_enter_submits_edited_text(self):
        """ENTER-002: 사용자 수정 → dirty=true → Enter → 수정본 readback → SUBMITTED"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": True, "text": "사용자가 직접 수정한 아주 훌륭하고 맛있는 내용이네요~"}
            return None

        self.mock_page.evaluate.side_effect = mock_eval
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                "사용자가 직접 수정한 아주 훌륭하고 맛있는 내용이네요~",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            self.assertEqual(btn_mock.click.call_count, 1)

    def test_enter_003_korean_ime_composing_blocks_and_rollbacks_submit_lock(self):
        """ENTER-003: compositionstart/isComposing 중 Enter → pre-click block → lock rollback"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": True, "dirty": True, "text": "장어구이 참 맛있겠네"}
            return None

        self.mock_page.evaluate.side_effect = mock_eval
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                "장어구이 참 맛있겠네요~ 정성스러운 후기 잘 읽고 갑니다!",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome.state, CommentSubmitState.PRECLICK_BLOCKED)
            self.assertTrue(outcome.retryable_same_post)
            btn_mock.click.assert_not_called()

            # Verify lock release can be called safely
            self.mock_page.evaluate.side_effect = lambda s, *a: True
            released = CommentInteractionService.release_submit_lock(self.mock_page, source="user_enter")
            self.assertTrue(released)

    def test_enter_004_auto_timer_boundary_user_enter_wins_over_timer(self):
        """ENTER-004: auto timer 6초, 만료 직전 user Enter → USER_ENTER 우선 처리"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        state = {"calls": 0}
        def mock_eval(script, *args):
            if "return [act, dirty]" in script:
                state["calls"] += 1
                # User pressed enter before timer!
                return ["SUBMIT", True]
            return None

        mock_frame.evaluate.side_effect = mock_eval
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                timeout_seconds=6.0,
            )
            self.assertEqual(action, UserAction.SUBMIT)

    def test_enter_005_user_edit_disarms_auto_timer_without_auto_submit(self):
        """ENTER-005: auto timer 중 user edit → timer disarm → 자동 제출 없음, manual enter 대기"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        state = {"calls": 0}
        stop_event = threading.Event()
        def mock_eval(script, *args):
            if "return [act, dirty]" in script:
                state["calls"] += 1
                if state["calls"] == 1:
                    return [None, True]  # user typing detected!
                if state["calls"] >= 3:
                    stop_event.set()
                return [None, True]
            return None

        mock_frame.evaluate.side_effect = mock_eval
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.05,
            )
            # Must NOT auto-submit; timeout was disarmed, stopped by stop_event
            self.assertEqual(action, UserAction.STOP)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.install_keyboard_listener")
    @patch("app.processor.CommentEditorAdapter.focus")
    def test_enter_006_pre_click_temporary_failure_retains_post_and_retries(
        self, mock_focus, mock_kb, mock_set_text, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """ENTER-006: pre-click failure 후 current post 유지 (same-post loop) → 다음 시도에서 SUBMITTED"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(title="장어 맛집", excerpt="파주 장어 맛집")

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=False,
            auto_comment_submit_enabled=False,
        )

        attempts = {"count": 0}
        def mock_wait_action(*args, **kwargs):
            attempts["count"] += 1
            return UserAction.SUBMIT

        def mock_submit_verify(page, final_text, *args, **kwargs):
            if attempts["count"] == 1:
                # First enter: pre-click blocked due to IME composing or short text
                return CommentSubmitOutcome(state=CommentSubmitState.PRECLICK_BLOCKED, reason="ime_composing", click_dispatched=False, retryable_same_post=True)
            # Second enter: successful submission!
            return CommentSubmitOutcome(state=CommentSubmitState.SUBMITTED, reason="server_verified", click_dispatched=True, retryable_same_post=False)

        with patch("app.processor.CommentInteractionService.wait_for_user_action", side_effect=mock_wait_action), \
             patch("app.processor.CommentInteractionService.read_final_text", return_value="장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~"), \
             patch("app.processor.CommentInteractionService.submit_and_verify", side_effect=mock_submit_verify), \
             patch("app.processor.CommentInteractionService.release_submit_lock") as mock_release_lock:
            res = processor.process(self.mock_page, self.post, action_plan=plan)

            self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
            # Verified that lock was released after first failure
            mock_release_lock.assert_called_once()
            self.assertEqual(attempts["count"], 2)

    def test_enter_007_native_click_submits_with_zero_python_clicks(self):
        """ENTER-007: native 등록 버튼 클릭 → NATIVE_CLICK → Python click 0회 → verify only"""
        btn_mock = MagicMock()
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                "장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~",
                origin=SubmitOrigin.NATIVE_CLICK,
            )
            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            # Python must NOT click the button!
            btn_mock.click.assert_not_called()

    def test_enter_008_click_dispatched_server_verify_unknown_no_auto_retry_click(self):
        """ENTER-008: click dispatched 후 서버 확인 불명 → SUBMISSION_UNKNOWN → 자동 재클릭 0회"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": "장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~"}
            return None

        self.mock_page.evaluate.side_effect = mock_eval
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.UNKNOWN, confidence=LikeConfidence.LOW)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                "장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome.state, CommentSubmitState.SUBMISSION_UNKNOWN)
            # Exactly 1 click dispatched, no re-clicks
            self.assertEqual(btn_mock.click.call_count, 1)
