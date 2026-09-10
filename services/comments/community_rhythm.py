"""Shared final comment quality policy for the V13.1 community rhythm.

This module validates the text that is about to be submitted, regardless of
whether it came from Gemini, the local composer, a template, or the clipboard.
It deliberately does not clean, truncate, or otherwise rewrite candidate text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Tuple, Union


class CommunityRhythmPreset(str, Enum):
    COMMUNITY = "community"
    CALM = "calm"
    THOUGHTFUL = "thoughtful"


@dataclass(frozen=True)
class CommentLengthPolicy:
    """Length limits and scoring band for one comment style preset."""

    minimum: int
    target_max: int
    preferred_min: int
    preferred_max: int
    maximum: int

    @property
    def min_length(self) -> int:
        return self.minimum

    @property
    def max_length(self) -> int:
        return self.maximum

    @property
    def preferred_range(self) -> Tuple[int, int]:
        return self.preferred_min, self.preferred_max

    def score(self, length: int) -> float:
        """Return a small, deterministic length score in the range 0..1."""
        if length < self.minimum or length > self.maximum:
            return 0.0
        if self.preferred_min <= length <= self.preferred_max:
            return 1.0
        if length < self.preferred_min:
            span = self.preferred_min - self.minimum
            return 0.0 if span == 0 else (length - self.minimum) / span
        if length > self.target_max:
            return 0.0
        span = self.target_max - self.preferred_max
        return 0.0 if span == 0 else (self.target_max - length) / span

    def band(self, length: int) -> str:
        if length < self.minimum:
            return "below_minimum"
        if length > self.maximum:
            return "above_maximum"
        if self.preferred_min <= length <= self.preferred_max:
            return "preferred"
        if length > self.target_max:
            return "long"
        return "acceptable"


COMMENT_POLICIES: Dict[Union[CommunityRhythmPreset, str], CommentLengthPolicy] = {
    CommunityRhythmPreset.COMMUNITY: CommentLengthPolicy(
        minimum=16, target_max=48, preferred_min=18, preferred_max=45, maximum=100
    ),
    CommunityRhythmPreset.CALM: CommentLengthPolicy(
        minimum=20, target_max=65, preferred_min=20, preferred_max=65, maximum=100
    ),
    CommunityRhythmPreset.THOUGHTFUL: CommentLengthPolicy(
        minimum=25, target_max=100, preferred_min=30, preferred_max=80, maximum=160
    ),
}

# A profile used only by the compatibility adapter in PositiveSafetyValidator.
# It keeps that public API's historic 12-character and period behavior while
# retaining the old validator's safety checks. New final text must use one of
# the public community rhythm presets above.
LEGACY_COMMENT_POLICY = CommentLengthPolicy(
    minimum=12, target_max=75, preferred_min=12, preferred_max=75, maximum=100
)


@dataclass(frozen=True)
class FinalQualityResult:
    """Structured, UI-friendly outcome of final text validation."""

    valid: bool
    code: str
    reason: str
    text: str
    normalized_text: str
    preset: str
    source: Optional[str]
    length: int
    quality_band: str
    length_score: float
    matched: Optional[str] = None
    anchor_evidence: Optional[str] = None
    semantic_compatibility: Optional[bool] = None
    repetition_family: Optional[str] = None
    tone_score: float = 0.0

    @property
    def ok(self) -> bool:
        return self.valid

    @property
    def is_valid(self) -> bool:
        return self.valid

    @property
    def accepted(self) -> bool:
        return self.valid

    @property
    def rejection_code(self) -> Optional[str]:
        return None if self.valid else self.code

    @property
    def failure_code(self) -> Optional[str]:
        return self.rejection_code

    @property
    def reason_code(self) -> str:
        return self.code


PresetLike = Union[CommunityRhythmPreset, str]


class FinalQualityGate:
    """One policy gate for every final comment text source."""

    HARD_MAX_LENGTH: ClassVar[int] = 160
    MAX_LENGTH: ClassVar[int] = HARD_MAX_LENGTH
    LEGACY_PRESET: ClassVar[str] = "legacy"
    FINAL_TEXT_SOURCES: ClassVar[Tuple[str, ...]] = (
        "gemini",
        "local",
        "template",
        "clipboard",
    )

    FORMAL_SUBSTRINGS: ClassVar[Tuple[str, ...]] = (
        "합니다",
        "입니다",
        "됩니다",
        "보입니다",
        "느껴집니다",
        "생각됩니다",
        "판단됩니다",
        "추천드립니다",
        "추천드려요",
        "인상적입니다",
        "인상적이네요",
        "유용합니다",
        "도움이 됩니다",
        "좋을 것 같습니다",
        "방문하고 싶습니다",
        "먹어보고 싶습니다",
        "같습니다",
        "싶습니다",
        "습니다",
        "있습니다",
        "없습니다",
        "드립니다",
    )

    # Hard banned macros: blatant macro / opening boilerplate / hype words
    HARD_BANNED_MACROS: ClassVar[Tuple[str, ...]] = (
        "전반적으로",
        "전체적으로",
        "무엇보다",
        "라는 점",
        "라는 부분",
        "다는 점",
        "다는 부분",
        "알찬 정보",
        "알찬",
        "유익한 정보",
        "유익한",
        "유용한 정보",
        "좋은 정보",
        "구성이 돋보",
        "돋보",
        "정리가 잘 되어",
        "깔끔하게 잘 정리",
        "깔끔하게 정리",
        "잘 정리",
        "참고하기 좋",
        "좋은 포스팅",
        "포스팅 잘 봤어요",
        "포스팅 잘 읽었습니다",
        "오늘도 좋은 하루",
        "제 블로그에도",
        "소통해요",
        "서이추",
        "답방",
        "놀러 와주세요",
        "완성도가",
        "잘 보고 갑니다",
        "작성자님",
        "도움이 되었습니다",
        "관점이",
        "그중에서도",
        "정성 가득",
        "취향저격",
        "취저",
        "못 참죠",
        "강추",
        "대박",
        "방문각",
        "구매각",
    )

    # Soft conversational phrases: permissible standalone, forbidden when stacked (>= 2)
    SOFT_AI_PHRASES: ClassVar[Tuple[str, ...]] = (
        "특히",
        "한눈에",
        "사진 보니까",
        "글 보니까",
        "포스팅 보니까",
        "매력적",
        "인상적",
        "눈길을 끄",
        "구성이",
        "구성도",
        "조화가",
    )

    AI_SUMMARY_MACRO_PHRASES: ClassVar[Tuple[str, ...]] = HARD_BANNED_MACROS

    FAKE_EXPERIENCE_PHRASES: ClassVar[Tuple[str, ...]] = (
        "저도 가봤",
        "저도 먹어봤",
        "저도 써봤",
        "저도 구매했",
        "저도 이용해봤",
        "저도 예전에",
        "저희 아이도",
        "우리 강아지도",
        "우리 고양이도",
        "저희 집도",
        "저희 가족도",
        "가봤어요",
        "먹어봤어요",
        "써봤어요",
        "사용해봤",
        "구매해봤",
        "이용해봤",
        "주문해봤",
        "다녀왔어요",
        "방문했어요",
        "다녀왔",
        "방문했",
        "더라구요",
        "더군요",
    )

    ABSOLUTE_OR_PRESSURE_PHRASES: ClassVar[Tuple[str, ...]] = (
        "꼭",
        "반드시",
        "무조건",
    )

    RUDE_SLANG_PHRASES: ClassVar[Tuple[str, ...]] = (
        "씨발",
        "시발",
        "ㅅㅂ",
        "병신",
        "ㅂㅅ",
        "븅신",
        "개새끼",
        "지랄",
        "꺼져",
        "닥쳐",
        "좆",
        "존나",
        "ㅈㄴ",
        "개같",
        "개쩐다",
        "미친놈",
        "미친년",
        "등신",
        "찐따",
        "fuck",
        "shit",
        "asshole",
    )

    EMOTICON_PHRASES: ClassVar[Tuple[str, ...]] = (
        ":)",
        ":-)",
        ":D",
        ":-D",
        ":P",
        ":p",
        ":-P",
        ":-p",
        ";)",
        ";-)",
        ";P",
        ";p",
        ";-P",
        ";-p",
        "^^",
        "^_^",
        "ㅎㅎ",
        "ㅋㅋ",
        "ㅠㅠ",
        "ㅜㅜ",
    )

    _LAUGHTER_RE: ClassVar[re.Pattern[str]] = re.compile(r"[ㅋㅎㅠㅜ]{1,}")
    _FAKE_EXPERIENCE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:저도|저는|제가|저희|우리)\s*"
        r"(?:어제|지난번|지난|전에|예전에|직접|이미|한번)?\s*"
        r"(?:가봤|먹어봤|써봤|구매했|구매해봤|이용해봤|주문해봤|"
        r"다녀왔|방문했|사용해봤)"
    )

    # Descriptive aliases make the policy easy to consume from UI and tests
    # without coupling callers to the internal naming of the lists above.
    BANNED_FORMAL_SUBSTRINGS: ClassVar[Tuple[str, ...]] = FORMAL_SUBSTRINGS
    BANNED_AI_SUMMARY_MACROS: ClassVar[Tuple[str, ...]] = AI_SUMMARY_MACRO_PHRASES
    BANNED_FAKE_EXPERIENCES: ClassVar[Tuple[str, ...]] = FAKE_EXPERIENCE_PHRASES
    BANNED_ABSOLUTE_PHRASES: ClassVar[Tuple[str, ...]] = ABSOLUTE_OR_PRESSURE_PHRASES
    BANNED_RUDE_SLANG: ClassVar[Tuple[str, ...]] = RUDE_SLANG_PHRASES

    @classmethod
    def policy_for(cls, preset: PresetLike = CommunityRhythmPreset.COMMUNITY) -> CommentLengthPolicy:
        if isinstance(preset, CommunityRhythmPreset):
            key = preset
        else:
            try:
                key = CommunityRhythmPreset(str(preset).lower())
            except ValueError as exc:
                raise ValueError(f"Unknown comment preset: {preset!r}") from exc
        return COMMENT_POLICIES[key]

    @classmethod
    def normalize(cls, text: str) -> str:
        return unicodedata.normalize("NFC", text)

    @classmethod
    def validate(
        cls,
        text: str,
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        source: Optional[str] = None,
        allow_period: bool = False,
        legacy: bool = False,
        anchor_evidence: Optional[str] = None,
        semantic_compatibility: Optional[bool] = None,
        repetition_family: Optional[str] = None,
    ) -> FinalQualityResult:
        original = text if isinstance(text, str) else ""
        normalized = cls.normalize(original)
        effective_preset = cls.LEGACY_PRESET if legacy else str(preset.value if isinstance(preset, CommunityRhythmPreset) else preset).lower()
        policy = LEGACY_COMMENT_POLICY if legacy else cls.policy_for(preset)
        length = len(normalized)

        def result(
            valid: bool,
            code: str,
            reason: str,
            *,
            matched: Optional[str] = None,
            band: Optional[str] = None,
            score: Optional[float] = None,
        ) -> FinalQualityResult:
            return FinalQualityResult(
                valid=valid,
                code=code,
                reason=reason,
                text=original,
                normalized_text=normalized,
                preset=effective_preset,
                source=source,
                length=length,
                quality_band=policy.band(length) if band is None else band,
                length_score=policy.score(length) if score is None else score,
                matched=matched,
                anchor_evidence=anchor_evidence,
                semantic_compatibility=semantic_compatibility,
                repetition_family=repetition_family,
                tone_score=round((policy.score(length) * 0.8) + (0.2 if normalized.endswith("~") else 0.1), 2),
            )

        is_thoughtful = (effective_preset == CommunityRhythmPreset.THOUGHTFUL.value)
        is_user_source = source in ("user_edit", "user", "manual", "user_override")
        effective_allow_period = allow_period or is_thoughtful or is_user_source
        if not isinstance(text, str):
            return result(False, "invalid_text", "comment text must be a string")
        if not effective_allow_period and not legacy and ("." in normalized or "。" in normalized):
            matched = "." if "." in normalized else "。"
            return result(False, "forbidden_period", f"period is forbidden: {matched}", matched=matched)
        if not legacy and not is_user_source and normalized.count("~") > (2 if is_thoughtful else 1):
            max_tilde = 2 if is_thoughtful else 1
            return result(False, "excessive_tilde", f"at most {max_tilde} tildes allowed", matched="~")
        strong_slang = re.findall(r"(?:^|\s)(헐|와|세상에|대박)(?=\s)|미쳤|맛도리", normalized)
        if not legacy and not is_user_source and len(strong_slang) > (2 if is_thoughtful else 1):
            return result(False, "excessive_slang", "at most one strong slang expression is allowed")
        if semantic_compatibility is False:
            return result(False, "semantic_mismatch", "reaction is incompatible with its anchor")
        if length > policy.maximum:
            return result(False, "length_exceeded", f"comment exceeds {policy.maximum} characters", band="above_maximum", score=0.0)

        # 사용자 직접 편집본은 AI 초안용 문체 강제 규칙(격식체/매크로/이모지/가짜경험/최소길이)을 면제함
        if not is_user_source:
            for phrase in cls.FORMAL_SUBSTRINGS:
                if phrase in normalized:
                    return result(False, "formal_register", f"formal register is forbidden: {phrase}", matched=phrase)
            # PositiveSafetyValidator still owns its historic macro vocabulary;
            # its compatibility profile must not reject existing grounded text
            # merely because a newly added V13.1 root is present. Final text uses
            # the strict public profile above.
            if not legacy:
                for phrase in cls.HARD_BANNED_MACROS:
                    if phrase in normalized:
                        return result(False, "banned_macro", f"summary or macro phrase is forbidden: {phrase}", matched=phrase)

                matched_soft = [p for p in cls.SOFT_AI_PHRASES if p in normalized]
                if len(matched_soft) >= 2:
                    return result(False, "banned_macro", f"multiple soft AI phrases: {', '.join(matched_soft)}", matched=matched_soft[0])
            for phrase in cls.FAKE_EXPERIENCE_PHRASES:
                if phrase in normalized:
                    return result(False, "fake_experience", f"unverified past experience is forbidden: {phrase}", matched=phrase)
            fake_experience = cls._FAKE_EXPERIENCE_RE.search(normalized)
            if fake_experience:
                return result(
                    False,
                    "fake_experience",
                    f"unverified past experience is forbidden: {fake_experience.group()}",
                    matched=fake_experience.group(),
                )
            for phrase in cls.ABSOLUTE_OR_PRESSURE_PHRASES:
                if phrase in normalized:
                    return result(False, "absolute_or_pressure", f"absolute or pressure wording is forbidden: {phrase}", matched=phrase)
            for phrase in cls.EMOTICON_PHRASES:
                if phrase in normalized:
                    return result(False, "laughter_or_emoticon", f"laughter or emoticon is forbidden: {phrase}", matched=phrase)
            laughter = cls._LAUGHTER_RE.search(normalized)
            if laughter:
                return result(False, "laughter_or_emoticon", f"laughter marker is forbidden: {laughter.group()}", matched=laughter.group())
            for symbol in normalized:
                if unicodedata.category(symbol) == "So":
                    return result(False, "emoji", f"emoji or symbol is forbidden: {symbol}", matched=symbol)

        # 욕설(RUDE_SLANG)은 항상 금지
        for phrase in cls.RUDE_SLANG_PHRASES:
            if phrase in normalized.lower():
                return result(False, "rude_slang", f"rude slang is forbidden: {phrase}", matched=phrase)

        if not is_user_source:
            if length < policy.minimum:
                return result(False, "length_below_minimum", f"comment is shorter than {policy.minimum} characters", band="below_minimum", score=0.0)

        return result(True, "ok", "comment passed final quality policy")

    @classmethod
    def validate_final_text(
        cls,
        final_text: str,
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        source: Optional[str] = None,
        anchor_evidence: Optional[str] = None,
        semantic_compatibility: Optional[bool] = None,
        repetition_family: Optional[str] = None,
    ) -> FinalQualityResult:
        return cls.validate(
            final_text, preset=preset, source=source,
            anchor_evidence=anchor_evidence,
            semantic_compatibility=semantic_compatibility,
            repetition_family=repetition_family,
        )

    @classmethod
    def validate_text(
        cls,
        text: str,
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        source: Optional[str] = None,
    ) -> FinalQualityResult:
        return cls.validate_final_text(text, preset=preset, source=source)

    @classmethod
    def validate_final(
        cls,
        text: str,
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        source: Optional[str] = None,
    ) -> FinalQualityResult:
        return cls.validate_final_text(text, preset=preset, source=source)

    @classmethod
    def validate_combined_text(
        cls,
        final_text: str,
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        source: Optional[str] = None,
    ) -> FinalQualityResult:
        return cls.validate_final_text(final_text, preset=preset, source=source)

    @classmethod
    def validate_candidate_text(cls, text: str, *, legacy: bool = False) -> FinalQualityResult:
        return cls.validate(text, legacy=legacy, allow_period=legacy)



@dataclass(frozen=True)
class DraftInspectionResult:
    """5단계 초안 품질 검사 결과"""
    passed: bool
    stage: int  # 0: 통과, 1~5: 불합격 단계
    code: str
    feedback: str
    matched: Optional[str] = None


class CommentDraftInspector:
    """
    실제 사람의 말투 반영 여부를 검증하는 5단계 초안 검사기 및 1회 재작성 피드백 생성기:
    1. 현재 글/요청에 해당하는 응답인가? (NEED_MORE_CONTEXT, 최소 길이)
    2. 본문에 없는 사실이나 경험을 지어냈는가? (거짓 방문/사용 경험)
    3. 상투적인 요약·평가·중복 마무리가 있는가? (설명조, 습관적 덧붙임)
    4. 최근 댓글과 지나치게 비슷한가? (어미/구조 반복)
    5. 실제 사용자 문체에서 벗어나는가? (격식체, 매크로 문구)
    """

    EXPLANATORY_PHRASES: ClassVar[Tuple[str, ...]] = (
        "부분을 이렇게 풀어주",
        "부분을 풀어주",
        "설명이 구체적이라",
        "설명이 자세해서",
        "구성이 돋보",
        "정리가 잘 되어",
        "깔끔하게 정리",
        "소개해주셔서",
        "한눈에 들어오네요",
        "이해하기 편하네요",
    )

    REPETITIVE_TAILS: ClassVar[Tuple[str, ...]] = (
        "참고해야겠",
        "참고할게요",
        "기억해둬야겠",
        "기억해둘게요",
        "한번 가봐야겠",
        "가봐야겠",
        "방문해봐야겠",
        "도움이 될 것 같",
        "도움이 되겠",
        "가보고 싶네요",
        "들러봐야겠",
        "좋겠어요",
    )

    UNVERIFIED_TEXTURE_WORDS: ClassVar[Tuple[str, ...]] = (
        "꼬독꼬독",
        "바삭바삭",
        "야들야들",
        "포슬포슬",
        "탱글탱글",
        "꾸덕",
        "쫄깃",
        "쫀득",
        "촉촉",
        "바삭",
    )

    INVENTED_CAUSALITY_PATTERNS: ClassVar[Tuple[re.Pattern[str], ...]] = (
        re.compile(r"(?:구워서|튀겨서|삶아서|볶아서|쪄서|숙성해서|끓여서|훈제해서)\s*(?:더\s*)?(?:통통|촉촉|쫀득|쫄깃|부드럽|탱글|꼬독|고소|바삭|담백|맛있)"),
        re.compile(r"(?:조합이라|조합이라서)\s*(?:더\s*)?(?:꼬독|쫀득|바삭|고소|촉촉|담백)"),
    )

    @classmethod
    def inspect(
        cls,
        text: str,
        recent_comments: Optional[List[str]] = None,
        preset: PresetLike = CommunityRhythmPreset.THOUGHTFUL,
        excerpt: Optional[str] = None,
        source: Optional[str] = None,
    ) -> DraftInspectionResult:
        if not text or not isinstance(text, str):
            return DraftInspectionResult(
                passed=False, stage=1, code="empty_text",
                feedback="댓글 텍스트가 비어 있습니다. 본문의 구체적 내용에 반응해주세요."
            )

        raw = text.strip()
        if raw == "NEED_MORE_CONTEXT":
            return DraftInspectionResult(
                passed=False, stage=1, code="need_more_context",
                feedback="NEED_MORE_CONTEXT"
            )

        # 사용자 직접 수정본(user_edit)은 AI 문체 제약(어미 중복, 과도한 부호, 사족 등) 면제
        is_user_edit = source in ("user_edit", "user", "manual", "user_override")

        normalized = FinalQualityGate.normalize(raw)
        norm_excerpt = FinalQualityGate.normalize(excerpt) if excerpt else ""

        # 1단계: 현재 글/요청 적합성 및 최소 길이
        if not is_user_edit and len(normalized) < 10:
            return DraftInspectionResult(
                passed=False, stage=1, code="too_short",
                feedback="댓글 길이가 너무 짧습니다. 본문의 구체적인 내용 하나를 언급하며 자연스럽게 작성해주세요."
            )

        # 2단계: 사실성 검사 (거짓 방문/사용 경험, 본문에 없는 식감 추측, 가짜 인과관계, 대상-속성 왜곡, 부정 왜곡)
        for phrase in FinalQualityGate.FAKE_EXPERIENCE_PHRASES:
            if phrase in normalized:
                return DraftInspectionResult(
                    passed=False, stage=2, code="fake_experience",
                    matched=phrase,
                    feedback=f"실제로 가봤거나 먹어봤다는 거짓 경험 표현('{phrase}')이 포함되어 있습니다. 방문/구매/사용 경험을 지어내지 말고, 본문 내용을 본 제3자의 시선에서 구체적 디테일에만 반응해주세요."
                )
        fake_match = FinalQualityGate._FAKE_EXPERIENCE_RE.search(normalized)
        if fake_match:
            matched_text = fake_match.group()
            return DraftInspectionResult(
                passed=False, stage=2, code="fake_experience",
                matched=matched_text,
                feedback=f"거짓 경험 표현('{matched_text}')이 포함되어 있습니다. 본문에 명시된 사실에 대해서만 반응해주세요."
            )

        # 2-1. 본문에 없는 식감 추측 검사
        if norm_excerpt:
            for tex_word in cls.UNVERIFIED_TEXTURE_WORDS:
                if tex_word in normalized and tex_word not in norm_excerpt:
                    return DraftInspectionResult(
                        passed=False, stage=2, code="unsupported_texture",
                        matched=tex_word,
                        feedback=f"본문에 언급되지 않은 식감 추측 표현('{tex_word}')이 포함되어 있습니다. 본문에 없는 식감이나 맛을 추측하지 말고 본문의 실제 내용에만 반응해주세요."
                    )

            # 2-2. 임의의 인과관계 왜곡 검사 (예: 구워서 더 촉촉)
            for pat in cls.INVENTED_CAUSALITY_PATTERNS:
                c_match = pat.search(normalized)
                if c_match and c_match.group() not in norm_excerpt:
                    return DraftInspectionResult(
                        passed=False, stage=2, code="invented_causality",
                        matched=c_match.group(),
                        feedback=f"본문에 없는 인과관계 표현('{c_match.group()}')이 포함되어 있습니다. 두 특징을 원인과 결과로 연결하지 말고 본문의 구체적 사실에만 반응해주세요."
                    )

            # 2-3. 반찬/부속 속성을 메인 메뉴 속성으로 왜곡 검사
            side_dish_words = ("반찬", "밑반찬", "김치", "깍두기", "피클", "단무지", "파절이", "양파절임", "샐러드", "무생채", "겉절이")
            side_attributes = ("아삭", "바삭", "매콤", "새콤", "정갈", "깔끔", "시원", "짭조름", "달콤")
            for side_kw in side_dish_words:
                if side_kw in norm_excerpt:
                    for attr in side_attributes:
                        if re.search(rf"(?:{side_kw})[^\.\n]{{0,25}}{attr}|{attr}[^\.\n]{{0,25}}(?:{side_kw})", norm_excerpt):
                            if attr in normalized and side_kw not in normalized:
                                main_transfer = re.search(rf"([가-힣]{{2,10}})(?:이|가|은|는|\s+).*{attr}|{attr}[가-힣\s]*\s+([가-힣]{{2,10}})", normalized)
                                if main_transfer:
                                    raw_noun = main_transfer.group(1) or main_transfer.group(2)
                                    target_noun = re.sub(r"(?:이라니|라니|에서|에게|으로|로|이|가|은|는|을|를|도|의)$", "", raw_noun) if raw_noun else ""
                                    if target_noun and target_noun not in side_dish_words and target_noun in norm_excerpt:
                                        return DraftInspectionResult(
                                            passed=False, stage=2, code="mismatched_attribute_target",
                                            matched=f"{target_noun}+{attr}",
                                            feedback=f"본문에서 반찬({side_kw})의 특징으로 언급된 '{attr}'을(를) 다른 메뉴('{target_noun}')의 속성으로 잘못 연결했습니다. 본문의 정확한 대상에 반응해주세요."
                                        )

            # 2-4. 본문의 부정을 긍정으로 왜곡 검사 (예: 바삭하지 않다 -> 바삭하다)
            eval_attrs = ("바삭", "촉촉", "맵", "달콤", "부드럽", "신선", "짜", "기름지", "쫄깃", "뜨겁", "따뜻", "비리", "질기", "느끼")
            for attr in eval_attrs:
                neg_in_excerpt = re.search(rf"{attr}(?:하지\s*않|지\s*않|지\s*못|하진\s*않|진\s*않|은\s*아니|는\s*아니|지\s*않았|하지\s*않았)", norm_excerpt)
                if neg_in_excerpt:
                    aff_in_comment = re.search(rf"{attr}(?:하(?:고|며|여|서|면|네요|군요|ㅂ니다|요|아|어)|한\b|해|합)", normalized)
                    neg_in_comment = re.search(rf"{attr}[가-힣\s]{{0,10}}(?:않|못|아니)", normalized)
                    if aff_in_comment and not neg_in_comment:
                        return DraftInspectionResult(
                            passed=False, stage=2, code="negation_inversion",
                            matched=f"{attr} (본문: 부정 -> 초안: 긍정)",
                            feedback=f"본문에서 부정적으로 서술된 속성('{attr}')을 긍정으로 왜곡했습니다. 본문의 실제 서술을 있는 그대로 반영해주세요."
                        )

        # 사용자 직접 수정본은 AI 문체 제약(3, 4, 5단계) 면제
        if is_user_edit:
            return DraftInspectionResult(passed=True, stage=0, code="ok", feedback="", matched=None)

        # 3단계: 상투적인 요약·평가·중복 마무리
        for phrase in cls.EXPLANATORY_PHRASES:
            if phrase in normalized:
                return DraftInspectionResult(
                    passed=False, stage=3, code="explanatory_tone",
                    matched=phrase,
                    feedback=f"글을 설명하거나 평가하는 문구('{phrase}')가 포함되어 있습니다. 설명조를 빼고 본문의 구체적인 디테일(메뉴, 비주얼, 사물 등)에 대한 짧은 반응 1문장으로 작성해주세요."
                )

        # 습관적 중복 마무리 ('좋겠어요'가 단문 반응일 때는 허용, 앞선 감탄에 사족으로 덧붙여진 경우만 차단)
        sentences = [s.strip() for s in re.split(r'[\n\.\?!~]+', normalized) if s.strip()]
        for tail in cls.REPETITIVE_TAILS:
            if tail in normalized:
                is_appended = False
                if tail == "좋겠어요":
                    if len(sentences) >= 2 and len(sentences[0]) >= 5:
                        is_appended = True
                    else:
                        prior_excl = re.search(
                            r"(?:[네구]요|[네구]염|대박|최고|예술|끝내주|완전|짱|미쳤|맛있(?:겠|어|네)|멋지|예쁘)[\s,~!]+.*좋겠어요",
                            normalized
                        )
                        is_appended = bool(prior_excl)
                else:
                    is_appended = (len(sentences) >= 2) or (normalized.find(tail) > (len(normalized) * 0.3))

                if is_appended:
                    return DraftInspectionResult(
                        passed=False, stage=3, code="repetitive_tail",
                        matched=tail,
                        feedback=f"마지막 문장에 습관적인 중복 마무리('{tail}')가 붙어 있습니다. 뒤의 군더더기를 삭제하고 본문의 구체적인 내용에 대한 1문장 반응만 남겨주세요."
                    )

        # 4단계: 최근 댓글과 반복 검사 (전체 일치 차단, 구조 클론 차단, 단순 어미 일치는 허용)
        if recent_comments:
            # 4-1. 문장 전체 동일
            for rc in recent_comments[-5:]:
                if rc and FinalQualityGate.normalize(rc.strip()) == normalized:
                    return DraftInspectionResult(
                        passed=False, stage=4, code="exact_duplicate",
                        matched=normalized,
                        feedback="최근 작성된 댓글과 완전히 동일한 문장입니다. 본문의 다른 내용에 반응해주세요."
                    )

            # 4-2. 구조적 템플릿 클론 ("... 진짜/너무/특히 ... 보여요" 템플릿 2회 이상 반복 시)
            clone_match = re.search(r"(?:진짜|너무|특히|더)\s*(?:맛있어|고소해|촉촉해|좋아|달콤해|예뻐)\s*(?:보여요|보이네요)", normalized)
            if clone_match:
                clone_count = 0
                for rc in recent_comments[-3:]:
                    if rc and re.search(r"(?:진짜|너무|특히|더)\s*(?:맛있어|고소해|촉촉해|좋아|달콤해|예뻐)\s*(?:보여요|보이네요)", FinalQualityGate.normalize(rc)):
                        clone_count += 1
                if clone_count >= 2:
                    return DraftInspectionResult(
                        passed=False, stage=4, code="structural_clone_detected",
                        matched=clone_match.group(),
                        feedback="최근 댓글과 동일한 감상 묘사 문장 구조('... 진짜/특히 ... 보여요')가 반복되고 있습니다. 설명형 문장 대신 본문의 다른 구체적인 사실(주문, 크기, 대기, 비교 등)에 반응해주세요."
                    )

            # 4-3. 동일한 어미/어구 반복 검사
            # 앞 댓글들과 같은 어미가 나왔다고 해서 무조건 반려하지 않고, 내용과 문장 구조까지 유사한 복제만 반려
            ending_tokens = ("네요", "겠어요", "겠네요", "보여요", "보이네요", "좋네요", "같아요", "있어요", "합니다", "입니다", "습니다", "듭니다")
            cand_clean = re.sub(r'[^가-힣]', '', normalized)
            cand_ending = next((tok for tok in ending_tokens if cand_clean.endswith(tok)), (cand_clean[-2:] if len(cand_clean) >= 2 else ""))
            if cand_ending:
                rep_count = 0
                matching_recent = []
                for rc in recent_comments[-3:]:
                    rc_clean = re.sub(r'[^가-힣]', '', rc.strip())
                    if rc_clean.endswith(cand_ending):
                        rep_count += 1
                        matching_recent.append(rc.strip())
                if rep_count >= 2:
                    is_clone = False
                    for mr in matching_recent:
                        # 1) 특정 감상/평가형 어구 동일 반복
                        if cand_ending in ("좋네요", "보여요", "보이네요", "같아요", "맛있네요", "최고네요"):
                            is_clone = True
                            break
                        # 2) 동일한 수식어 구조 복제
                        if re.search(r"(?:진짜|너무|특히|더)\s*[가-힣]+", normalized) and re.search(r"(?:진짜|너무|특히|더)\s*[가-힣]+", mr):
                            is_clone = True
                            break
                        # 3) 핵심 단어 2개 이상 공유
                        from services.comments.entities import extract_entity_tokens
                        cand_tokens = set(extract_entity_tokens(normalized))
                        mr_tokens = set(extract_entity_tokens(mr))
                        if len(cand_tokens & mr_tokens) >= 2:
                            is_clone = True
                            break

                    if is_clone:
                        return DraftInspectionResult(
                            passed=False, stage=4, code="repetition_detected",
                            matched=cand_ending,
                            feedback=f"최근 작성된 댓글과 동일한 어미/어구('{cand_ending}')가 반복되고 있습니다. 다른 자연스러운 어미나 새로운 시각의 반응을 선택해주세요."
                        )

        # 5단계: 실제 사용자 문체 이탈 (격식체/매크로 문구)
        formal_endings = FinalQualityGate.FORMAL_SUBSTRINGS + ("듭니다", "습니다", "있습니다", "없습니다", "드립니다")
        for phrase in FinalQualityGate.HARD_BANNED_MACROS:
            if phrase in normalized:
                return DraftInspectionResult(
                    passed=False, stage=5, code="banned_macro",
                    matched=phrase,
                    feedback=f"기계적인 블로그 매크로 문구('{phrase}')가 포함되어 있습니다. 매크로 표현을 지우고 본문 속 구체적인 내용에 편안한 일상체로 반응해주세요."
                )
        for phrase in formal_endings:
            if phrase in normalized:
                return DraftInspectionResult(
                    passed=False, stage=5, code="formal_register",
                    matched=phrase,
                    feedback=f"딱딱한 격식체 어미('{phrase}')가 사용되었습니다. 친근하고 편안한 일상 구어체 존댓말(~요, ~네요 등)로 바꿔주세요."
                )

        return DraftInspectionResult(
            passed=True, stage=0, code="ok", feedback="", matched=None
        )


__all__ = [
    "COMMENT_POLICIES",
    "CommentLengthPolicy",
    "CommunityRhythmPreset",
    "FinalQualityGate",
    "FinalQualityResult",
    "DraftInspectionResult",
    "CommentDraftInspector",
]

