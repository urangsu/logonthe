"""comment_context_selector.py
P1: 댓글 생성용 본문 맥락 선별 — 순수 Python, 브라우저·모델 호출 없음.

계획서 §5 구현: 문장 단위 선별, 감정/반전 묶음 보존, 예산 관리.
PostContext 호환 유지 — 기존 호출자와 fixture를 깨지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextUnit:
    """문장 단위 컨텍스트 유닛."""
    source_index: int          # 원문 문장 순서 (원문 순서 재조립 기준)
    text: str
    kind: str                  # event | fact | feeling | contrast | background | noise
    group_id: int              # 반전/원인-결과 묶음 ID (같은 번호는 함께 선택)
    weight: int = 0            # 선별 가중치


@dataclass(frozen=True)
class SelectedCommentContext:
    """select_comment_context 반환값."""
    excerpt: str                          # 선별·조립된 본문 발췌
    selected_indices: Tuple[int, ...]     # 선택된 ContextUnit.source_index
    mood_hint: Optional[str]              # achievement | regret | daily | info | mixed | None
    mood_evidence_indices: Tuple[int, ...]
    source_chars: int                     # 원본 총 길이
    selected_chars: int                   # 선택된 총 길이
    removed_counts: Dict[str, int]        # kind별 제거 수 통계
    needs_more_context: bool              # True면 확장 요청 필요


# ---------------------------------------------------------------------------
# 내부 상수
# ---------------------------------------------------------------------------

_KIND_WEIGHTS: Dict[str, int] = {
    "contrast": 4, "feeling": 3, "event": 3, "fact": 3, "background": 1, "noise": -99,
}

# 반전/부정 표지: 이 어구가 있으면 직전 문장과 묶는다
_CONTRAST_RE = re.compile(
    r"(?:하지만|그런데|그래도|생각보다|그럼에도|아쉽|아니"
    r"|못\s|안\s|없었|없어요|별로|만족스럽지|기대에\s*못|실망|아쉬웠"
    r"|아니었|아니에요|그러나|근데|반면|오히려|예상과\s*달리)",
    re.MULTILINE,
)

_FEELING_RE = re.compile(
    r"(?:기대|설레|감동|만족|실망|아쉬|슬프|기뻐|즐거|행복|좋았|좋아|힘들"
    r"|고생|뿌듯|다행|신기|놀라|궁금|그리워|그립|후회|어색)",
    re.MULTILINE,
)

# 독립 줄 전체가 보일러플레이트
_BOILERPLATE_RE = re.compile(
    r"^(?:안녕하세요|반갑습니다|오늘도\s*좋은\s*하루|구독|이웃추가|서이추\s*환영"
    r"|공감\s*\d*|댓글\s*\d*|공유하기|URL\s*복사|이웃\s*신청|블로그\s*앱으로"
    r"|저작자\s*명시|영리적\s*사용\s*불가|내용\s*변경\s*불가)[^가-힣]*$",
    re.IGNORECASE,
)

_MENU_RE = re.compile(
    r"^(?:공감|댓글|공유|이웃|구독|이전글|다음글|목록|전체보기"
    r"|블로그|카테고리|태그|통계|신고하기|인쇄)[^가-힣]{0,10}$",
)

_FACT_RE = re.compile(
    r"\d+(?:,\d+)?(?:원|개|분|km|m|층|인분|g|ml|%)"
    r"|(?:식감|맛있|비주얼|재료|메뉴|가격|분위기|인테리어|웨이팅)",
)

_EVENT_RE = re.compile(
    r"(?:했|했어|갔|먹었|봤|들었|만났|받았|생겼|됐|올랐|내렸|이겼"
    r"|완주|달성|드디어|처음으로|마지막으로)",
)


# ---------------------------------------------------------------------------
# 문장 분할
# ---------------------------------------------------------------------------

def _split_sentences(para: str) -> List[str]:
    """
    문단을 문장 단위로 분할한다.
    소수(3.5km), 날짜(2024.01.01), URL은 마침표에서 끊지 않는다.
    """
    # URL 보호
    text = re.sub(r"https?://\S+", lambda m: m.group().replace(".", "\ue000"), para)
    # 소수/날짜 보호: 숫자.숫자
    text = re.sub(r"(\d)\.(\d)", lambda m: m.group(1) + '\ue001' + m.group(2), text)

    parts = re.split(r"(?<=[.!?])\s+(?=[가-힣A-Z\"'])", text)
    result = []
    for p in parts:
        p = p.replace('\ue000', '.').replace('\ue001', '.').strip()
        if p:
            result.append(p)
    return result if result else ([para.strip()] if para.strip() else [])


# ---------------------------------------------------------------------------
# 분류
# ---------------------------------------------------------------------------

def _classify(text: str, source_index: int, group_counter: List[int]) -> ContextUnit:
    t = text.strip()
    if not t:
        return ContextUnit(source_index=source_index, text=t, kind="noise", group_id=-1, weight=-99)

    if _BOILERPLATE_RE.match(t) or _MENU_RE.match(t):
        return ContextUnit(source_index=source_index, text=t, kind="noise", group_id=-1, weight=-99)

    has_contrast = bool(_CONTRAST_RE.search(t))
    has_feeling = bool(_FEELING_RE.search(t))

    if has_contrast:
        gid = group_counter[0]
        group_counter[0] += 1
        return ContextUnit(source_index=source_index, text=t, kind="contrast", group_id=gid,
                           weight=_KIND_WEIGHTS["contrast"])
    if has_feeling:
        return ContextUnit(source_index=source_index, text=t, kind="feeling", group_id=-1,
                           weight=_KIND_WEIGHTS["feeling"])
    if _FACT_RE.search(t):
        return ContextUnit(source_index=source_index, text=t, kind="fact", group_id=-1,
                           weight=_KIND_WEIGHTS["fact"])
    if _EVENT_RE.search(t):
        return ContextUnit(source_index=source_index, text=t, kind="event", group_id=-1,
                           weight=_KIND_WEIGHTS["event"])
    return ContextUnit(source_index=source_index, text=t, kind="background", group_id=-1,
                       weight=_KIND_WEIGHTS["background"])


# ---------------------------------------------------------------------------
# 반전 묶음 처리
# ---------------------------------------------------------------------------

def _pair_contrast_with_prev(units: List[ContextUnit]) -> List[ContextUnit]:
    """contrast kind의 직전 유닛을 같은 group_id로 묶는다."""
    result = list(units)
    for i, u in enumerate(result):
        if u.kind == "contrast" and u.group_id >= 0 and i > 0:
            prev = result[i - 1]
            if prev.group_id < 0:
                result[i - 1] = ContextUnit(
                    source_index=prev.source_index,
                    text=prev.text,
                    kind=prev.kind,
                    group_id=u.group_id,
                    weight=prev.weight + 1,
                )
    return result


# ---------------------------------------------------------------------------
# 분위기 추론
# ---------------------------------------------------------------------------

def _infer_mood(units: List[ContextUnit]) -> Tuple[Optional[str], List[int]]:
    """분위기 힌트 추론. 확신 불가능하면 None 반환."""
    joined = " ".join(u.text for u in units)
    evidence_indices: List[int] = []

    has_achievement = bool(re.search(
        r"(?:드디어|처음으로|마지막으로|완주|달성|성공|합격|수상|기념)", joined))
    has_regret = bool(re.search(
        r"(?:아쉽|실망|별로|기대에\s*못|만족스럽지\s*않|아니었|좋지\s*않|힘들었|고생)", joined))
    has_info = sum(1 for u in units if u.kind == "fact") >= 2

    contrast_units = [u for u in units if u.kind == "contrast"]
    feeling_units = [u for u in units if u.kind == "feeling"]

    if contrast_units:
        evidence_indices = [u.source_index for u in contrast_units[:2]]
        if has_achievement and has_regret:
            return "mixed", evidence_indices
        if has_regret:
            return "regret", evidence_indices

    if has_achievement:
        return "achievement", [u.source_index for u in feeling_units[:2]]
    if has_regret:
        return "regret", [u.source_index for u in feeling_units[:2]]
    if has_info and not feeling_units:
        return "info", []
    if feeling_units:
        return "daily", [u.source_index for u in feeling_units[:2]]

    return None, []


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def select_comment_context(
    title: str,
    body: str,
    max_chars: int = 600,
) -> SelectedCommentContext:
    """
    블로그 본문에서 댓글 작성용 맥락을 선별한다.

    - 목표: 400~700자 발췌 (max_chars 기본 600)
    - 문장 중간 substring 절단 금지
    - 감정·반전 묶음은 함께 선택
    - 근거 부족 시 needs_more_context=True 반환 (호출자가 확장 결정)
    """
    if not body or not body.strip():
        return SelectedCommentContext(
            excerpt="", selected_indices=(), mood_hint=None,
            mood_evidence_indices=(), source_chars=0, selected_chars=0,
            removed_counts={}, needs_more_context=True,
        )

    title_s = title.strip() if title else ""
    source_chars = len(body)

    # 1. 문단 분리
    raw_paras = [p.strip() for p in re.split(r"[\r\n]+", body) if p.strip()]

    # 2. 문장 단위 분해 + 분류
    group_counter = [0]
    all_units: List[ContextUnit] = []
    sidx = 0
    for para in raw_paras:
        sentences = _split_sentences(para)
        for sent in sentences:
            unit = _classify(sent, sidx, group_counter)
            all_units.append(unit)
            sidx += 1

    if not all_units:
        return SelectedCommentContext(
            excerpt="", selected_indices=(), mood_hint=None,
            mood_evidence_indices=(), source_chars=source_chars, selected_chars=0,
            removed_counts={}, needs_more_context=True,
        )

    # 3. 반전 묶음 처리
    all_units = _pair_contrast_with_prev(all_units)

    # 4. 제목이 첫 문장과 완전 동일하면 noise로 처리
    if title_s and all_units and all_units[0].text.strip() == title_s:
        u0 = all_units[0]
        all_units[0] = ContextUnit(
            source_index=u0.source_index, text=u0.text,
            kind="noise", group_id=-1, weight=-99,
        )

    # 5. 예산 내 그룹 단위 선택
    selectable = [u for u in all_units if u.kind != "noise"]

    # 전체 예산 이내면 그대로 사용
    total_len = sum(len(u.text) + 1 for u in selectable)
    if total_len <= max_chars:
        selected_units = selectable
    else:
        # 그룹 단위로 묶기
        groups: Dict[int, List[ContextUnit]] = {}
        singletons: List[ContextUnit] = []
        for u in selectable:
            if u.group_id >= 0:
                groups.setdefault(u.group_id, []).append(u)
            else:
                singletons.append(u)

        candidates: List[Tuple[int, int, List[ContextUnit]]] = []
        for gid, g_units in groups.items():
            max_w = max(u.weight for u in g_units)
            g_len = sum(len(u.text) + 1 for u in g_units)
            candidates.append((max_w, g_len, g_units))
        for u in singletons:
            candidates.append((u.weight, len(u.text) + 1, [u]))

        candidates.sort(key=lambda x: x[0], reverse=True)

        selected_units = []
        accumulated = 0
        for weight, g_len, units in candidates:
            if accumulated + g_len > max_chars:
                continue
            selected_units.extend(units)
            accumulated += g_len

    # 의미 근거 기반 needs_more 판단: background만 있으면 근거 부족으로 처리
    meaningful = [
        u for u in selected_units
        if u.kind in {"fact", "event", "feeling", "contrast"}
    ]
    needs_more = not meaningful

    # 원문 순서로 재조립
    selected_units.sort(key=lambda u: u.source_index)
    excerpt = "\n".join(u.text for u in selected_units).strip()
    selected_indices = tuple(u.source_index for u in selected_units)

    # 분위기 추론
    mood_hint, mood_evidence_list = _infer_mood(selected_units)

    # 제거 통계
    removed_counts: Dict[str, int] = {}
    selected_set = set(selected_indices)
    for u in all_units:
        if u.source_index not in selected_set:
            removed_counts[u.kind] = removed_counts.get(u.kind, 0) + 1

    return SelectedCommentContext(
        excerpt=excerpt,
        selected_indices=selected_indices,
        mood_hint=mood_hint,
        mood_evidence_indices=tuple(mood_evidence_list),
        source_chars=source_chars,
        selected_chars=len(excerpt),
        removed_counts=removed_counts,
        needs_more_context=needs_more,
    )
