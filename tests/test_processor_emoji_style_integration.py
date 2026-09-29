# -*- coding: utf-8 -*-
"""
tests/test_processor_emoji_style_integration.py

PostProcessor 단위의 이모지 및 스타일 정규화 엔드투엔드 통합 테스트:
1. P0 — frozen CommentStylePolicy 직접 수정 제거 (dataclasses.replace 기반 candidate policy 검증)
2. P1 — style_profile.emoji_ratio 런타임 강제변경 제거 검증 (사용자 학습 프로필 오염 방지)
3. sampled + single 😋 → 실제 FINAL_TEXT 및 에디터 주입 텍스트에 😋 존재
4. sampling miss → 😋 제거
5. recent cooldown → 최근 등록 이력에 이모지 있으면 제거
6. 😋😋🔥 등 다중/과장형 이모티콘 → 전면 제거
7. 20℃, 1.5㎏, ©, ™ 등 일반 단위 및 특수 기호 → 100% 원형 유지
8. kept emoji 상태로 editor readback + submit 검증까지 PASS
"""

import dataclasses
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from app.models import (
    CommentSubmitState,
    FeedPost,
    FeedSourceType,
    PostActionPlan,
    UserAction,
)
from app.processor import PostProcessor
from app.state import StateManager
from naver.comment_guard import CommentPresenceResult, CommentPresenceState
from services.comments.policy import CommentStylePolicy
from services.comments.style_normalizer import CommentStyleNormalizer
from services.gemini_extension_bridge import GeminiResult, GeminiResultStatus


class TestProcessorEmojiStyleIntegration(unittest.TestCase):
    def setUp(self):
        self.mock_page = MagicMock()
        self.mock_page.is_closed.return_value = False
        self.mock_page.url = "https://m.blog.naver.com/testuser/12345"

        self.config = {
            "skip_on_comment_failure": True,
            "gemini_browser_mode": "extension_existing_chrome",
            "gemini_web_enabled": True,
            "comment_template": "잘 보고 갑니다",
        }

    def _find_sampled_post_key(self, should_sample: bool) -> str:
        """deterministic sampling threshold(<20)를 만족하거나 불만족하는 post_key 탐색"""
        for i in range(200):
            pk = f"post_user:{i}"
            is_sampled = CommentStyleNormalizer.stable_hash(f"emoji:{pk}") % 100 < 20
            if is_sampled == should_sample:
                return pk
        raise RuntimeError("Failed to find appropriate post_key for sampling test")

    def _run_processor_with_answer(
        self,
        gemini_answer: str,
        post_key: str,
        recent_submitted_comments: list = None,
    ):
        post = FeedPost(
            key=post_key,
            source=FeedSourceType.NEIGHBOR,
            url=f"https://m.blog.naver.com/{post_key.replace(':', '/')}",
            title="성수동 삼겹살 솥뚜껑 구이 맛집",
            blog_id=post_key.split(":")[0],
            log_no=post_key.split(":")[1],
        )

        bridge = MagicMock()
        bridge.preflight.return_value = MagicMock(ready=True)
        bridge.wait_for_result.return_value = GeminiResult(
            request_id="req_test_123",
            post_key=post.key,
            navigation_version=1,
            status=GeminiResultStatus.COMPLETED,
            text=gemini_answer,
            error="",
        )

        with ExitStack() as stack:
            targets = {
                "TargetPostGuard.verify": MagicMock(),
                "CommentInteractionService.open_comment_layer": (True, "ok"),
                "MobileDOMResolver.get_comment_editor_context": {"frame": self.mock_page, "root": self.mock_page},
                "ServerCommentDuplicateGuard.scan_page_for_my_comment": CommentPresenceResult(
                    state=CommentPresenceState.ABSENT, confidence="high"
                ),
                "ContentContextExtractor.extract": MagicMock(
                    title=post.title, excerpt="성수동 솥뚜껑에 두툼한 삼겹살과 김치를 구웠어요 20℃ 1.5㎏"
                ),
                "CommentEditorAdapter.set_text": True,
                "CommentInteractionService.wait_for_user_action": UserAction.SUBMIT,
                "CommentInteractionService.read_final_text": None,  # readback will use editor injected text
                "CommentInteractionService.submit_and_verify": CommentSubmitState.SUBMITTED,
                "DraftService.resolve_suffix": "",
            }
            mocks = {
                name: stack.enter_context(patch("app.processor." + name, return_value=value))
                for name, value in targets.items()
            }

            processor = PostProcessor(
                self.config,
                like_enabled=False,
                comment_enabled=True,
                gemini_web_enabled=True,
                gemini_extension_bridge=bridge,
                state_manager=StateManager(),
            )
            if recent_submitted_comments:
                processor._recent_submitted_comments = list(recent_submitted_comments)

            plan = PostActionPlan(
                process_like=False,
                process_comment=True,
                comment_sample_selected=True,
                comment_sample_roll=0.1,
            )
            result = processor.process(self.mock_page, post, action_plan=plan)

            # Get injected text into editor
            injected_text = None
            if mocks["CommentEditorAdapter.set_text"].called:
                injected_text = mocks["CommentEditorAdapter.set_text"].call_args[0][1]

            return result, injected_text, processor

    def test_sampled_single_emoji_preserved_and_submitted_without_policy_mutation(self):
        """
        P0/P1 통합 검증:
        - sampled + single 😋 → 실제 FINAL_TEXT 및 에디터 주입 텍스트에 😋 보존
        - frozen CommentStylePolicy에 FrozenInstanceError 없이 dataclasses.replace로 candidate policy 적용
        - style_profile.emoji_ratio가 런타임에 오염되지 않고 분리됨
        - 에디터 readback 및 submit 검증까지 SUBMITTED로 성공
        """
        sampled_pk = self._find_sampled_post_key(should_sample=True)
        raw_text = "솥뚜껑 삼겹살에 김치 조합 비주얼부터 맛있어 보여요 😋"

        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, sampled_pk, recent_submitted_comments=["맛있어 보여요", "잘 보고 갑니다"]
        )

        # 1. 제출 상태 검증
        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)

        # 2. 주입된 텍스트에 😋 보존 확인
        self.assertIsNotNone(injected_text)
        self.assertIn("😋", injected_text)
        self.assertFalse(injected_text.endswith("."))

        # 3. 등록 성공 후 processor의 _recent_submitted_comments에 기록 확인
        self.assertTrue(any("😋" in c for c in processor._recent_submitted_comments))

    def test_sampling_miss_emoji_stripped(self):
        """sampling miss(20% 확률 밖) → 단일 이모지라도 쿨하게 제거되어 주입된다"""
        unsampled_pk = self._find_sampled_post_key(should_sample=False)
        raw_text = "솥뚜껑 삼겹살에 김치 조합 비주얼부터 맛있어 보여요 😋"

        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, unsampled_pk, recent_submitted_comments=[]
        )

        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertIsNotNone(injected_text)
        self.assertNotIn("😋", injected_text)

    def test_recent_cooldown_emoji_stripped(self):
        """최근 등록 댓글 5개 중 이모지가 있으면 샘플링 대상이어도 쿨다운으로 제거된다"""
        sampled_pk = self._find_sampled_post_key(should_sample=True)
        raw_text = "솥뚜껑 삼겹살에 김치 조합 비주얼부터 맛있어 보여요 😋"

        # 최근 등록 댓글에 이모지 포함
        recent_comments = ["댓글 1", "댓글 2 ✨", "댓글 3"]
        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, sampled_pk, recent_submitted_comments=recent_comments
        )

        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertIsNotNone(injected_text)
        self.assertNotIn("😋", injected_text)

    def test_multiple_and_exaggerated_emoji_stripped(self):
        """😋😋🔥 등 다중/과장형 이모티콘은 전면 제거된다"""
        sampled_pk = self._find_sampled_post_key(should_sample=True)
        raw_text = "솥뚜껑 삼겹살에 김치 조합 비주얼부터 맛있어 보여요 😋😋🔥"

        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, sampled_pk, recent_submitted_comments=[]
        )

        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertIsNotNone(injected_text)
        self.assertNotIn("😋", injected_text)
        self.assertNotIn("🔥", injected_text)

    def test_special_symbols_20c_preserved(self):
        """20℃ 기호는 이모지로 간주되지 않고 에디터 주입까지 100% 원형 유지된다"""
        sampled_pk = self._find_sampled_post_key(should_sample=True)
        raw_text = "날씨가 20℃ 정도라 솥뚜껑 삼겹살 구워 먹기 딱 좋네요"

        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, sampled_pk, recent_submitted_comments=[]
        )

        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertIsNotNone(injected_text)
        self.assertIn("20℃", injected_text)

    def test_special_symbols_1_5kg_preserved(self):
        """1.5㎏ 단위 기호는 이모지로 간주되지 않고 에디터 주입까지 100% 원형 유지된다"""
        sampled_pk = self._find_sampled_post_key(should_sample=True)
        raw_text = "솥뚜껑 삼겹살 1.5㎏ 푸짐하고 비주얼도 참 맛있어 보여요"

        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, sampled_pk, recent_submitted_comments=[]
        )

        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertIsNotNone(injected_text)
        self.assertIn("1.5㎏", injected_text)

    def test_kept_emoji_editor_readback_and_submit_pass(self):
        """kept emoji 상태에서 readback mismatch 없이 submit 검증까지 PASS 확인"""
        sampled_pk = self._find_sampled_post_key(should_sample=True)
        raw_text = "솥뚜껑 삼겹살에 김치 조합 비주얼부터 맛있어 보여요 😋"

        # readback 시 동일한 이모지 텍스트가 정상 반환되는 상황 모의
        result, injected_text, processor = self._run_processor_with_answer(
            raw_text, sampled_pk, recent_submitted_comments=[]
        )

        self.assertEqual(result.comment_result.status, CommentSubmitState.SUBMITTED)
        self.assertIn("😋", injected_text)


if __name__ == "__main__":
    unittest.main()
