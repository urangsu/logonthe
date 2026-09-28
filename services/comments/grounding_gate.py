# -*- coding: utf-8 -*-
"""
services/comments/grounding_gate.py

CommentGroundingGate: AI 댓글에서 본문에 근거 없는 구체적 사실 주장을 차단한다.

역할:
    - 문장 전체의 lexical copy 여부를 검사하는 것이 아님.
    - AI 댓글에 새로 추가된 "구체적인 사실성 content claim"이 원문(excerpt)에 근거하는지 확인.
    - 감상/관심 표현("멋지네요", "궁금하네요")은 정확한 lexical 근거가 없어도 허용.
    - 검증 가능한 구체 사실(시설·기능·서비스 단계·장소 특성)은 반드시 context 근거 필요.

파이프라인 위치:
    Gemini raw → ResponseContaminationGate → CommentDraftInspector
    → FinalQualityGate → CommentGroundingGate ← 여기 → FoodRelevance → Final approval
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 결과 데이터 클래스
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroundingResult:
    valid: bool
    code: str          # "ok" | "unsupported_content_claim"
    reason: str
    unsupported_terms: Tuple[str, ...] = field(default_factory=tuple)
    supported_terms: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.valid

    @property
    def violation_category(self) -> Optional[str]:
        return None if self.valid else "fact_violation"

    def rewrite_feedback(self, domain: str = "GENERAL") -> str:
        """재작성 피드백 메시지 생성."""
        terms_str = ", ".join(f"'{t}'" for t in self.unsupported_terms)
        if domain == "PLACE":
            return (
                f"이전 댓글에 본문에서 확인되지 않은 구체적인 장소·시설·풍경 표현"
                f"({terms_str})이 포함되어 있습니다. "
                "본문에 실제로 나온 장소·풍경·시설만 사용하고, "
                "없는 장면이나 시설을 추가하지 말고 다시 작성해 주세요."
            )
        elif domain == "SERVICE":
            return (
                f"이전 댓글에 본문에서 확인되지 않은 서비스 단계·관리 항목"
                f"({terms_str})이 포함되어 있습니다. "
                "실제로 본문에서 확인되는 관리 구성만 사용해 다시 작성해 주세요."
            )
        elif domain == "PRODUCT":
            return (
                f"이전 댓글에 본문에 없는 제품 기능·스펙"
                f"({terms_str})을 추가했습니다. "
                "본문에 명시된 기능이나 디자인만 사용해 다시 작성해 주세요."
            )
        else:
            return (
                f"이전 댓글에 본문에서 확인되지 않은 구체적인 내용"
                f"({terms_str})이 포함되어 있습니다. "
                "본문에 실제로 언급된 사실만 사용해 다시 작성해 주세요."
            )


# ---------------------------------------------------------------------------
# Alias 정규화 맵 (alias → excerpt에서 찾을 대체 패턴들)
# "오션뷰"가 댓글에 있을 때, 원문에 "바다 전망" 등이 있으면 허용.
# ---------------------------------------------------------------------------

GROUNDING_ALIASES: Dict[str, Tuple[str, ...]] = {
    # PLACE
    "오션뷰": ("바다 전망", "바다가 보이", "바다를 바라보", "바다 뷰", "바다뷰"),
    "야경": ("밤 풍경", "밤바다", "불빛", "야간 풍경"),
    "산책로": ("산책길", "걷는 길", "둘레길", "산책로"),
    "해안도로": ("해안도로", "해안 도로", "해안길"),
    "통창": ("통창", "전망창", "대형 창"),
    "루프탑": ("루프탑", "옥상", "옥상 테라스"),
    "테라스": ("테라스", "야외 테라스", "야외자리"),
    "오션테라스": ("오션 테라스", "테라스", "바다 테라스"),
    "포토존": ("포토존", "사진 명소", "사진 스팟"),
    # PRODUCT
    "노이즈캔슬링": ("노이즈캔슬링", "노캔", "소음 차단", "소음차단"),
    "무선충전": ("무선충전", "무선 충전"),
    "고속충전": ("고속충전", "빠른 충전"),
    "방수": ("방수",),
    "방진": ("방진",),
    "OLED": ("OLED", "올레드"),
    "블루투스": ("블루투스", "Bluetooth"),
    # SERVICE
    "족욕": ("족욕",),
    "개인실": ("개인실", "프라이빗 룸", "1인실"),
}


# ---------------------------------------------------------------------------
# 도메인별 검증 대상 용어 (댓글에 등장하면 원문에 근거가 있어야 함)
# ---------------------------------------------------------------------------

PLACE_CLAIM_WORDS: Tuple[str, ...] = (
    "통창", "해안도로", "루프탑", "테라스", "옥상", "잔디", "잔디밭",
    "수영장", "오션뷰", "마운틴뷰", "야경", "일출", "일몰", "포토존",
    "케이블카", "전망대", "정원", "호수", "계곡", "인피니티풀",
    "오션테라스", "스카이라운지",
)

SERVICE_CLAIM_WORDS: Tuple[str, ...] = (
    "1:1", "개인실", "족욕", "기계관리", "수기관리",
    "두피 진단", "두피진단", "마무리팩", "클리닉",
    "스케일링", "마취", "코팅", "유리막",
)

PRODUCT_CLAIM_WORDS: Tuple[str, ...] = (
    "무선충전", "고속충전", "방수", "방진", "OLED", "LCD",
    "노이즈캔슬링", "블루투스", "알루미늄", "USB-C", "저소음",
    "흡입력", "필터", "폴딩", "쿠션",
)

# 주관적 반응 표현 패턴 - 이 패턴들은 사실적 근거 없이도 허용
_SUBJECTIVE_REACTION_RE = re.compile(
    r"(?:멋지|예쁘|인상적|궁금|먹어보고\s*싶|가보고\s*싶|편해\s*보|좋아\s*보|맛있어\s*보|"
    r"대단|놀라|기대|기억에\s*남|아름답|황홀|짱|최고|대박|신기|멋있|푸짐해\s*보|"
    r"먹음직|기대되|기대가\s*되|기대\s*됩|어떤\s*맛|맛이\s*궁금|가고\s*싶)"
)

# 단순 감상어 (사실 결합 없는 단독 감상)
_PURE_SENTIMENT_WORDS = frozenset({
    "멋지네요", "멋지다", "예쁘네요", "예쁘다", "인상적이네요",
    "궁금하네요", "편해 보여요", "좋아 보여요",
})


def _resolve_claim_term(term: str, excerpt: str) -> bool:
    """
    term이 excerpt에 직접 or alias를 통해 뒷받침되는지 확인.
    True이면 근거 있음(허용). False이면 근거 없음(차단).
    """
    # 1) 직접 매칭 (대소문자 무시)
    if term.lower() in excerpt.lower():
        return True

    # 2) alias normalization
    aliases = GROUNDING_ALIASES.get(term, ())
    for alias in aliases:
        if alias.lower() in excerpt.lower():
            return True

    return False


def _extract_claim_terms(text: str, domain: str) -> List[str]:
    """
    댓글 text에서 해당 domain의 content claim 용어를 추출.
    주관적 반응이 claim 용어를 "설명"하는 경우에도 포함.

    예: "통창이라 전망이 멋지네요" → "통창" 추출
    """
    if domain == "PLACE":
        claim_vocab = PLACE_CLAIM_WORDS
    elif domain == "SERVICE":
        claim_vocab = SERVICE_CLAIM_WORDS
    elif domain == "PRODUCT":
        claim_vocab = PRODUCT_CLAIM_WORDS
    else:
        # GENERAL: 모든 도메인 합산
        claim_vocab = PLACE_CLAIM_WORDS + SERVICE_CLAIM_WORDS + PRODUCT_CLAIM_WORDS

    found = []
    for word in claim_vocab:
        if word in text:
            found.append(word)
    return found


# ---------------------------------------------------------------------------
# 메인 Gate
# ---------------------------------------------------------------------------

class CommentGroundingGate:
    """
    AI 댓글의 content claim이 원문(excerpt)에 근거하는지 검증한다.

    FOOD 도메인은 기존 unsupported_texture / unsupported_taste /
    food_relevance 전용 검사를 유지하므로 이 Gate에서 중복 검사하지 않는다.
    단, GENERAL 모드에서는 PLACE+SERVICE+PRODUCT 용어를 모두 검사한다.
    """

    @classmethod
    def validate(
        cls,
        text: str,
        context: str,
        *,
        domain: str = "GENERAL",
        reaction_plan=None,
    ) -> GroundingResult:
        """
        Parameters
        ----------
        text:
            최종 검사할 AI 생성 댓글 텍스트.
        context:
            선별된 본문 발췌(excerpt).
        domain:
            ReactionPlan.domain 값. FOOD는 기존 gate에서 처리하므로 early-pass.
        reaction_plan:
            선택적. 추가 메타데이터 참조용 (현재는 미사용).
        """
        if not text or not context:
            # 텍스트 또는 컨텍스트가 없으면 판단 불가 → 통과 처리 (안전 우선)
            return GroundingResult(
                valid=True,
                code="ok",
                reason="empty text or context: grounding check skipped",
            )

        # FOOD 도메인: 기존 Inspector + FoodRelevance에서 전담 → 패스
        if domain == "FOOD":
            return GroundingResult(
                valid=True,
                code="ok",
                reason="FOOD domain: delegated to food-specific gates",
            )

        # 사용자 직접 수정본은 면제
        # (reaction_plan에서 source 정보가 없으므로, 상위에서 is_user_source 체크 후 호출 안 할 것)

        # 댓글에서 claim 용어 추출
        claimed = _extract_claim_terms(text, domain)
        if not claimed:
            return GroundingResult(
                valid=True,
                code="ok",
                reason="no content claim terms found in comment",
                supported_terms=(),
            )

        # 각 claim 용어를 원문으로 검증
        supported = []
        unsupported = []
        for term in claimed:
            if _resolve_claim_term(term, context):
                supported.append(term)
            else:
                unsupported.append(term)

        if unsupported:
            return GroundingResult(
                valid=False,
                code="unsupported_content_claim",
                reason=(
                    f"댓글에 본문에서 확인되지 않는 구체적 사실 표현이 포함됨: "
                    f"{unsupported}"
                ),
                unsupported_terms=tuple(unsupported),
                supported_terms=tuple(supported),
            )

        return GroundingResult(
            valid=True,
            code="ok",
            reason="all content claims are grounded in excerpt",
            unsupported_terms=(),
            supported_terms=tuple(supported),
        )
