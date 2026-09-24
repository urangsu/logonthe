"""
tests/test_comment_context_selector.py
comment_context_selector 단위 테스트 — 계획서 §9.B 검증 매트릭스 일부.
"""
import pytest
from services.comment_context_selector import select_comment_context


# ---------------------------------------------------------------------------
# 기본 동작
# ---------------------------------------------------------------------------

def test_empty_body_returns_needs_more():
    result = select_comment_context("제목", "")
    assert result.needs_more_context is True
    assert result.excerpt == ""


def test_short_body_within_budget_returns_full():
    body = "오늘 드디어 100번째 글이에요."
    result = select_comment_context("기념", body, max_chars=600)
    assert "100번째" in result.excerpt
    assert result.needs_more_context is False


# ---------------------------------------------------------------------------
# 인사말 제거 (§9.B: 독립 인사만 제거, 성취는 보존)
# ---------------------------------------------------------------------------

def test_standalone_greeting_removed():
    body = "안녕하세요.\n오늘 드디어 100번째 글이에요."
    result = select_comment_context("기념", body)
    # 성취 내용("100번째")은 보존
    assert "100번째" in result.excerpt

def test_greeting_with_fact_preserved():
    """인사와 사실이 한 문장에 있으면 사실 부분 보존 (§5.2 #2)."""
    body = "안녕하세요. 드디어 마라톤 완주했어요!"
    result = select_comment_context("완주", body)
    # "드디어 마라톤 완주" 가 살아있어야 함
    assert result.excerpt  # 아예 비어있으면 안 됨


# ---------------------------------------------------------------------------
# 반전·부정 묶음 보존 (§9.B: '기대했어요. 하지만 별로였어요')
# ---------------------------------------------------------------------------

def test_contrast_pair_preserved_together():
    body = "음식이 정말 기대됐어요.\n하지만 생각보다 별로였어요."
    result = select_comment_context("후기", body, max_chars=600)
    # 긍정(기대)과 부정(별로) 둘 다 살아있어야 함 — 한 쪽만 있으면 왜곡된 칭찬 댓글이 생성됨
    has_setup = "기대" in result.excerpt
    has_contrast = "별로" in result.excerpt or "하지만" in result.excerpt
    assert has_setup, f"setup 문장이 없음: excerpt={result.excerpt!r}"
    assert has_contrast, f"contrast 문장이 없음: excerpt={result.excerpt!r}"


def test_negation_word_not_stripped():
    body = "기대했는데 아쉬웠어요."
    result = select_comment_context("후기", body)
    assert "아쉬" in result.excerpt


# ---------------------------------------------------------------------------
# 짧은 감정·부정 문장 보존 (§9.B)
# ---------------------------------------------------------------------------

def test_short_negative_sentence_kept():
    body = "맛집이에요.\n아쉬웠어요."
    result = select_comment_context("맛집 후기", body)
    assert "아쉬" in result.excerpt


# ---------------------------------------------------------------------------
# 예산 준수 (§5.2 #7: substring 절단 없음)
# ---------------------------------------------------------------------------

def test_no_substring_truncation():
    # 각 문장이 100자인 긴 글에서 선택된 텍스트가 문장 경계에서만 끊어지는지 확인
    sentences = [f"이것은 {i}번째 문장으로 적당한 길이를 가진 예시 문장입니다." for i in range(10)]
    body = "\n".join(sentences)
    result = select_comment_context("긴 글", body, max_chars=300)
    # 선택된 excerpt가 원문 문장과 정확히 일치하는지 (중간 절단 없음)
    for line in result.excerpt.split("\n"):
        line = line.strip()
        if line:
            assert any(line == s for s in sentences), f"잘린 문장 발견: {line!r}"


# ---------------------------------------------------------------------------
# 분위기 힌트 추론
# ---------------------------------------------------------------------------

def test_achievement_mood():
    body = "드디어 목표를 달성했어요! 정말 뿌듯합니다."
    result = select_comment_context("달성", body)
    # 명확한 성취 fixture: achievement가 기대됨 (daily 허용, None은 불가)
    assert result.mood_hint in ("achievement", "daily"), (
        f"achievement fixture에서 mood_hint={result.mood_hint!r} 반환됨"
    )


def test_regret_mood():
    body = "맛집이라 기대했는데 실망스러웠어요."
    result = select_comment_context("후기", body)
    # 명확한 후회/실망 fixture: None 허용 안 됨
    assert result.mood_hint in ("regret", "mixed"), (
        f"regret fixture에서 mood_hint={result.mood_hint!r} 반환됨"
    )


def test_no_forced_mood_for_ambiguous():
    body = "날씨가 좋아서 산책을 나갔어요."
    result = select_comment_context("일상", body)
    # 억지 분류 없음: daily 또는 None
    assert result.mood_hint in ("daily", "info", None)


# ---------------------------------------------------------------------------
# 선택 인덱스 원문 순서 대응 (§9.B)
# ---------------------------------------------------------------------------

def test_selected_indices_in_source_order():
    body = "첫 번째 문장입니다.\n두 번째로 기대됐어요.\n세 번째 아쉬운 결과였어요."
    result = select_comment_context("테스트", body)
    indices = list(result.selected_indices)
    assert indices == sorted(indices), "선택 인덱스가 원문 순서가 아님"


# ---------------------------------------------------------------------------
# 통계 필드
# ---------------------------------------------------------------------------

def test_removed_counts_populated():
    body = "안녕하세요.\n오늘 맛있는 케이크를 먹었어요.\n다음에 또 오고 싶어요."
    result = select_comment_context("카페", body)
    # noise 또는 background 제거 있을 수 있음
    assert isinstance(result.removed_counts, dict)
    assert result.source_chars > 0


# ---------------------------------------------------------------------------
# needs_more_context: background만 있으면 근거 부족
# ---------------------------------------------------------------------------

def test_background_only_body_needs_more_context():
    """모든 문장이 background로 분류되면 needs_more_context=True여야 함."""
    # 사실/이벤트/감정/반전 표현 없는 단순 배경 문장
    body = "날씨가 맑은 날이었습니다.\n하늘이 파랗게 펼쳐져 있었습니다.\n바람도 살랑살랑 불었습니다."
    result = select_comment_context("일상", body)
    assert result.needs_more_context is True, (
        f"background만 있는데 needs_more_context=False: excerpt={result.excerpt!r}"
    )


def test_fact_body_not_needs_more_context():
    """사실 정보가 포함된 본문은 needs_more_context=False여야 함."""
    body = "아메리카노 3,500원에 조각케이크 5,800원이었어요.\n재료도 신선해 보였습니다."
    result = select_comment_context("카페 후기", body)
    assert result.needs_more_context is False, (
        f"fact가 있는데 needs_more_context=True: excerpt={result.excerpt!r}"
    )
