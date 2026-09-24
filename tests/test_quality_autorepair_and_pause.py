import threading
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from app.models import (
    CommentSubmitState,
    FeedPost,
    FeedSourceType,
    PostActionPlan,
    UserAction,
    WorkerCommand,
    WorkerCommandType,
)
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from app.processor import PostProcessor, build_quality_rewrite_feedback
from app.state import BotRuntimeState, FeedState, StateManager
from services.comments.community_rhythm import FinalQualityGate, FinalQualityResult
from services.gemini_extension_bridge import GeminiResult, GeminiResultStatus


class TestQualityAutoRepairAndPause(unittest.TestCase):
    def setUp(self):
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False
        self.mock_page.url = "https://m.blog.naver.com/testuser/12345"

        self.post = FeedPost(
            key="testuser:12345",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/testuser/12345",
            title="성수동 삼겹살 솥뚜껑 구이 맛집",
            blog_id="testuser",
            log_no="12345",
        )
        self.config = {
            "skip_on_comment_failure": False,
            "gemini_browser_mode": "extension_existing_chrome",
            "gemini_web_enabled": True,
            "comment_template": "잘 보고 갑니다",
        }

    def _run_handoff_case(self, error="", repair_suffix=False):
        bridge = MagicMock()
        bridge.preflight.return_value = MagicMock(ready=True)
        body = "솥뚜껑 삼겹살에 김치 조합이 눈에 들어오네요"
        repaired = body + "\n\n편안한 하루 보내세요!"
        bridge.wait_for_result.side_effect = lambda cmd, **kw: GeminiResult(
            request_id=cmd.request_id, post_key=cmd.post_key,
            navigation_version=cmd.navigation_version,
            status=GeminiResultStatus.FAILED if error else GeminiResultStatus.COMPLETED,
            text="" if error else body, error=error,
        )
        with ExitStack() as stack:
            targets = {
                "TargetPostGuard.verify": MagicMock(),
                "CommentInteractionService.open_comment_layer": (True, "ok"),
                "MobileDOMResolver.get_comment_editor_context": {"frame": self.mock_page, "root": self.mock_page},
                "ServerCommentDuplicateGuard.scan_page_for_my_comment": CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high"),
                "ContentContextExtractor.extract": MagicMock(title=self.post.title, excerpt="솥뚜껑에 삼겹살과 김치를 구웠어요"),
                "CommentEditorAdapter.set_text": True,
                "CommentInteractionService.wait_for_user_action": UserAction.SUBMIT,
                "CommentInteractionService.read_final_text": None,
                "CommentInteractionService.submit_and_verify": CommentSubmitState.SUBMITTED,
                "DraftService.resolve_suffix": "편안한 하루 보내세요" if repair_suffix else "",
            }
            mocks = {name: stack.enter_context(patch("app.processor." + name, return_value=value)) for name, value in targets.items()}
            if repair_suffix:
                valid = MagicMock(valid=True, code="ok", length=len(repaired))
                invalid = MagicMock(valid=False, code="excessive_tilde")
                stack.enter_context(patch.object(FinalQualityGate, "validate_final_text", side_effect=lambda text, **kw: invalid if text.endswith("편안한 하루 보내세요") else valid))
                stack.enter_context(patch.object(FinalQualityGate, "auto_repair", return_value=(repaired, valid)))
            processor = PostProcessor(
                {**self.config, "skip_on_comment_failure": True}, like_enabled=False,
                comment_enabled=True, gemini_web_enabled=True, gemini_extension_bridge=bridge,
            )
            result = processor.process(self.mock_page, self.post, action_plan=PostActionPlan(
                process_like=False, process_comment=True, comment_sample_selected=True, comment_sample_roll=0.1,
            ))
            return result, mocks, bridge, repaired

    def test_handoff_processor_preserves_bridge_error_without_retry(self):
        result, mocks, bridge, _ = self._run_handoff_case(error="send_commit_unknown")
        self.assertEqual(result.comment_result.error, "gemini_failed:send_commit_unknown")
        self.assertEqual(bridge.wait_for_result.call_count, 1)
        mocks["CommentEditorAdapter.set_text"].assert_not_called()

    def test_handoff_processor_injects_exact_repaired_suffix_once(self):
        result, mocks, _, repaired = self._run_handoff_case(repair_suffix=True)
        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertEqual(mocks["CommentEditorAdapter.set_text"].call_args.args[1], repaired)

    def test_01_build_quality_rewrite_feedback_mappings(self):
        """FinalQualityGate 결과 코드별 피드백 문구 매핑 검증"""
        cases = [
            ("laughter_or_emoticon", "ㅎㅎ", "초성 웃음"),
            ("emoji", "❤️", "그림 이모지"),
            ("formal_register", "합니다", "격식체/문어체"),
            ("banned_macro", "좋은 포스팅", "상투적인 매크로"),
            ("fake_experience", "저도 가봤어요", "가보지 않은 곳"),
            ("absolute_or_pressure", "꼭", "강요하거나 단정적인"),
            ("forbidden_period", ".", "마침표"),
            ("excessive_tilde", "~", "물결표"),
            ("excessive_slang", "대박", "과도한 감탄사"),
            ("length_below_minimum", "", "글자 수가 너무 짧습니다"),
            ("length_exceeded", "", "글자 수가 너무 깁니다"),
            ("rude_slang", "욕설", "속어/은어"),
            ("semantic_mismatch", "", "핵심 주제/맥락"),
            ("unknown_code", "", "품질 기준 위반"),
        ]
        for code, matched, expected_keyword in cases:
            res = FinalQualityResult(
                valid=False,
                code=code,
                reason=f"fail on {code}",
                text="sample",
                normalized_text="sample",
                preset="community",
                source="gemini",
                length=10,
                quality_band="optimal",
                length_score=1.0,
                matched=matched,
            )
            feedback = build_quality_rewrite_feedback(res)
            self.assertIn(expected_keyword, feedback, f"Feedback for {code} should contain {expected_keyword}")

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    @patch("app.processor.CommentEditorAdapter.set_text", return_value=True)
    @patch("app.processor.CommentInteractionService.wait_for_user_action")
    @patch("app.processor.CommentInteractionService.read_final_text", return_value=None)
    @patch("app.processor.CommentInteractionService.submit_and_verify")
    def test_02_quality_autorepair_success_on_second_attempt(
        self, mock_submit, mock_read_final, mock_wait, mock_set_text, mock_extract,
        mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """1차 응답에 ㅎㅎ 포함 시 1회 auto-repair 피드백 반영 후 2차 정상 통과"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(
            title="성수동 삼겹살 솥뚜껑 구이 맛집",
            excerpt="성수동 솥뚜껑 삼겹살집 다녀왔습니다. 두툼한 고기랑 김치가 예술입니다."
        )
        mock_wait.return_value = UserAction.SUBMIT
        mock_submit.return_value = CommentSubmitState.SUBMITTED

        published_prompts = []
        attempt_counter = [0]

        mock_gemini_bridge = MagicMock()
        mock_gemini_bridge.preflight.return_value = MagicMock(ready=True)

        def mock_publish(cmd, **kwargs):
            published_prompts.append(cmd.prompt)
            return True

        def mock_wait_result(cmd, **kwargs):
            attempt_counter[0] += 1
            if attempt_counter[0] == 1:
                # 1차 시도: ㅎㅎ 포함 (FinalQualityGate 탈락)
                return GeminiResult(
                    request_id=cmd.request_id,
                    post_key=cmd.post_key,
                    navigation_version=cmd.navigation_version,
                    status=GeminiResultStatus.COMPLETED,
                    text="솥뚜껑 삼겹살에 김치 조합 너무 맛있어 보여요 ㅎㅎ",
                    error="",
                )
            else:
                # 2차 시도: ㅎㅎ 제거 (FinalQualityGate 통과)
                return GeminiResult(
                    request_id=cmd.request_id,
                    post_key=cmd.post_key,
                    navigation_version=cmd.navigation_version,
                    status=GeminiResultStatus.COMPLETED,
                    text="솥뚜껑 삼겹살에 김치 조합 넘 맛있어 보이네요~",
                    error="",
                )

        mock_gemini_bridge.publish.side_effect = mock_publish
        mock_gemini_bridge.wait_for_result.side_effect = mock_wait_result

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        state_mgr = StateManager()
        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_gemini_bridge,
            state_manager=state_mgr,
        )

        with patch.object(FinalQualityGate, "auto_repair", return_value=(None, MagicMock(valid=False))):
            res = processor.process(self.mock_page, self.post, action_plan=plan)

        # 2회 호출 확인
        self.assertEqual(len(published_prompts), 2)
        # 2번째 프롬프트에 재작성 피드백이 주입되었는지 확인
        self.assertTrue("수정 요청 (1회 재작성)" in published_prompts[1] or "수정 사유" in published_prompts[1])
        self.assertIn("초성 웃음", published_prompts[1])
        # 최종 댓글 성공 확인
        self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertEqual(processor._quality_body_retry_done, True)

    @patch("app.processor.TargetPostGuard.verify")
    @patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok"))
    @patch("app.processor.MobileDOMResolver.get_comment_editor_context")
    @patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment")
    @patch("app.processor.ContentContextExtractor.extract")
    def test_03_quality_retry_exhausted_enters_paused_with_semantic_reason(
        self, mock_extract, mock_dup_scan, mock_ctx, mock_open, mock_guard
    ):
        """2회 연속 품질 탈락 시 skip_on_comment_failure=False 조건에서 pause_reason='gemini_quality_gate'로 PAUSED 진입"""
        mock_ctx.return_value = {"frame": self.mock_page, "root": self.mock_page}
        mock_dup_scan.return_value = CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")
        mock_extract.return_value = MagicMock(
            title="성수동 삼겹살 솥뚜껑 구이 맛집",
            excerpt="성수동 솥뚜껑 삼겹살집 다녀왔습니다. 두툼한 고기랑 김치가 예술입니다."
        )

        mock_gemini_bridge = MagicMock()
        mock_gemini_bridge.preflight.return_value = MagicMock(ready=True)

        # 1차, 2차 모두 ㅎㅎ / ㅋㅋ 로 탈락
        mock_gemini_bridge.wait_for_result.side_effect = [
            GeminiResult(
                request_id="r1",
                post_key="testuser:12345",
                navigation_version=1,
                status=GeminiResultStatus.COMPLETED,
                text="솥뚜껑 삼겹살 너무 맛있겠어요 ㅎㅎ",
                error="",
            ),
            GeminiResult(
                request_id="r2",
                post_key="testuser:12345",
                navigation_version=1,
                status=GeminiResultStatus.COMPLETED,
                text="솥뚜껑 삼겹살 최고네요 ㅋㅋ",
                error="",
            ),
        ]

        state_mgr = StateManager()
        pause_event = threading.Event()

        # Command bridge to allow breaking out of pause loop via skip
        command_bridge = MagicMock()
        command_bridge.pop_command.side_effect = [
            WorkerCommand(kind=WorkerCommandType.GEMINI_SKIP_POST)
        ]

        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.1
        )

        processor = PostProcessor(
            self.config,  # skip_on_comment_failure = False
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_extension_bridge=mock_gemini_bridge,
            state_manager=state_mgr,
            pause_event=pause_event,
            command_bridge=command_bridge,
        )

        with patch.object(FinalQualityGate, "auto_repair", return_value=(None, MagicMock(valid=False))):
            res = processor.process(self.mock_page, self.post, action_plan=plan)

        # Result is SKIPPED due to GEMINI_SKIP_POST command
        self.assertEqual(res.comment_result.status, CommentSubmitState.SKIPPED)
        # Pause event was cleared after command processed
        self.assertFalse(pause_event.is_set())

    def test_04_ui_update_state_semantic_pause_and_button_sync(self):
        """ui/main_window._update_ui_state()에서 pause_reason별 복구 버튼 및 btn_pause 상태 동기화 검증"""
        from ui.main_window import MainWindow

        mock_window = MagicMock(spec=MainWindow)
        mock_window.pause_event = threading.Event()
        mock_window.status_msg_lbl = MagicMock()
        mock_window.badge_lbl = MagicMock()
        mock_window.ai_post_title_lbl = MagicMock()
        mock_window.ai_post_excerpt_lbl = MagicMock()
        mock_window.btn_gemini_retry = MagicMock()
        mock_window.btn_gemini_local = MagicMock()
        mock_window.btn_gemini_skip = MagicMock()
        mock_window.btn_pause = MagicMock()
        mock_window.btn_pause.cget.return_value = "normal"

        # Case 1: gemini_quality_gate -> recovery buttons ENABLED, btn_pause says "작업 재개"
        state_qg = BotRuntimeState(
            current_state=FeedState.PAUSED,
            pause_reason="gemini_quality_gate",
            message="Gemini 품질 검사 제외 (quality_body:laughter_or_emoticon)",
        )
        MainWindow._update_ui_state(mock_window, state_qg)

        mock_window.btn_gemini_retry.configure.assert_called_with(state="normal")
        mock_window.btn_gemini_local.configure.assert_called_with(state="normal")
        mock_window.btn_gemini_skip.configure.assert_called_with(state="normal")
        mock_window.btn_pause.configure.assert_called_with(
            text="▶️ 작업 재개", fg_color="#2563EB", hover_color="#1D4ED8"
        )
        status_text = mock_window.status_msg_lbl.configure.call_args[1]["text"]
        self.assertIn("Gemini 품질 기준 미달", status_text)

        # Case 2: gemini_circuit_breaker -> recovery buttons ENABLED, status says "서킷 브레이커"
        state_cb = BotRuntimeState(
            current_state=FeedState.PAUSED,
            pause_reason="gemini_circuit_breaker",
            message="서킷 브레이커 작동",
        )
        MainWindow._update_ui_state(mock_window, state_cb)
        mock_window.btn_gemini_retry.configure.assert_called_with(state="normal")
        status_text_cb = mock_window.status_msg_lbl.configure.call_args[1]["text"]
        self.assertIn("서킷 브레이커 작동", status_text_cb)

        # Case 3: user_manual_pause -> recovery buttons DISABLED, btn_pause says "작업 재개"
        state_manual = BotRuntimeState(
            current_state=FeedState.PAUSED,
            pause_reason="user_manual_pause",
            message="작업 일시정지됨 (재개 대기 중)",
        )
        MainWindow._update_ui_state(mock_window, state_manual)
        mock_window.btn_gemini_retry.configure.assert_called_with(state="disabled")
        mock_window.btn_gemini_local.configure.assert_called_with(state="disabled")
        mock_window.btn_gemini_skip.configure.assert_called_with(state="disabled")
        mock_window.btn_pause.configure.assert_called_with(
            text="▶️ 작업 재개", fg_color="#2563EB", hover_color="#1D4ED8"
        )
        status_text_manual = mock_window.status_msg_lbl.configure.call_args[1]["text"]
        self.assertIn("작업 일시정지됨", status_text_manual)

        # Case 4: Non-paused state (e.g. OPENING_POST) -> recovery buttons DISABLED, btn_pause says "일시정지"
        state_running = BotRuntimeState(
            current_state=FeedState.OPENING_POST,
            pause_reason=None,
            message="피드 처리 중",
        )
        MainWindow._update_ui_state(mock_window, state_running)
        mock_window.btn_gemini_retry.configure.assert_called_with(state="disabled")
        mock_window.btn_pause.configure.assert_called_with(
            text="⏸️ 일시정지", fg_color="#D97706", hover_color="#B45309"
        )

    def test_05_state_manager_clear_pause_reason(self):
        """StateManager의 clear_pause_reason 동작 검증"""
        sm = StateManager()
        self.assertIsNone(sm.get_state().pause_reason)

        sm.update(new_state=FeedState.PAUSED, pause_reason="gemini_quality_gate")
        self.assertEqual(sm.get_state().pause_reason, "gemini_quality_gate")

        # Explicit clear
        sm.update(clear_pause_reason=True)
        self.assertIsNone(sm.get_state().pause_reason)

        # Pause again
        sm.update(new_state=FeedState.PAUSED, pause_reason="user_manual_pause")
        self.assertEqual(sm.get_state().pause_reason, "user_manual_pause")

        # Non-paused state change automatically resets pause_reason
        sm.update(new_state=FeedState.OPENING_POST)
        self.assertIsNone(sm.get_state().pause_reason)


if __name__ == "__main__":
    unittest.main()
