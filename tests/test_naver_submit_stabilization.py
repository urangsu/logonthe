import unittest
from unittest.mock import MagicMock, patch, call
import threading

from playwright.sync_api import Page
from app.models import (
    CommentSubmitState,
    SubmitOrigin,
    CommentSubmitOutcome,
)
from naver.interaction import CommentInteractionService
from naver.resolver import MobileDOMResolver
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from services.like_transaction import LikeConfidence


class TestNaverSubmitStabilization(unittest.TestCase):
    def setUp(self):
        self.mock_page = MagicMock(spec=Page)
        self.mock_page.is_closed.return_value = False
        self.comment_text = "장어 비주얼도 좋고 숯불구이라 정말 맛있어 보이네요~"

    def test_01_normal_single_click_success_with_permit(self):
        """1. 정상 1회 클릭 성공: attempt=1, timeout=2500, permit 소비됨 -> SUBMITTED"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        eval_calls = []
        def mock_eval(script, *args):
            eval_calls.append((script, args))
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": self.comment_text}
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return True
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            self.assertTrue(outcome.click_dispatched)
            self.assertFalse(outcome.retryable_same_post)
            self.assertEqual(btn_mock.click.call_count, 1)
            btn_mock.click.assert_called_with(timeout=2500)

    def test_02_click_exception_but_permit_consumed_step_a(self):
        """2. Step A: btn.click() 예외이나 permit 소비됨 -> 2회차 클릭 절대 금지 -> 서버 검증 성공"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        btn_mock.click.side_effect = TimeoutError("Timeout 2500ms waiting for element to settle")
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": self.comment_text}
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return True  # Permit was consumed! Event reached button!
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            self.assertTrue(outcome.click_dispatched)
            # Crucial: Exactly 1 click attempt, no 2nd click!
            self.assertEqual(btn_mock.click.call_count, 1)

    def test_03_click_exception_permit_consumed_server_unknown(self):
        """3. Step A: btn.click() 예외 + permit 소비됨 + 서버 확인 불명 -> SUBMISSION_UNKNOWN (재클릭 0회)"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        btn_mock.click.side_effect = RuntimeError("Navigation occurred mid-click")
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": self.comment_text}
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return True
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.UNKNOWN, confidence=LikeConfidence.LOW)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.SUBMISSION_UNKNOWN)
            self.assertTrue(outcome.click_dispatched)
            self.assertFalse(outcome.retryable_same_post)
            self.assertEqual(btn_mock.click.call_count, 1)

    def test_04_step_c_retry_success_with_fresh_locator(self):
        """4. Step C 재시도 성공: 1차 사전 실패(permit 미소비) -> 신규 locator 재해결 -> 2차 성공 -> SUBMITTED"""
        btn1_mock = MagicMock()
        btn1_mock.is_disabled.return_value = False
        btn1_mock.click.side_effect = TimeoutError("Element detached from DOM")

        btn2_mock = MagicMock()
        btn2_mock.is_disabled.return_value = False
        btn2_mock.click.return_value = None  # 2nd click succeeds!

        ctx1 = {
            "button": btn1_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }
        ctx2 = {
            "button": btn2_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        resolve_count = {"count": 0}
        def mock_get_submit_ctx(*args, **kwargs):
            resolve_count["count"] += 1
            if resolve_count["count"] == 1:
                return ctx1
            return ctx2

        attempt_permits = {"permit_consumed": False}
        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {
                    "isComposing": False,
                    "dirty": False,
                    "text": self.comment_text,
                    "visible": True,
                    "lockHeld": True,
                }
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return attempt_permits["permit_consumed"]
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        # In attempt 1 server scan returns ABSENT, after attempt 2 server scan returns PRESENT
        scan_count = {"count": 0}
        def mock_scan(*args, **kwargs):
            scan_count["count"] += 1
            if scan_count["count"] == 1:
                return CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence=LikeConfidence.HIGH)
            return CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", side_effect=mock_get_submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment", side_effect=mock_scan):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            self.assertTrue(outcome.click_dispatched)
            # Re-resolved from DOM:
            self.assertEqual(resolve_count["count"], 2)
            # btn1 clicked once, btn2 clicked once:
            self.assertEqual(btn1_mock.click.call_count, 1)
            self.assertEqual(btn2_mock.click.call_count, 1)

    def test_05_step_c_both_attempts_fail_preclick_blocked(self):
        """5. Step C 재시도 실패: 1차 사전 실패 -> 2차도 사전 실패 -> PRECLICK_BLOCKED, retryable_same_post=True"""
        btn1_mock = MagicMock()
        btn1_mock.is_disabled.return_value = False
        btn1_mock.click.side_effect = TimeoutError("Attempt 1 actionability timeout")

        btn2_mock = MagicMock()
        btn2_mock.is_disabled.return_value = False
        btn2_mock.click.side_effect = TimeoutError("Attempt 2 actionability timeout")

        resolve_count = {"count": 0}
        def mock_get_submit_ctx(*args, **kwargs):
            resolve_count["count"] += 1
            btn = btn1_mock if resolve_count["count"] == 1 else btn2_mock
            return {
                "button": btn,
                "frame": self.mock_page,
                "frame_name": "main",
                "selector": "button.u_cbox_btn_upload",
                "visible": True,
                "enabled": True,
                "score": 1950,
            }

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {
                    "isComposing": False,
                    "dirty": False,
                    "text": self.comment_text,
                    "visible": True,
                    "lockHeld": True,
                }
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return False  # Permit never consumed
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", side_effect=mock_get_submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.PRECLICK_BLOCKED)
            self.assertFalse(outcome.click_dispatched)
            self.assertTrue(outcome.retryable_same_post)
            # Maximum 2 attempts, no 3rd attempt
            self.assertEqual(btn1_mock.click.call_count, 1)
            self.assertEqual(btn2_mock.click.call_count, 1)

    def test_06_step_b_editor_disappeared_forbids_retry(self):
        """6. Step B: 1차 예외 후 에디터 사라짐(visible=False) -> 2회차 클릭 금지 -> 서버 미확인 시 SUBMISSION_UNKNOWN"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        btn_mock.click.side_effect = TimeoutError("Click timed out")
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        call_step = {"count": 0}
        def mock_eval(script, *args):
            call_step["count"] += 1
            if "dirty" in script or "currentText" in script:
                if call_step["count"] <= 2:
                    return {"isComposing": False, "dirty": False, "text": self.comment_text, "visible": True, "lockHeld": True}
                # After click: editor disappeared!
                return {"isComposing": False, "dirty": False, "text": "", "visible": False, "lockHeld": True}
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return False
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.UNKNOWN, confidence=LikeConfidence.LOW)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            # Strictly 1 click, no retry because editor disappeared (Step B)
            self.assertEqual(btn_mock.click.call_count, 1)
            self.assertEqual(outcome.state, CommentSubmitState.SUBMISSION_UNKNOWN)
            self.assertFalse(outcome.retryable_same_post)

    def test_07_step_b_server_comment_present_immediately_submitted(self):
        """7. Step B: 1차 예외 후 서버 스캔에 이미 본인 댓글 발견 -> 2회차 클릭 금지 -> 즉시 SUBMITTED"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        btn_mock.click.side_effect = RuntimeError("DOM error")
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": self.comment_text, "visible": True, "lockHeld": True}
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return False
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            # Exactly 1 click attempt, no 2nd click!
            self.assertEqual(btn_mock.click.call_count, 1)
            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            self.assertTrue(outcome.click_dispatched)

    def test_08_native_click_dispatches_zero_python_clicks(self):
        """8. NATIVE_CLICK 규약: Python 클릭 0회, 서버 검증만 수행 -> SUBMITTED"""
        btn_mock = MagicMock()
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.NATIVE_CLICK,
            )

            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            btn_mock.click.assert_not_called()
            self.assertFalse(outcome.click_dispatched)

    def test_09_candidate_inventory_priority_scoring(self):
        """9. 후보 인벤토리 우선순위 스코어링: sameFrame, visible, enabled, specific selector 우선"""
        mock_frame1 = MagicMock()
        mock_frame1.name = "editor_frame"
        mock_frame1.url = "https://m.blog.naver.com/post/1"

        mock_frame2 = MagicMock()
        mock_frame2.name = "other_frame"
        mock_frame2.url = "https://m.blog.naver.com/other"

        self.mock_page.frames = [mock_frame1, mock_frame2]

        # In frame1: specific button that is visible
        loc_specific = MagicMock()
        loc_specific.count.return_value = 1
        loc_specific.nth.return_value = loc_specific
        loc_specific.is_visible.return_value = True
        loc_specific.is_enabled.return_value = True
        loc_specific.bounding_box.return_value = {"x": 10, "y": 10, "width": 60, "height": 30}
        loc_specific.inner_text.return_value = "등록"
        loc_specific.get_attribute.return_value = "등록"

        # In frame2: generic fallback button
        loc_generic = MagicMock()
        loc_generic.count.return_value = 1
        loc_generic.nth.return_value = loc_generic
        loc_generic.is_visible.return_value = True
        loc_generic.is_enabled.return_value = True
        loc_generic.bounding_box.return_value = {"x": 10, "y": 10, "width": 60, "height": 30}
        loc_generic.inner_text.return_value = "등록"
        loc_generic.get_attribute.return_value = "등록"

        def frame1_loc(sel):
            if "u_cbox_btn_upload" in sel:
                return loc_specific
            mock_empty = MagicMock()
            mock_empty.count.return_value = 0
            return mock_empty

        def frame2_loc(sel):
            if ":has-text" in sel:
                return loc_generic
            mock_empty = MagicMock()
            mock_empty.count.return_value = 0
            return mock_empty

        mock_frame1.locator.side_effect = frame1_loc
        mock_frame2.locator.side_effect = frame2_loc

        res = MobileDOMResolver.get_comment_submit_context(self.mock_page, preferred_frame=mock_frame1)

        self.assertIsNotNone(res)
        self.assertEqual(res["frame"], mock_frame1)
        self.assertTrue(res["visible"])
        self.assertTrue(res["enabled"])
        self.assertTrue(res["editorSameFrame"])
        # Score for same frame(1000) + visible(500) + enabled(250) + bbox(200) + specific(150) + exact_text(100) = 2200
        self.assertEqual(res["score"], 2200)

    def test_10_button_disabled_returns_preclick_blocked(self):
        """10. 등록 버튼 비활성화 상태: click_dispatched=False, retryable_same_post=True, PRECLICK_BLOCKED"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = True
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": False,
            "score": 1000,
        }

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": self.comment_text, "visible": True, "lockHeld": True}
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.PRECLICK_BLOCKED)
            self.assertFalse(outcome.click_dispatched)
            self.assertTrue(outcome.retryable_same_post)
            btn_mock.click.assert_not_called()

    def test_11_was_submit_permit_consumed_helper(self):
        """11. _was_submit_permit_consumed 헬퍼 기능 검증"""
        mock_frame = MagicMock()
        mock_frame.evaluate.return_value = True
        consumed = CommentInteractionService._was_submit_permit_consumed(mock_frame, "permit_1234")
        self.assertTrue(consumed)
        mock_frame.evaluate.assert_called_with(
            "(p) => window.__NAVER_LAST_CONSUMED_PERMIT__ === p",
            "permit_1234",
        )

        mock_frame.evaluate.return_value = False
        consumed_false = CommentInteractionService._was_submit_permit_consumed(mock_frame, "permit_1234")
        self.assertFalse(consumed_false)

        self.assertFalse(CommentInteractionService._was_submit_permit_consumed(None, "permit_1234"))
        self.assertFalse(CommentInteractionService._was_submit_permit_consumed(mock_frame, ""))

    def test_12_structured_logging_output(self):
        """12. 구조화된 로그 출력 검증: candidate, attempt, returned, event_confirmed, server_verified"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {
            "button": btn_mock,
            "frame": self.mock_page,
            "frame_name": "main",
            "selector": "button.u_cbox_btn_upload",
            "visible": True,
            "enabled": True,
            "score": 1950,
        }

        def mock_eval(script, *args):
            if "dirty" in script or "currentText" in script:
                return {"isComposing": False, "dirty": False, "text": self.comment_text}
            if "window.__NAVER_LAST_CONSUMED_PERMIT__ === p" in script:
                return True
            return None

        self.mock_page.evaluate.side_effect = mock_eval

        logs = []
        with patch("naver.interaction.logger.log", side_effect=lambda msg, *a: logs.append(msg)), \
             patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome = CommentInteractionService.submit_and_verify(
                self.mock_page,
                self.comment_text,
                origin=SubmitOrigin.USER_ENTER,
            )

            self.assertEqual(outcome.state, CommentSubmitState.SUBMITTED)
            # Check structured log messages
            self.assertTrue(any("[COMMENT][SUBMIT_CANDIDATE]" in l for l in logs))
            self.assertTrue(any("[COMMENT][CLICK_ATTEMPT]" in l for l in logs))
            self.assertTrue(any("[COMMENT][CLICK_RETURNED]" in l for l in logs))
            self.assertTrue(any("[COMMENT][CLICK_EVENT_CONFIRMED]" in l for l in logs))
            self.assertTrue(any("[COMMENT][SERVER_VERIFIED]" in l for l in logs))


if __name__ == "__main__":
    unittest.main()
