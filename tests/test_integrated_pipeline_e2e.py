import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.models import (
    FeedPost,
    FeedSourceType,
    PostActionPlan,
    CommentProcessResult,
    LikeProcessResult,
    CommentSubmitState,
    LikeState,
    PostProcessResult,
    UserAction,
)
from app.processor import PostProcessor, GenerationContext
from app.state import StateManager, FeedState
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import (
    CommentDraftInspector,
    FinalQualityGate,
    CommunityRhythmPreset,
)
from services.gemini_extension_bridge import (
    GeminiCommand,
    GeminiResult,
    GeminiResultStatus,
    GeminiExtensionBridge,
)
from services.history import HistoryStore
from services.user_learning_service import (
    UserLearningService,
    CorpusSource,
    USER_LEARNING_FILE,
)


class TestIntegratedPipelineE2E(unittest.TestCase):
    """
    설정 -> 표본 선택 -> 본문 추출 -> 개인화 프롬프트 -> Gemini 응답 ->
    초안 검사 -> 사용자 수정 -> 등록 확인 -> 이력·통계 전 과정 통합 연결 검증
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_learning_file = os.path.join(self.tmp_dir.name, "user_learning_corpus.json")
        self.test_history_file = os.path.join(self.tmp_dir.name, "test_history.json")

        self.config = {
            "comment_style_preset": "community",
            "auto_comment_submit_enabled": False,
            "auto_comment_chance": 0.60,
            "gemini_web_enabled": True,
            "gemini_browser_mode": "extension_existing_chrome",
            "gemini_response_timeout": 10.0,
            "ai_prompt_style": "warm_short",
            "ai_context_max_chars": 800,
            "like_popularity_guard_enabled": False,
            "daily_visitor_guard_enabled": False,
        }

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_e2e_full_happy_path_with_user_edit_and_learning_recording(self):
        """
        정상 플로우:
        본문 추출 -> 개인화 프롬프트(동적 예시 포함) -> Gemini 초안 수신 ->
        초안 검사 통과 -> 사용자 직접 수정(물결표 다수 등 스타일 제약 면제) ->
        등록 확인 -> 히스토리 & 학습 코퍼스(user_edit) 기록 -> 통계 갱신
        """
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://m.blog.naver.com/testuser/1001"
        mock_page.main_frame = mock_page

        state_mgr = StateManager()
        history_store = HistoryStore(file_path=self.test_history_file)

        post = FeedPost(
            key="testuser_1001",
            url="https://m.blog.naver.com/testuser/1001",
            title="수유역 경양식 돈까스 스프 밥 무한리필 맛집",
            excerpt="경양식 돈까스인데 스프와 밥이 무한리필이라 푸짐하고 좋습니다.",
            source=FeedSourceType.NEIGHBOR,
        )

        plan = PostActionPlan(
            process_like=True,
            process_comment=True,
            comment_sample_selected=True,
            comment_sample_roll=0.35,
        )

        mock_bridge = MagicMock(spec=GeminiExtensionBridge)
        preflight_mock = MagicMock()
        preflight_mock.ready = True
        mock_bridge.await_ready.return_value = preflight_mock
        mock_bridge.publish.return_value = True

        # Gemini가 초안 생성 응답
        gemini_draft = "스프랑 밥 무한리필이라니 경양식 돈까스 먹을 때 최고네요~"
        mock_bridge.wait_for_result.return_value = GeminiResult(
            request_id="req-1",
            post_key=post.key,
            navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text=gemini_draft,
        )

        processor = PostProcessor(
            self.config,
            like_enabled=True,
            comment_enabled=True,
            state_manager=state_mgr,
            gemini_extension_bridge=mock_bridge,
            history_store=history_store,
            on_comment_committed=history_store.record_comment_checkpoint,
        )

        with patch("services.user_learning_service.USER_LEARNING_FILE", self.test_learning_file), \
             patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("app.processor.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page, "root": mock_page}), \
             patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(CommentPresenceState.ABSENT, None, None)), \
             patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(title=post.title, excerpt=post.excerpt)), \
             patch("app.processor.CommentEditorAdapter.set_text", return_value=True), \
             patch("app.processor.CommentInteractionService.install_keyboard_listener"), \
             patch("app.processor.CommentEditorAdapter.focus"), \
             patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.SUBMIT), \
             patch("app.processor.CommentInteractionService.read_final_text", return_value="스프랑 밥 무한리필이라니 돈까스 먹을 때 최고네요~~~"), \
             patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED):

            result = processor.process(mock_page, post, action_plan=plan)

            # 1. 처리 결과 검증
            self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
            self.assertEqual(result.comment_result.submitted_text, "스프랑 밥 무한리필이라니 돈까스 먹을 때 최고네요~~~")

            # 2. 히스토리 기록 검증
            recent = history_store.get_recent_submitted_comments(limit=5)
            self.assertEqual(len(recent), 1)
            self.assertIn("돈까스 먹을 때 최고네요~~~", recent[0])

            # 3. 사용자 학습 코퍼스(user_learning_corpus.json) 기록 검증
            corpus = UserLearningService.load_corpus()
            self.assertEqual(len(corpus), 1)
            self.assertEqual(corpus[0]["source_type"], CorpusSource.USER_EDIT.value)
            self.assertTrue(corpus[0]["is_user_edited"])
            self.assertEqual(corpus[0]["final_submitted"], "스프랑 밥 무한리필이라니 돈까스 먹을 때 최고네요~~~")

            # 4. 처리 글 수 (processed_count) 정확히 1 증가 검증
            self.assertEqual(state_mgr.state.processed_count, 1)
            self.assertEqual(state_mgr.state.comments_count, 1)

    def test_processed_count_strictly_increments_by_one_across_diverse_paths(self):
        """
        처리 글 수(processed_count)는 모든 조기 반환, 스킵, 예외를 포함해 정확히 1만 증가해야 함:
        1) comment_sample_selected=False 조기 반환
        2) 본문 추출 실패(본문 비어있음)
        3) 사용자 스킵 이벤트 발생
        """
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://m.blog.naver.com/testuser/2001"
        mock_page.main_frame = mock_page

        state_mgr = StateManager()
        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            state_manager=state_mgr,
        )

        # Path 1: comment_sample_selected=False (랜덤 확률 제외)
        post1 = FeedPost(key="post_p1", url="https://m.blog.naver.com/testuser/2001", title="글 1", excerpt="본문 1", source=FeedSourceType.NEIGHBOR)
        plan1 = PostActionPlan(process_like=False, process_comment=True, comment_sample_selected=False)
        processor.process(mock_page, post1, action_plan=plan1)
        self.assertEqual(state_mgr.state.processed_count, 1)

        # Path 2: 본문 추출 실패
        post2 = FeedPost(key="post_p2", url="https://m.blog.naver.com/testuser/2002", title="글 2", excerpt="", source=FeedSourceType.NEIGHBOR)
        with patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("app.processor.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page, "root": mock_page}), \
             patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(CommentPresenceState.ABSENT, None, None)), \
             patch("app.processor.ContentContextExtractor.extract") as mock_ext:
            mock_ctx = MagicMock()
            mock_ctx.title = "글 2"
            mock_ctx.excerpt = ""  # 본문 없음
            mock_ext.return_value = mock_ctx

            processor.process(mock_page, post2)
            self.assertEqual(state_mgr.state.processed_count, 2)

        # Path 3: 사용자 스킵 (wait_for_user_action -> SKIP)
        post3 = FeedPost(key="post_p3", url="https://m.blog.naver.com/testuser/2003", title="글 3", excerpt="본문 3", source=FeedSourceType.NEIGHBOR)
        with patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("app.processor.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page, "root": mock_page}), \
             patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(CommentPresenceState.ABSENT, None, None)), \
             patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(title=post3.title, excerpt=post3.excerpt)), \
             patch("app.processor.CommentEditorAdapter.set_text", return_value=True), \
             patch("app.processor.CommentInteractionService.install_keyboard_listener"), \
             patch("app.processor.CommentEditorAdapter.focus"), \
             patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.SKIP):

            processor.process(mock_page, post3)
            self.assertEqual(state_mgr.state.processed_count, 3)

            # 동일 포스트 재호출 시 중복 증가 방지(Idempotency) 검증
            processor.process(mock_page, post3)
            self.assertEqual(state_mgr.state.processed_count, 3)

    def test_e2e_need_more_context_retry_and_recovery(self):
        """NEED_MORE_CONTEXT 수신 시 1800자 재추출 후 성공적 재생성 플로우 검증"""
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://m.blog.naver.com/testuser/3001"
        mock_page.main_frame = mock_page

        state_mgr = StateManager()
        post = FeedPost(
            key="post_nmc",
            url="https://m.blog.naver.com/testuser/3001",
            title="성수동 신상 카페 방문기",
            excerpt="간단 요약",
            source=FeedSourceType.NEIGHBOR,
        )

        mock_bridge = MagicMock(spec=GeminiExtensionBridge)
        preflight_mock = MagicMock()
        preflight_mock.ready = True
        mock_bridge.await_ready.return_value = preflight_mock
        mock_bridge.publish.return_value = True

        good_comment = "매일 아침 직접 굽는 소금빵이라 버터 풍미가 정말 제대로 느껴지네요~"

        # 1차: NEED_MORE_CONTEXT, 2차: 정상 댓글
        res1 = GeminiResult(
            request_id="req-1", post_key=post.key,
            navigation_version=1, status=GeminiResultStatus.COMPLETED, text="NEED_MORE_CONTEXT"
        )
        res2 = GeminiResult(
            request_id="req-2", post_key=post.key,
            navigation_version=1, status=GeminiResultStatus.COMPLETED, text=good_comment
        )
        mock_bridge.wait_for_result.side_effect = [res1, res2]

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            state_manager=state_mgr,
            gemini_extension_bridge=mock_bridge,
        )

        with patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("app.processor.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page, "root": mock_page}), \
             patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(CommentPresenceState.ABSENT, None, None)), \
             patch("app.processor.ContentContextExtractor.extract") as mock_ext, \
             patch("app.processor.CommentEditorAdapter.set_text", return_value=True), \
             patch("app.processor.CommentInteractionService.install_keyboard_listener"), \
             patch("app.processor.CommentEditorAdapter.focus"), \
             patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT), \
             patch("app.processor.CommentInteractionService.read_final_text", return_value=good_comment), \
             patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED):

            ctx1 = MagicMock(title=post.title, excerpt="성수동 신상 카페 소금빵 맛집")
            ctx2 = MagicMock(title=post.title, excerpt="성수동 신상 카페 매일 아침 소금빵을 구워내며 프랑스산 고메 버터를 듬뿍 넣어 풍미가 깊습니다.")
            mock_ext.side_effect = [ctx1, ctx2]

            result = processor.process(mock_page, post)

            self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
            self.assertEqual(result.comment_result.submitted_text, good_comment)
            self.assertEqual(state_mgr.state.processed_count, 1)

    def test_draft_inspector_rewrite_feedback_loop(self):
        """초안 검사기 반려 시 1회 재작성 피드백을 프롬프트에 반영하여 재생성하는 루프 검증"""
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_page.url = "https://m.blog.naver.com/testuser/4001"
        mock_page.main_frame = mock_page

        state_mgr = StateManager()
        post = FeedPost(
            key="post_rewrite",
            url="https://m.blog.naver.com/testuser/4001",
            title="수제버거 전문점",
            excerpt="패티 두께가 2cm이고 육즙이 풍부합니다.",
            source=FeedSourceType.NEIGHBOR,
        )

        mock_bridge = MagicMock(spec=GeminiExtensionBridge)
        preflight_mock = MagicMock()
        preflight_mock.ready = True
        mock_bridge.await_ready.return_value = preflight_mock
        mock_bridge.publish.return_value = True

        # 1차 응답: 사족 중복 마무리 ('가봐야겠어요') -> 반려 대상
        bad_draft = "수제 패티 비쥬얼 진짜 예술이네요 다음에 꼭 한번 가봐야겠어요"
        # 2차 응답: 피드백 반영 깔끔한 단문 반응
        good_draft = "수제 패티 두께가 2cm라니 육즙이 장난 아니겠네요~"

        res1 = GeminiResult(request_id="r1", post_key=post.key, navigation_version=1, status=GeminiResultStatus.COMPLETED, text=bad_draft)
        res2 = GeminiResult(request_id="r2", post_key=post.key, navigation_version=1, status=GeminiResultStatus.COMPLETED, text=good_draft)
        mock_bridge.wait_for_result.side_effect = [res1, res2]

        processor = PostProcessor(
            self.config,
            like_enabled=False,
            comment_enabled=True,
            state_manager=state_mgr,
            gemini_extension_bridge=mock_bridge,
        )

        with patch("app.processor.TargetPostGuard.verify"), \
             patch("app.processor.CommentInteractionService.open_comment_layer", return_value=(True, "ok")), \
             patch("app.processor.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page, "root": mock_page}), \
             patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(CommentPresenceState.ABSENT, None, None)), \
             patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(title=post.title, excerpt=post.excerpt)), \
             patch("app.processor.CommentEditorAdapter.set_text", return_value=True), \
             patch("app.processor.CommentInteractionService.install_keyboard_listener"), \
             patch("app.processor.CommentEditorAdapter.focus"), \
             patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT), \
             patch("app.processor.CommentInteractionService.read_final_text", return_value=good_draft), \
             patch("app.processor.CommentInteractionService.submit_and_verify", return_value=CommentSubmitState.SUBMITTED):

            result = processor.process(mock_page, post)

            self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
            self.assertEqual(result.comment_result.submitted_text, good_draft)
            # 재작성 후 1회 카운트 검증
            self.assertEqual(state_mgr.state.processed_count, 1)

    def test_e2e_auto_submit_5_consecutive_runs_with_full_comment_layer_contract(self):
        """
        Section 9: Auto-submit End-to-End Gate
        5건 연속 실행 검증:
        - comment_layer_timeout = 0/5
        - Gemini publish = 5/5
        - editor readback = 5/5
        - submit click dispatched = 5/5
        - server verified = 5/5
        - duplicate submit = 0
        """
        from app.models import SubmitOrigin

        cfg = dict(self.config)
        cfg["auto_comment_submit_enabled"] = True
        cfg["auto_comment_delay_min"] = 1.0
        cfg["auto_comment_delay_max"] = 2.0

        state_mgr = StateManager()

        publish_calls = []
        editor_readbacks = []
        clicks_dispatched = []
        server_verified_calls = []
        layer_timeouts = []

        mock_bridge = MagicMock(spec=GeminiExtensionBridge)
        preflight_mock = MagicMock(ready=True)
        mock_bridge.await_ready.return_value = preflight_mock

        def bridge_publish(cmd, *args, **kwargs):
            publish_calls.append(cmd)
            return True
        mock_bridge.publish.side_effect = bridge_publish

        def bridge_wait(cmd, **kwargs):
            return GeminiResult(
                request_id=cmd.request_id,
                post_key=cmd.post_key,
                navigation_version=cmd.navigation_version,
                status=GeminiResultStatus.COMPLETED,
                text=f"수제 패티랑 소스 조합이 정말 좋아 보이네요~ ({cmd.post_key})",
                error="",
            )
        mock_bridge.wait_for_result.side_effect = bridge_wait

        processor = PostProcessor(
            cfg,
            like_enabled=False,
            comment_enabled=True,
            state_manager=state_mgr,
            gemini_extension_bridge=mock_bridge,
            auto_comment_submit_enabled=True,
            auto_comment_delay_min=1.0,
            auto_comment_delay_max=2.0,
        )

        for i in range(1, 6):
            post_key = f"live_test_{i}"
            post = FeedPost(
                key=post_key,
                url=f"https://m.blog.naver.com/testuser/{i}",
                title=f"성수동 경양식 돈까스 맛집 후기 {i}",
                excerpt=f"성수동 돈까스 전문점에 다녀왔습니다. 수제 패티와 소스가 조화롭습니다. ({i})",
                source=FeedSourceType.NEIGHBOR,
            )
            plan = PostActionPlan(
                process_like=False,
                process_comment=True,
                comment_sample_selected=True,
                comment_sample_roll=0.1,
            )

            mock_page = MagicMock()
            mock_page.is_closed.return_value = False
            mock_page.url = post.url
            mock_page.frames = [mock_page]

            # Opener button fixture: visible and enabled
            opener_btn = MagicMock()
            opener_btn.is_visible.return_value = True
            opener_btn.is_enabled.return_value = True
            opener_btn.bounding_box.return_value = {"x": 10, "y": 400, "width": 60, "height": 30}

            def page_locator(sel):
                loc = MagicMock()
                if "pst.re" in sel:
                    loc.count.return_value = 1
                    loc.nth.return_value = opener_btn
                else:
                    loc.count.return_value = 0
                    loc.first.count.return_value = 0
                return loc
            mock_page.locator.side_effect = page_locator

            def mock_set_text(page, text):
                editor_readbacks.append(text)
                return True

            def mock_submit_and_verify(page, final_text, stop_event=None, timeout=8.0, preset="thoughtful", click=None, origin=None):
                if origin in (SubmitOrigin.USER_ENTER, SubmitOrigin.AUTO_TIMER):
                    clicks_dispatched.append(post_key)
                server_verified_calls.append(post_key)
                return CommentSubmitState.SUBMITTED

            with patch("app.processor.TargetPostGuard.verify"), \
                 patch("app.processor.ServerCommentDuplicateGuard.scan_page_for_my_comment", return_value=CommentPresenceResult(CommentPresenceState.ABSENT, None, None)), \
                 patch("app.processor.ContentContextExtractor.extract", return_value=MagicMock(title=post.title, excerpt=post.excerpt)), \
                 patch("app.processor.CommentEditorAdapter.set_text", side_effect=mock_set_text), \
                 patch("app.processor.CommentInteractionService.install_keyboard_listener"), \
                 patch("app.processor.CommentEditorAdapter.focus"), \
                 patch("app.processor.CommentInteractionService.wait_for_user_action", return_value=UserAction.AUTO_SUBMIT), \
                 patch("app.processor.CommentInteractionService.read_final_text", side_effect=lambda page: editor_readbacks[-1]), \
                 patch("app.processor.CommentInteractionService.submit_and_verify", side_effect=mock_submit_and_verify), \
                 patch("naver.interaction.CommentEditorAdapter.is_visible", side_effect=[False, True, True]):

                res = processor.process(mock_page, post, action_plan=plan)

                if res.comment_result.error == "comment_layer_timeout":
                    layer_timeouts.append(post_key)
                self.assertEqual(res.comment_result.status, CommentSubmitState.SUBMITTED)

        # Gate 검증: 5/5 전원 완결
        self.assertEqual(len(layer_timeouts), 0, "comment_layer_timeout = 0/5 이어야 함")
        self.assertEqual(len(publish_calls), 5, "Gemini publish = 5/5 이어야 함")
        self.assertEqual(len(editor_readbacks), 5, "editor readback = 5/5 이어야 함")
        self.assertEqual(len(clicks_dispatched), 5, "submit click dispatched = 5/5 이어야 함")
        self.assertEqual(len(server_verified_calls), 5, "server verified = 5/5 이어야 함")
        self.assertEqual(state_mgr.state.processed_count, 5, "processed_count는 정확히 5이어야 함")

    def test_e2e_manual_enter_5_scenarios(self):
        """
        Section 10: Manual Enter Gate 5대 시나리오 실증
        A. Gemini 초안 그대로 Enter
        B. 사용자 1자 수정 후 Enter
        C. 한글 IME 수정 중 첫 Enter 조합확정 -> 두 번째 Enter 제출
        D. auto timer 직전(5.9초) Enter -> User Enter 우선
        E. auto timer 중 사용자 수정 -> timer disarm -> 후속 Enter 제출
        """
        from app.models import SubmitOrigin
        from naver.interaction import CommentInteractionService
        from services.like_transaction import LikeConfidence

        btn_mock = MagicMock(is_disabled=lambda: False)

        # Case A: Draft as-is
        mock_page_a = MagicMock(is_closed=lambda: False)
        mock_page_a.evaluate.return_value = {"dirty": False, "isComposing": False, "text": "돈까스 두께가 정말 바삭해보이네요~"}
        submit_ctx_a = {"button": btn_mock, "frame": mock_page_a}
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page_a}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx_a), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome_a = CommentInteractionService.submit_and_verify(
                mock_page_a,
                "돈까스 두께가 정말 바삭해보이네요~",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome_a.state, CommentSubmitState.SUBMITTED)
            self.assertTrue(outcome_a.click_dispatched)

        # Case B: User edit (dirty=True)
        mock_page_b = MagicMock(is_closed=lambda: False)
        mock_page_b.evaluate.return_value = {"dirty": True, "isComposing": False, "text": "수정본: 돈까스 밥 무한리필이라 푸짐하겠어요!"}
        submit_ctx_b = {"button": btn_mock, "frame": mock_page_b}
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page_b}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx_b), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            outcome_b = CommentInteractionService.submit_and_verify(
                mock_page_b,
                "수정본: 돈까스 밥 무한리필이라 푸짐하겠어요!",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome_b.state, CommentSubmitState.SUBMITTED)
            self.assertTrue(outcome_b.click_dispatched)

        # Case C: IME composing blocks pre-click and allows next Enter
        mock_page_c = MagicMock(is_closed=lambda: False)
        submit_ctx_c = {"button": btn_mock, "frame": mock_page_c}
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page_c}), \
             patch("naver.interaction.MobileDOMResolver.get_comment_submit_context", return_value=submit_ctx_c), \
             patch("naver.interaction.ServerCommentDuplicateGuard.capture_submission_baseline", return_value=None), \
             patch("naver.interaction.ServerCommentDuplicateGuard.scan_page_for_my_comment",
                   return_value=CommentPresenceResult(state=CommentPresenceState.PRESENT, confidence=LikeConfidence.HIGH)):
            # 1st call: isComposing=True -> PRECLICK_BLOCKED
            mock_page_c.evaluate.return_value = {"dirty": True, "isComposing": True, "text": "테스트"}
            outcome_c1 = CommentInteractionService.submit_and_verify(
                mock_page_c,
                "테스트",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome_c1.state, CommentSubmitState.PRECLICK_BLOCKED)
            self.assertTrue(outcome_c1.retryable_same_post)

            # 2nd call: isComposing=False -> SUBMITTED
            mock_page_c.evaluate.return_value = {"dirty": True, "isComposing": False, "text": "테스트 완료 돈까스 맛집이네요~"}
            outcome_c2 = CommentInteractionService.submit_and_verify(
                mock_page_c,
                "테스트 완료 돈까스 맛집이네요~",
                origin=SubmitOrigin.USER_ENTER,
            )
            self.assertEqual(outcome_c2.state, CommentSubmitState.SUBMITTED)

        # Case D: User Enter wins over auto timer
        mock_page_d = MagicMock(is_closed=lambda: False)
        mock_page_d.main_frame = mock_page_d
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page_d}), \
             patch.object(mock_page_d, "evaluate", return_value=["SUBMIT", False]):
            act_d = CommentInteractionService.wait_for_user_action(mock_page_d, timeout_seconds=6.0)
            self.assertEqual(act_d, UserAction.SUBMIT, "사용자 Enter가 auto timer보다 우선해야 함")

        # Case E: User edit disarms timer, manual enter submits
        mock_page_e = MagicMock(is_closed=lambda: False)
        mock_page_e.main_frame = mock_page_e
        poll_count = [0]
        def eval_e(script, *args):
            if "return [act, dirty]" in script:
                poll_count[0] += 1
                if poll_count[0] == 1:
                    return [None, True]  # user edit -> disarm
                return ["SUBMIT", True]  # subsequent Enter
            return None
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value={"frame": mock_page_e}), \
             patch.object(mock_page_e, "evaluate", side_effect=eval_e):
            act_e = CommentInteractionService.wait_for_user_action(mock_page_e, timeout_seconds=5.0)
            self.assertEqual(act_e, UserAction.SUBMIT, "타이머 해제 후 수동 Enter 제출이 수신되어야 함")


if __name__ == "__main__":
    unittest.main()


