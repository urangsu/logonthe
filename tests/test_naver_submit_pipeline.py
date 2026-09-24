"""
tests/test_naver_submit_pipeline.py

계획서 §10: Naver submit pipeline normalize 통일 회귀 테스트.
interaction.py / editor_adapter.py 경로 검증.
실 브라우저 없이 MagicMock으로 검증한다.
"""
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

from services.draft import normalize_naver_comment_text
from naver.editor_adapter import CommentEditorAdapter


# ---------------------------------------------------------------------------
# §2, §3, §4: normalize_naver_comment_text 경로 통일 계약
# ---------------------------------------------------------------------------

class TestNormalizeTextComparisons(unittest.TestCase):
    """interaction.py 비교 로직과 동일 기준 단위 테스트."""

    def _is_same(self, a: str, b: str) -> bool:
        return normalize_naver_comment_text(a) == normalize_naver_comment_text(b)

    def test_double_newline_vs_single_is_same(self):
        """expected A\nB, editor A\n\nB → text_mutation 아님."""
        self.assertTrue(self._is_same("A\nB", "A\n\nB"))

    def test_crlf_vs_lf_is_same(self):
        """CRLF vs LF → 동일."""
        self.assertTrue(self._is_same("A\nB", "A\r\nB"))

    def test_zero_width_added_is_same(self):
        """zero-width space 삽입 → 동일."""
        self.assertTrue(self._is_same("주차 됩니다", "주차\u200B 됩니다"))

    def test_nbsp_is_same(self):
        """NBSP → 동일."""
        self.assertTrue(self._is_same("주차 됩니다", "주차\u00A0됩니다"))

    def test_negation_word_is_mismatch(self):
        """부정어 변경 → text_mutation."""
        self.assertFalse(self._is_same("주차 안 됩니다", "주차 됩니다"))

    def test_number_change_is_mismatch(self):
        """숫자 변경 → text_mutation."""
        self.assertFalse(self._is_same("13,900원", "12,900원"))

    def test_emoji_change_is_mismatch(self):
        """이모지 변경 → 불일치."""
        self.assertFalse(self._is_same("맛있어요 😊", "맛있어요 😢"))

    def test_multiple_newlines_collapsed(self):
        """연속 개행 → 1개로 축약 (한 줄 띄우기 금지)."""
        result = normalize_naver_comment_text("문장1\n\n\n문장2")
        self.assertEqual(result, "문장1\n문장2")

    def test_compose_uses_single_newline(self):
        """compose_body_and_suffix은 \\n 하나만 사용해야 함."""
        from services.draft import DraftService
        result = DraftService.compose_body_and_suffix("본문", "좋은 하루 되세요")
        self.assertNotIn("\n\n", result)
        self.assertIn("\n", result)


# ---------------------------------------------------------------------------
# §4: _get_editor_state() rawText / text 분리 계약
# ---------------------------------------------------------------------------

class TestGetEditorStateNormalized(unittest.TestCase):
    def _make_frame(self, inner_text: str, lock: bool = True) -> MagicMock:
        frame = MagicMock()
        frame.evaluate.return_value = {
            "visible": True,
            "rawText": inner_text,
            "lockHeld": lock,
            "dirty": False,
        }
        return frame

    def test_text_field_is_normalized(self):
        from naver.interaction import CommentInteractionService
        frame = self._make_frame("본문\n\n마무리")
        result = CommentInteractionService._get_editor_state(frame)
        self.assertEqual(result["text"], "본문\n마무리")

    def test_rawtext_field_preserved(self):
        from naver.interaction import CommentInteractionService
        frame = self._make_frame("본문\r\n마무리")
        result = CommentInteractionService._get_editor_state(frame)
        self.assertEqual(result["rawText"], "본문\r\n마무리")

    def test_none_frame_returns_empty(self):
        from naver.interaction import CommentInteractionService
        result = CommentInteractionService._get_editor_state(None)
        self.assertFalse(result["visible"])
        self.assertEqual(result["text"], "")
        self.assertEqual(result["rawText"], "")


# ---------------------------------------------------------------------------
# §3: read_final_text() 정규화 계약
# ---------------------------------------------------------------------------

class TestReadFinalText(unittest.TestCase):
    def test_saved_text_normalized(self):
        from naver.interaction import CommentInteractionService
        page = MagicMock()
        context = {
            "frame": MagicMock(),
            "editor": MagicMock(),
            "selector": "#editor",
            "frame_name": "main",
            "frame_url": "https://m.blog.naver.com",
        }
        context["frame"].evaluate.return_value = "본문\n\n마무리"
        with patch("naver.interaction.MobileDOMResolver.get_comment_editor_context", return_value=context):
            result = CommentInteractionService.read_final_text(page)
        self.assertEqual(result, "본문\n마무리")


# ---------------------------------------------------------------------------
# §6: submit button polling — 200ms 후 활성화 / timeout까지 비활성
# ---------------------------------------------------------------------------

class TestSubmitButtonPolling(unittest.TestCase):
    def _base_ctx(self, frame, editor):
        return {
            "frame": frame,
            "editor": editor,
            "selector": "#editor",
            "frame_name": "main",
            "frame_url": "https://m.blog.naver.com",
        }

    def test_button_enabled_after_delay_succeeds(self):
        """버튼이 처음 비활성이었다가 2회 polling 후 활성화되면 True."""
        page = MagicMock()
        frame = MagicMock()
        editor = MagicMock()
        editor.evaluate.side_effect = lambda s, *a: "div" if "tagName" in s else None
        text = "정상 댓글입니다"
        editor.inner_text.return_value = text

        call_count = {"n": 0}

        def _submit_ctx(page_arg, frame_arg=None):
            btn = MagicMock()
            call_count["n"] += 1
            btn.is_disabled.return_value = call_count["n"] <= 2
            return {"button": btn, "frame": frame, "frame_name": "main", "selector": ".btn"}

        with patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context",
                   return_value=self._base_ctx(frame, editor)), \
             patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context",
                   side_effect=_submit_ctx):
            result = CommentEditorAdapter._verify_and_confirm(
                editor, text, is_textarea=False, frame=frame, page=page
            )
        self.assertTrue(result)

    def test_button_timeout_disabled_fails(self):
        """버튼이 timeout까지 계속 비활성이면 False."""
        page = MagicMock()
        frame = MagicMock()
        editor = MagicMock()
        editor.evaluate.side_effect = lambda s, *a: "div" if "tagName" in s else None
        text = "정상 댓글입니다"
        editor.inner_text.return_value = text

        def _submit_ctx_disabled(page_arg, frame_arg=None):
            btn = MagicMock()
            btn.is_disabled.return_value = True
            return {"button": btn, "frame": frame, "frame_name": "main", "selector": ".btn"}

        with patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context",
                   return_value=self._base_ctx(frame, editor)), \
             patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context",
                   side_effect=_submit_ctx_disabled):
            result = CommentEditorAdapter._verify_and_confirm(
                editor, text, is_textarea=False, frame=frame, page=page
            )
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# §7: editor re-resolve — stale locator 대비
# ---------------------------------------------------------------------------

class TestEditorReResolve(unittest.TestCase):
    def test_stale_editor_resolved_to_fresh(self):
        """stale editor가 에러를 내도 fresh locator로 재연결 후 성공."""
        page = MagicMock()
        frame = MagicMock()
        text = "재렌더 후 텍스트"

        stale_editor = MagicMock()
        stale_editor.evaluate.side_effect = lambda s, *a: "div" if "tagName" in s else None
        stale_editor.inner_text.side_effect = Exception("Stale locator")

        fresh_editor = MagicMock()
        fresh_editor.evaluate.side_effect = lambda s, *a: "div" if "tagName" in s else None
        fresh_editor.inner_text.return_value = text

        resolve_calls = {"n": 0}

        def _get_editor_ctx(page_arg):
            resolve_calls["n"] += 1
            ed = fresh_editor if resolve_calls["n"] >= 2 else stale_editor
            return {
                "frame": frame,
                "editor": ed,
                "selector": "#editor",
                "frame_name": "main",
                "frame_url": "https://m.blog.naver.com",
            }

        btn = MagicMock()
        btn.is_disabled.return_value = False

        with patch("naver.editor_adapter.MobileDOMResolver.get_comment_editor_context",
                   side_effect=_get_editor_ctx), \
             patch("naver.editor_adapter.MobileDOMResolver.get_comment_submit_context") as ms:
            ms.return_value = {"button": btn, "frame": frame, "frame_name": "main", "selector": ".btn"}
            result = CommentEditorAdapter._verify_and_confirm(
                stale_editor, text, is_textarea=False, frame=frame, page=page
            )

        self.assertTrue(result)
        self.assertGreaterEqual(resolve_calls["n"], 2)


# ---------------------------------------------------------------------------
# §2: AUTO_TIMER pre-check: normalize 비교 기준
# ---------------------------------------------------------------------------

class TestAutoTimerNormalize(unittest.TestCase):
    def test_double_newline_in_editor_not_text_mutation(self):
        expected_norm = normalize_naver_comment_text("A\nB")
        editor_norm = normalize_naver_comment_text("A\n\nB")
        self.assertEqual(expected_norm, editor_norm)

    def test_negation_change_is_text_mutation(self):
        expected_norm = normalize_naver_comment_text("주차 안 됩니다")
        editor_norm = normalize_naver_comment_text("주차 됩니다")
        self.assertNotEqual(expected_norm, editor_norm)

    def test_number_change_is_text_mutation(self):
        expected_norm = normalize_naver_comment_text("13,900원")
        editor_norm = normalize_naver_comment_text("12,900원")
        self.assertNotEqual(expected_norm, editor_norm)


# ---------------------------------------------------------------------------
# §16: StateManager.update() gemini_phase 연결 계약
# ---------------------------------------------------------------------------

class TestStateManagerGeminiPhase(unittest.TestCase):
    def test_gemini_phase_update(self):
        from app.state import StateManager
        mgr = StateManager()
        mgr.update(
            gemini_phase="VERIFYING_INPUT",
            gemini_failure_count=1,
            gemini_failure_code="prompt_exact_readback_failed",
        )
        snap = mgr.get_snapshot()
        self.assertEqual(snap.gemini_phase, "VERIFYING_INPUT")
        self.assertEqual(snap.gemini_failure_count, 1)
        self.assertEqual(snap.gemini_failure_code, "prompt_exact_readback_failed")

    def test_gemini_phase_reset(self):
        from app.state import StateManager
        mgr = StateManager()
        mgr.update(gemini_phase="WAITING_RESPONSE", gemini_failure_count=2)
        mgr.update(gemini_phase="", gemini_failure_count=0, gemini_failure_code="")
        snap = mgr.get_snapshot()
        self.assertEqual(snap.gemini_phase, "")
        self.assertEqual(snap.gemini_failure_count, 0)


if __name__ == "__main__":
    unittest.main()
