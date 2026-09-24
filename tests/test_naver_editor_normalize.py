"""
tests/test_naver_editor_normalize.py
P0-2 계획서 §21.A 검증 매트릭스: normalize_naver_comment_text 단위 테스트.
"""
import pytest
from services.draft import normalize_naver_comment_text, DraftService


# ---------------------------------------------------------------------------
# normalize_naver_comment_text
# ---------------------------------------------------------------------------

class TestNormalizeNaverCommentText:
    """정규화 허용/차단 매트릭스."""

    # --- 정규화 허용 ---

    def test_crlf_to_lf(self):
        assert normalize_naver_comment_text("본문\r\n마무리") == "본문\n마무리"

    def test_cr_only_to_lf(self):
        assert normalize_naver_comment_text("본문\r마무리") == "본문\n마무리"

    def test_nbsp_to_space(self):
        result = normalize_naver_comment_text("주차\u00A0가능해요")
        assert result == "주차 가능해요"

    def test_zero_width_removed(self):
        # zero-width space, zero-width no-break space(BOM)
        result = normalize_naver_comment_text("좋아\u200B요")
        assert result == "좋아요"
        result2 = normalize_naver_comment_text("\uFEFF좋아요")
        assert result2 == "좋아요"

    def test_double_newline_collapsed(self):
        """빈 DOM 문단(<p><br></p>)이 만드는 연속 개행은 1개로."""
        result = normalize_naver_comment_text("본문\n\n꼬리말")
        assert result == "본문\n꼬리말"

    def test_triple_newline_collapsed(self):
        result = normalize_naver_comment_text("본문\n\n\n꼬리말")
        assert result == "본문\n꼬리말"

    def test_trailing_whitespace_stripped(self):
        result = normalize_naver_comment_text("  본문  ")
        assert result == "본문"

    def test_trailing_space_before_newline(self):
        result = normalize_naver_comment_text("본문   \n마무리")
        assert result == "본문\n마무리"

    # --- 정규화 차단 ---

    def test_negation_preserved(self):
        """부정 표현은 절대 제거하지 않는다."""
        t = "주차 안 됩니다"
        assert normalize_naver_comment_text(t) == t

    def test_negation_differ_is_mismatch(self):
        """부정어 유무 차이 → 정규화 후에도 불일치."""
        a = normalize_naver_comment_text("주차 안 됩니다")
        b = normalize_naver_comment_text("주차 됩니다")
        assert a != b

    def test_numbers_preserved(self):
        result = normalize_naver_comment_text("가격이 13,900원이에요")
        assert result == "가격이 13,900원이에요"

    def test_emoji_preserved(self):
        result = normalize_naver_comment_text("맛있어요🍜")
        assert result == "맛있어요🍜"

    def test_word_space_preserved(self):
        """단어 사이 공백은 유지."""
        result = normalize_naver_comment_text("정말 맛있었어요")
        assert result == "정말 맛있었어요"

    def test_meaningful_punctuation_preserved(self):
        """의미 있는 문장 부호는 유지."""
        result = normalize_naver_comment_text("웨이팅? 생각보다 짧았어요~")
        assert result == "웨이팅? 생각보다 짧았어요~"

    def test_single_newline_unchanged(self):
        """단일 개행은 그대로."""
        result = normalize_naver_comment_text("본문\n꼬리말")
        assert result == "본문\n꼬리말"

    # --- +1 문자 시나리오 (계획서 핵심) ---

    def test_raw_plus_one_newline_normalized(self):
        """
        실제 로그 재현: expected=66, actual=67 (+1 개행)
        정규화 후 동일해야 한다.
        """
        expected = "맛있어 보여요~ 웨이팅이 있어도 도전해볼만 하겠네요\n좋은 하루 되세요"
        actual_dom = "맛있어 보여요~ 웨이팅이 있어도 도전해볼만 하겠네요\n\n좋은 하루 되세요"  # +1 빈 줄
        assert normalize_naver_comment_text(expected) == normalize_naver_comment_text(actual_dom)

    def test_raw_plus_one_crlf_normalized(self):
        expected = "오늘도 맛있는 하루 되세요\n좋은 하루 되세요"
        actual_crlf = "오늘도 맛있는 하루 되세요\r\n좋은 하루 되세요"
        assert normalize_naver_comment_text(expected) == normalize_naver_comment_text(actual_crlf)

    # --- 빈 문자열 처리 ---

    def test_empty_string(self):
        assert normalize_naver_comment_text("") == ""

    def test_whitespace_only(self):
        assert normalize_naver_comment_text("   \n  ") == ""

    def test_non_string_returns_empty(self):
        assert normalize_naver_comment_text(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compose_body_and_suffix: \n\n → \n 회귀
# ---------------------------------------------------------------------------

class TestComposeBodyAndSuffix:
    def test_single_newline_only(self):
        result = DraftService.compose_body_and_suffix("본문", "좋은 하루 되세요")
        assert result == "본문\n좋은 하루 되세요"
        assert "\n\n" not in result

    def test_no_suffix_returns_body(self):
        result = DraftService.compose_body_and_suffix("본문만", "")
        assert result == "본문만"

    def test_empty_body_returns_suffix(self):
        result = DraftService.compose_body_and_suffix("", "꼬리말만")
        assert result == "꼬리말만"

    def test_suffix_already_in_body_not_duplicated(self):
        result = DraftService.compose_body_and_suffix("본문에 꼬리말 포함", "꼬리말 포함")
        assert "꼬리말 포함" in result
        assert result.count("꼬리말 포함") == 1

    def test_normalized_result_matches_raw_plus_one_newline(self):
        """
        compose 결과(본문\\n꼬리말) 를 DOM이 본문\\n\\n꼬리말로 직렬화해도
        정규화 후 동일해야 한다.
        """
        composed = DraftService.compose_body_and_suffix("요즘 자주 가게 됩니다", "좋은 하루 되세요")
        dom_serialized = composed.replace("\n", "\n\n", 1)  # DOM 빈 문단 시뮬레이션
        assert normalize_naver_comment_text(composed) == normalize_naver_comment_text(dom_serialized)
