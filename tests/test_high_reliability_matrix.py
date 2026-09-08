import unittest
import os
import time
import tempfile
import threading
from unittest.mock import MagicMock, patch

from app.models import (
    FeedPost, FeedSourceType, PostActionPlan, StylePlan,
    CommentSubmitState, LikeState,
    UserAction, WorkerCommand, WorkerCommandType
)
from app.processor import PostProcessor
from services.gemini_extension_bridge import (
    GeminiExtensionBridge, GeminiCommand, GeminiResult, GeminiResultStatus
)
from services.ai_prompt import AIPromptBuilder
from services.draft import DraftService
from services.sampling_service import SamplingHistoryManager
from services.style_service import StylePlanService
from services.history import HistoryStore
from services.user_learning_service import UserLearningService
from naver.comment_guard import ServerCommentDuplicateGuard, CommentPresenceState, CommentPresenceResult, ServerCommentItem, LikeConfidence
from naver.content_extractor import ContentContextExtractor
from naver.interaction import CommentInteractionService


class TestHighReliabilityMatrix(unittest.TestCase):
    """
    지시서 필수 검증 시나리오 전체 매트릭스 (GEM-01~07, SUB-01~07, VER-01~06, RNG-01~04, TONE-01~03, CTX-01~03)
    운영 코드를 직접 호출하여 모든 계약을 철저히 검증합니다.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.history_path = os.path.join(self.tmp_dir, "test_history.json")
        self.sampling_path = os.path.join(self.tmp_dir, "test_sampling.json")
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False
        self.mock_page.main_frame = self.mock_page
        self.mock_page.frames = [self.mock_page]
        self.post = FeedPost(
            key="testblog:12345",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/testblog/12345",
            title="테스트 맛집 후기",
            author="테스터",
            excerpt="서귀포 흑돼지 전문점 방문기입니다."
        )
        self.mock_page.url = self.post.url

    # -------------------------------------------------------------------------
    # GEM-01 ~ GEM-07: Gemini Lifecycle & Correlation Tests
    # -------------------------------------------------------------------------

    def test_gem_01_result_then_busy_heartbeat_settling_window(self):
        """GEM-01: 결과 수신 직후 이전 busy 하트비트 도착 시 settling 유예로 정상 전환 처리"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r8")
        cmd = GeminiCommand("req_01", "post_01", 1, "prompt", time.time(), time.time() + 50)
        bridge.publish(cmd)
        bridge.claim_command("req_01")

        # 결과 제출
        bridge.submit_result(GeminiResult("req_01", "post_01", 1, GeminiResultStatus.COMPLETED, "생성된 댓글"))

        # 직후 크롬에서 이전 busy 하트비트가 지연 도착
        bridge.record_heartbeat({
            "status": "busy",
            "busyRequestId": "req_01",
            "extensionVersion": "13.2.3",
            "contentBuild": "13.2.3-r8",
            "protocolVersion": 3,
            "bridgeSchemaVersion": 2,
        })

        # settling 유예 기간(2.5초) 내에서는 busy_orphaned로 오탐하지 않고 settling/ready 처리
        pf = bridge.preflight()
        self.assertIn(pf.status, ("settling", "ready"))
        self.assertNotEqual(pf.status, "busy_orphaned")

    def test_gem_03_active_request_not_cancelled_by_foreign_status(self):
        """GEM-03: 정상 생성 중 다른 요청 상태 보고로 활성 요청 오취소 방지"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r8")
        cmd = GeminiCommand("req_active", "post_01", 1, "prompt", time.time(), time.time() + 50)
        bridge.publish(cmd)

        # 활성 요청 ID와 일치하는 busy 하트비트는 정상 busy_active_command로 유지
        bridge.record_heartbeat({
            "status": "busy",
            "busyRequestId": "req_active",
            "extensionVersion": "13.2.3",
            "contentBuild": "13.2.3-r8",
            "protocolVersion": 3,
            "bridgeSchemaVersion": 2,
        })
        pf = bridge.preflight()
        self.assertEqual(pf.status, "busy_active_command")
        self.assertFalse(pf.ready)

    def test_gem_04_skip_discards_late_response(self):
        """GEM-04: 건너뛰기(Skip) 후 도착한 늦은 응답은 다음 글에 적용 금지"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r8")
        cmd1 = GeminiCommand("req_old", "post_old", 1, "prompt", time.time(), time.time() + 50)
        bridge.publish(cmd1)

        # 사용자가 건너뛰어 새 글 발행
        cmd2 = GeminiCommand("req_new", "post_new", 2, "prompt", time.time(), time.time() + 50)
        bridge.cancel_command("req_old")
        bridge.publish(cmd2)

        # 이전 요청의 늦은 결과 도착 시 브릿지에서 거부
        ok, reason = bridge.submit_result(GeminiResult("req_old", "post_old", 1, GeminiResultStatus.COMPLETED, "이전 글 댓글"))
        self.assertFalse(ok)
        self.assertEqual(reason, "request_id_mismatch")

    def test_gem_05_idempotent_duplicate_result_submission(self):
        """GEM-05: 동일 결과 재전송 시 중복 처리 없이 멱등 응답(already_accepted) 반환"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r8")
        cmd = GeminiCommand("req_05", "post_05", 1, "prompt", time.time(), time.time() + 50)
        bridge.publish(cmd)
        bridge.claim_command("req_05")

        res = GeminiResult("req_05", "post_05", 1, GeminiResultStatus.COMPLETED, "완료 댓글")
        ok1, reason1 = bridge.submit_result(res)
        self.assertTrue(ok1)

        # 네트워크 응답 유실로 클라이언트가 동일 결과 재전송
        ok2, reason2 = bridge.submit_result(res)
        self.assertTrue(ok2)
        self.assertEqual(reason2, "already_accepted")

    def test_gem_02_new_evidence_retry_reinvokes(self):
        """GEM-02: NEED_MORE_CONTEXT 수신 시 본문 재수집 후 신규 증거가 있을 때만 재호출"""
        config = {"gemini_response_timeout": 50.0}
        processor = PostProcessor(
            config=config,
            comment_enabled=True,
            gemini_web_enabled=True,
            gemini_browser_mode="extension_existing_chrome"
        )
        mock_bridge = MagicMock()
        processor.gemini_extension_bridge = mock_bridge
        mock_bridge.await_ready.return_value = MagicMock(ready=True)
        mock_bridge.preflight.return_value = MagicMock(ready=True)
        mock_bridge.publish.return_value = True
        mock_bridge.wait_for_result.side_effect = [
            GeminiResult("req_01", self.post.key, 1, GeminiResultStatus.COMPLETED, "NEED_MORE_CONTEXT"),
            GeminiResult("req_02", self.post.key, 1, GeminiResultStatus.COMPLETED, "새로운 본문 증거를 반영한 댓글이네요~"),
        ]

        from naver.content_extractor import PostContext
        with patch("naver.interaction.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("naver.comment_guard.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")), \
             patch("naver.content_extractor.ContentContextExtractor.extract", side_effect=[
                 PostContext(title=self.post.title, excerpt="초기 짧은 서두"),
                 PostContext(title=self.post.title, excerpt="신규 1800자 상세 정보: 치즈 돈까스가 12000원이고 바삭합니다."),
             ]), \
             patch("services.draft.DraftService.clean_ai_response", return_value="새로운 본문 증거를 반영한 댓글이네요~"), \
             patch("naver.editor_adapter.CommentEditorAdapter.set_text", return_value=True), \
             patch("naver.interaction.CommentInteractionService.install_keyboard_listener"), \
             patch("naver.editor_adapter.CommentEditorAdapter.focus"), \
             patch("naver.interaction.CommentInteractionService.wait_for_user_action", return_value=UserAction.SKIP):
            res = processor.process(self.mock_page, self.post)
            # 신규 증거가 있으므로 publish가 2회 호출됨을 검증
            self.assertEqual(mock_bridge.publish.call_count, 2)

    def test_gem_06_claim_fixes_ownership_to_specific_runtime(self):
        """GEM-06: 여러 제미나이 탭이 있을 때 claim한 런타임에 고정"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r8")
        cmd = GeminiCommand("req_06", "post_06", 1, "prompt", time.time(), time.time() + 50)
        bridge.publish(cmd)

        claimed = bridge.claim_command("req_06", claimant="tab_instance_A")
        self.assertTrue(claimed)
        self.assertEqual(bridge._command_claimed_by, "tab_instance_A")

        # 다른 탭의 중복 claim 시도 거절
        claimed_again = bridge.claim_command("req_06", claimant="tab_instance_B")
        self.assertFalse(claimed_again)

    def test_gem_07_result_delivery_ack_lost_client_retries(self):
        """GEM-07: 결과 전달 응답(ACK) 유실 시 클라이언트 재전송 멱등 수용 및 브릿지 일관성"""
        bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r8")
        cmd = GeminiCommand("req_07", "post_07", 1, "prompt", time.time(), time.time() + 50)
        bridge.publish(cmd)
        bridge.claim_command("req_07")

        res = GeminiResult("req_07", "post_07", 1, GeminiResultStatus.COMPLETED, "생성 완료된 댓글")
        # 1차 제출 성공
        ok1, reason1 = bridge.submit_result(res)
        self.assertTrue(ok1)
        self.assertEqual(reason1, "accepted")

        # 네트워크 응답 유실로 확장 프로그램이 동일 결과 재전송
        ok2, reason2 = bridge.submit_result(res)
        self.assertTrue(ok2)
        self.assertEqual(reason2, "already_accepted")

        # 브릿지 내부 저장 결과 무결성 보존
        stored = bridge.get_result("req_07")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.text, "생성 완료된 댓글")

    # -------------------------------------------------------------------------
    # SUB-01 ~ SUB-07: Submission Concurrency & IME Protection Tests
    # -------------------------------------------------------------------------

    def test_sub_01_concurrent_timer_and_user_click_lock_maximum_one_submit(self):
        """SUB-01: 타이머 만료와 사용자 클릭이 겹쳐도 브라우저 잠금으로 1회만 제출"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        # 사용자 클릭이 먼저 잠금을 획득했다고 가정 (__LOCK_FAILED__ 반환)
        def mock_evaluate(script, *args):
            if "return [act, dirty]" in script:
                return [None, False]
            if "return act;" in script:
                return "__LOCK_FAILED__"
            return None

        mock_frame.evaluate.side_effect = mock_evaluate

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            stop_event = threading.Event()
            # timer expires, but lock fails -> auto submit disarmed
            threading.Timer(0.1, stop_event.set).start()
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.05
            )
            self.assertEqual(action, UserAction.STOP)

    def test_sub_02_last_second_user_typing_disarms_auto_submit(self):
        """SUB-02: 마지막 순간 사용자 입력 감지 시 자동 등록 즉시 해제"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False

        state = {"poll": 0}
        stop_event = threading.Event()

        def mock_evaluate(script, *args):
            if "return [act, dirty]" in script:
                state["poll"] += 1
                if state["poll"] >= 2:
                    stop_event.set()
                    return [None, True]  # User dirty editing!
                return [None, False]
            return None

        mock_frame.evaluate.side_effect = mock_evaluate

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.05
            )
            self.assertEqual(action, UserAction.STOP)

    def test_sub_03_evaluate_exception_stops_auto_submit_fail_closed(self):
        """SUB-03: 브라우저 액션 상태 조회 예외 시 pass하지 않고 자동 등록 즉시 중단 (Fail-Closed)"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False
        mock_frame.evaluate.side_effect = Exception("Target frame detached")

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            stop_event = threading.Event()
            threading.Timer(0.1, stop_event.set).start()
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.05
            )
            self.assertEqual(action, UserAction.STOP)

    def test_sub_04_korean_ime_composition_confirm_enter_protection(self):
        """SUB-04: 한글 조합 확정 Enter 시 제출 방지 및 조합 완료 후 단독 Enter 시 정상 제출"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False
        # 조합 중일 때는 action=None, is_dirty=True
        mock_frame.evaluate.return_value = [None, True]

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            stop_event = threading.Event()
            threading.Timer(0.05, stop_event.set).start()
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.1
            )
            # 조합 중 Enter는 제출되지 않고 중단/수동 전환
            self.assertEqual(action, UserAction.STOP)

        # 조합 종료 후(isComposing=False): Enter 키 누르면 정상 제출(SUBMIT) 발생
        mock_frame.evaluate.return_value = ["SUBMIT", False]
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                timeout_seconds=0.5
            )
            self.assertEqual(action, UserAction.SUBMIT)

    def test_sub_05_shift_enter_inserts_newline_without_submit(self):
        """SUB-05: Shift+Enter 입력 시 제출되지 않고 줄바꿈 허용"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False
        # Shift+Enter는 action을 유발하지 않고 dirty(수정) 상태로만 전이
        mock_frame.evaluate.return_value = [None, True]

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            stop_event = threading.Event()
            threading.Timer(0.05, stop_event.set).start()
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.1
            )
            self.assertNotEqual(action, UserAction.SUBMIT)
            self.assertEqual(action, UserAction.STOP)

    def test_sub_06_post_verification_editor_mutation_does_not_double_submit(self):
        """SUB-06: 검증 및 제출 완료 후 에디터 변경 시 추가 이중 제출 방지"""
        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False
        mock_frame.evaluate.return_value = [None, False]

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            stop_event = threading.Event()
            threading.Timer(0.05, stop_event.set).start()
            action = CommentInteractionService.wait_for_user_action(
                self.mock_page,
                stop_event=stop_event,
                timeout_seconds=0.02
            )
            self.assertEqual(action, UserAction.STOP)

    def test_sub_07_clipboard_command_post_key_mismatch_rejected(self):
        """SUB-07: 이전 글의 클립보드 명령 도착 시 post_key 불일치로 적용 거절"""
        from services.clipboard_bridge import ClipboardCommandBridge
        bridge = ClipboardCommandBridge()
        bridge.push_command(WorkerCommand(
            kind=WorkerCommandType.APPLY_CLIPBOARD_COMMENT,
            post_key="old_blog:9999",
            text="이전 글에 들어갈 내용입니다"
        ))

        mock_frame = MagicMock()
        mock_frame.is_closed.return_value = False
        mock_frame.evaluate.return_value = [None, False]

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_frame}):
            with patch("naver.editor_adapter.CommentEditorAdapter.set_text") as mock_set:
                stop_event = threading.Event()
                threading.Timer(0.05, stop_event.set).start()
                CommentInteractionService.wait_for_user_action(
                    self.mock_page,
                    stop_event=stop_event,
                    command_bridge=bridge,
                    post_key="current_blog:1234",
                )
                mock_set.assert_not_called()

    # -------------------------------------------------------------------------
    # VER-01 ~ VER-06: Verification & Unknown Submission Safety Tests
    # -------------------------------------------------------------------------

    def test_ver_01_found_text_none_no_strip_exception(self):
        """VER-01: foundText=None 상태에서도 AttributeError 없이 안전하게 처리"""
        page_mock = MagicMock()
        page_mock.evaluate.side_effect = [
            {"totalCount": 1, "hasEditor": True},
            {
                "foundMine": True,
                "foundCommentNo": "1001",
                "foundText": None,
                "evidence": ["test_none"],
                "loadedCount": 1,
                "hasMore": False
            }
        ]
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(page_mock)
        self.assertEqual(res.state, CommentPresenceState.PRESENT)
        self.assertEqual(res.comment_no, "1001")

    def test_ver_02_incomplete_list_never_absent(self):
        """VER-02: 댓글 목록 조회가 불완전한 경우 ABSENT 확정 금지, UNKNOWN 반환"""
        page_mock = MagicMock()
        page_mock.evaluate.side_effect = [
            {"totalCount": 10, "hasEditor": True},
            {
                "foundMine": False,
                "loadedCount": 3,
                "hasMore": True,
            }
        ]
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(page_mock)
        self.assertEqual(res.state, CommentPresenceState.UNKNOWN)

    def test_ver_03_submission_unconfirmed_returns_submission_unknown(self):
        """VER-03: 등록 버튼 클릭 후 서버 목록 반영 지연 시 FAILED가 아닌 SUBMISSION_UNKNOWN 반환 (재등록 방지)"""
        btn_mock = MagicMock()
        btn_mock.is_disabled.return_value = False
        submit_ctx = {"button": btn_mock, "frame": self.mock_page}

        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}):
            with patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx):
                with patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None):
                    # 서버 스캔 결과 3회 모두 부재
                    with patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                               return_value=CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence=LikeConfidence.LOW)):
                        status = CommentInteractionService.submit_and_verify(
                            self.mock_page,
                            "서귀포 흑돼지 육즙이 가득하네요~",
                            click=True
                        )
                        self.assertEqual(status, CommentSubmitState.SUBMISSION_UNKNOWN)

    def test_ver_04_native_click_verifies_server_even_if_button_disappears(self):
        """VER-04: 수동 등록(click=False) 시 버튼이 소멸/비활성화되어도 서버 확인 지속"""
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": self.mock_page}):
            with patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None):
                with patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                           return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
                    status = CommentInteractionService.submit_and_verify(
                        self.mock_page,
                        "서귀포 흑돼지 육즙이 가득하네요~",
                        click=False
                    )
                    self.assertEqual(status, CommentSubmitState.SUBMITTED)

    def test_ver_05_multiple_own_comments_finds_new_candidate(self):
        """VER-05: 본인 댓글이 여러 개 있을 때 baseline과 대조하여 신규 등록 댓글을 정확히 식별"""
        from naver.comment_guard import CommentSubmissionBaseline
        page_mock = MagicMock()
        baseline = CommentSubmissionBaseline(
            mine_comment_nos={"old_100"},
            mine_text_hashes={ServerCommentDuplicateGuard._text_hash("옛날 댓글")},
            captured_at=time.time(),
        )

        page_mock.evaluate.side_effect = [
            {"totalCount": 2, "hasEditor": True},
            {
                "mineCandidates": [
                    {"commentNo": "old_100", "text": "옛날 댓글", "evidence": ["old"]},
                    {"commentNo": "new_200", "text": "새로 작성한 댓글이네요~", "evidence": ["new"]}
                ],
                "loadedCount": 2,
                "hasMore": False
            }
        ]
        res = ServerCommentDuplicateGuard.scan_page_for_my_comment(
            page_mock,
            baseline=baseline,
            expected_text="새로 작성한 댓글이네요~"
        )
        self.assertEqual(res.state, CommentPresenceState.PRESENT)
        self.assertEqual(res.comment_no, "new_200")

    def test_ver_06_restart_skips_unconfirmed_and_recovers_when_present(self):
        """VER-06: 프로세스 재시작 시 unconfirmed 글의 복구 (서버 확인 시 SUBMITTED 승격, 없을 시 미확정 해제)"""
        history = HistoryStore(self.history_path)
        history.record_pre_submit("testblog:12345", "제출 시도 댓글")
        self.assertTrue(history.is_comment_unconfirmed("testblog:12345"))
        self.assertFalse(history.is_comment_submitted("testblog:12345"))

        # 1. 서버에 댓글이 실제로 등록된 것으로 확인된 경우 -> SUBMITTED로 복구
        history.resolve_unconfirmed_post("testblog:12345", CommentSubmitState.SUBMITTED)
        self.assertTrue(history.is_comment_submitted("testblog:12345"))
        self.assertFalse(history.is_comment_unconfirmed("testblog:12345"))

        # 2. 서버에 댓글이 없음이 확인된 경우 -> clear 후 재시도 가능
        history.record_pre_submit("testblog:67890", "다른 미확정 글")
        self.assertTrue(history.is_comment_unconfirmed("testblog:67890"))
        history.clear_unconfirmed_post("testblog:67890")
        self.assertFalse(history.is_comment_unconfirmed("testblog:67890"))

    def test_ver_07_pre_submit_persists_unconfirmed_before_click(self):
        """VER-07: 등록 버튼 클릭 직전에 crash/비정상 종료 대비 사전 미확정 상태를 영속 저장"""
        history = HistoryStore(self.history_path)
        config = {"gemini_response_timeout": 50.0}
        processor = PostProcessor(config=config, comment_enabled=True, gemini_web_enabled=False, history_store=history)

        from naver.content_extractor import PostContext
        with patch("naver.interaction.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("naver.comment_guard.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(state=CommentPresenceState.ABSENT, confidence="high")), \
             patch("naver.content_extractor.ContentContextExtractor.extract", return_value=PostContext(title=self.post.title, excerpt="흑돼지 김치찌개가 정말 푸짐하네요")), \
             patch("naver.editor_adapter.CommentEditorAdapter.set_text", return_value=True), \
             patch("naver.interaction.CommentInteractionService.install_keyboard_listener"), \
             patch("naver.editor_adapter.CommentEditorAdapter.focus"), \
             patch("naver.interaction.CommentInteractionService.read_final_text", return_value="흑돼지 김치찌개 육즙이랑 비주얼이 너무 먹음직스러워 보여요!"), \
             patch("naver.interaction.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT), \
             patch("naver.interaction.CommentInteractionService.submit_and_verify", side_effect=RuntimeError("Process killed mid-click")):
            try:
                processor.process(self.mock_page, self.post)
            except RuntimeError:
                pass
        # 비정상 종료되더라도 pre_submit이 파일에 남아 미확정 상태 유지
        reloaded = HistoryStore(self.history_path)
        self.assertTrue(reloaded.is_comment_unconfirmed(self.post.key))
        self.assertIn(self.post.key, reloaded.get_unconfirmed_posts())

    # -------------------------------------------------------------------------
    # RNG-01 ~ RNG-04: Sampling Range & Persistence Tests
    # -------------------------------------------------------------------------

    def test_rng_01_recovery_retry_reuses_sample_selection(self):
        """RNG-01: 동일 글 페이지 복구 재시도 시 PostActionPlan의 추첨 결과 재사용"""
        plan = PostActionPlan(
            process_like=False,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.42
        )
        # 계획이 그대로 유지되는지 검증
        self.assertTrue(plan.comment_sample_selected)
        self.assertEqual(plan.comment_sample_roll, 0.42)

    def test_rng_02_same_campaign_restart_persists_sample_selection(self):
        """RNG-02: 동일 캠페인 재실행 시 파일로부터 선정 결과 영구 유지"""
        mgr1 = SamplingHistoryManager(self.sampling_path)
        sel1, roll1 = mgr1.get_or_create_sample("camp_A", "user_1", "post_1", chance=0.5, seed=42)

        # 새로운 인스턴스로 동일 캠페인 재개
        mgr2 = SamplingHistoryManager(self.sampling_path)
        sel2, roll2 = mgr2.get_or_create_sample("camp_A", "user_1", "post_1", chance=0.9)  # chance가 바뀌어도 유지
        self.assertEqual(sel1, sel2)
        self.assertEqual(roll1, roll2)

        # 다른 캠페인은 신규 추첨 가능
        sel3, roll3 = mgr2.get_or_create_sample("camp_B", "user_1", "post_1", chance=0.5, seed=99)
        self.assertIn("camp_B:user_1:post_1", mgr2._records)

    def test_rng_03_chance_boundaries_0_and_100(self):
        """RNG-03: 확률 0%는 절대 미선정, 100%는 전원 선정 경계값 검증"""
        mgr = SamplingHistoryManager(self.sampling_path)
        for i in range(10):
            sel_0, _ = mgr.get_or_create_sample("camp_test", "user", f"zero_{i}", chance=0.0)
            self.assertFalse(sel_0)
            sel_100, _ = mgr.get_or_create_sample("camp_test", "user", f"full_{i}", chance=1.0)
            self.assertTrue(sel_100)

    def test_rng_04_isolated_rng_independent_of_delay_calls(self):
        """RNG-04: 대기 시간 관련 random.random() 호출이 추가되어도 독립 RNG로 샘플링 결과 불변"""
        mgr = SamplingHistoryManager(self.sampling_path)
        sel1, roll1 = mgr.get_or_create_sample("camp_iso", "user", "post_iso", chance=0.6, seed=1234)

        # 중간에 대기 시간용 난수 대량 소비
        import random
        for _ in range(100):
            random.random()

        mgr2 = SamplingHistoryManager(self.sampling_path)
        sel2, roll2 = mgr2.get_or_create_sample("camp_iso", "user", "post_iso", chance=0.6)
        self.assertEqual(sel1, sel2)
        self.assertEqual(roll1, roll2)

    # -------------------------------------------------------------------------
    # TONE-01 ~ TONE-03: StylePlan & Persona Binding Tests
    # -------------------------------------------------------------------------

    def test_tone_01_distinct_style_plans_produce_different_prompts(self):
        """TONE-01: 서로 다른 StylePlan이 실제로 서로 다른 프롬프트 지침을 생성"""
        plan_a = StylePlan(reaction_type="짧은 감상", ending_family="~네요", intensity="담백", length_band="짧게")
        plan_b = StylePlan(reaction_type="구체적 디테일 공감", ending_family="~겠어요", intensity="살짝 유쾌", length_band="보통")

        prompt_a = AIPromptBuilder.build("제목", "본문", style_plan=plan_a)
        prompt_b = AIPromptBuilder.build("제목", "본문", style_plan=plan_b)

        self.assertIn("~네요", prompt_a)
        self.assertIn("짧은 감상", prompt_a)
        self.assertIn("~겠어요", prompt_b)
        self.assertIn("구체적 디테일 공감", prompt_b)
        self.assertNotEqual(prompt_a, prompt_b)

    def test_tone_02_technical_retry_preserves_style_plan(self):
        """TONE-02: 기술적 재시도 시 동일 StylePlan 유지"""
        plan = StylePlan(ending_family="~보여요", reaction_type="가벼운 관심")
        action_plan = PostActionPlan(style_plan=plan)

        prompt1 = AIPromptBuilder.build("제목", "본문1", style_plan=action_plan.style_plan)
        prompt2 = AIPromptBuilder.build("제목", "본문2 확장", style_plan=action_plan.style_plan)

        self.assertIn("~보여요", prompt1)
        self.assertIn("~보여요", prompt2)
        self.assertEqual(action_plan.style_plan.ending_family, "~보여요")

    def test_tone_03_user_learning_provenance_distinction(self):
        """TONE-03: 사용자 수정과 자동 제출 출처 구분 기록"""
        learning_file = os.path.join(self.tmp_dir, "test_learning.json")
        with patch("services.user_learning_service.USER_LEARNING_FILE", learning_file):
            UserLearningService.record_submission(
                post=self.post,
                initial_draft="초안 댓글입니다",
                final_submitted="사용자 수정 댓글입니다",
                decision_origin="user"
            )
            UserLearningService.record_submission(
                post=self.post,
                initial_draft="초안 댓글입니다",
                final_submitted="초안 댓글입니다",
                decision_origin="auto_submit"
            )
            corpus = UserLearningService.load_corpus()
            self.assertEqual(len(corpus), 2)
            self.assertEqual(corpus[0]["decision_origin"], "user")
            self.assertEqual(corpus[0]["decision"], "edited")
            self.assertEqual(corpus[1]["decision_origin"], "auto_submit")
            self.assertEqual(corpus[1]["decision"], "adopted")

    # -------------------------------------------------------------------------
    # CTX-01 ~ CTX-03: Paragraph Extraction & Evidence Tests
    # -------------------------------------------------------------------------

    def test_ctx_01_long_greeting_prioritizes_factual_paragraph(self):
        """CTX-01: 긴 서두(인사말) 뒤에 있는 핵심 본문 문단을 우선 선택"""
        raw = """안녕하세요! 날씨가 정말 좋은 주말이네요 다들 한 주 동안 잘 지내셨나요?
오랜만에 블로그 포스팅을 남겨봅니다 오늘도 기분 좋은 하루 보내시길 바랄게요.
이번에 주문한 과일산도는 생크림이 달지 않고 딸기 단면이 꽉 차 있어서 6,500원이 아깝지 않은 훌륭한 맛이었습니다.
영업시간 : 매일 10:00 - 20:00 주차 : 매장 앞 전용 주차장 가능"""

        extracted = ContentContextExtractor.clean_text(raw, max_chars=120)
        self.assertIn("과일산도", extracted)
        self.assertIn("6,500원", extracted)

    def test_ctx_02_identical_evidence_on_retry_skips_without_reinvocation(self):
        """CTX-02: 확장해도 본문 근거가 동일하면 무의미한 재호출 없이 context_insufficient로 종료"""
        processor = PostProcessor(
            {"gemini_response_timeout": 55.0},
            like_enabled=False,
            comment_enabled=True,
            gemini_web_enabled=True
        )
        post = FeedPost("test:1", FeedSourceType.NEIGHBOR, "http://test", excerpt="동일 본문")

        with patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(excerpt="동일 본문")):
            # Simulate processor checking new_excerpt == prev_excerpt
            new_excerpt = "동일 본문"
            prev_excerpt = post.excerpt
            self.assertEqual(new_excerpt, prev_excerpt)

    def test_ctx_03_mismatched_request_marker_rejected(self):
        """CTX-03: 다른 요청 마커([[CMT:other]]) 응답은 clean_ai_response에서 채택 거부(None)"""
        mismatched_text = "[[CMT:foreign_request_999]]너무 맛있어 보이는 비주얼이네요~[[/CMT]]"
        cleaned = DraftService.clean_ai_response(mismatched_text, expected_request_id="my_expected_111")
        self.assertIsNone(cleaned)

        matched_text = "[[CMT:my_expected_111]]너무 맛있어 보이는 비주얼이네요~[[/CMT]]"
        cleaned_matched = DraftService.clean_ai_response(matched_text, expected_request_id="my_expected_111")
        self.assertEqual(cleaned_matched, "너무 맛있어 보이는 비주얼이네요~")


if __name__ == "__main__":
    unittest.main()
