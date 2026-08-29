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
from typing import ClassVar, Dict, Optional, Tuple, Union


class CommunityRhythmPreset(str, Enum):
    COMMUNITY = "community"
    CALM = "calm"


@dataclass(frozen=True)
class CommentLengthPolicy:
    """Length limits and scoring band for one comment style preset."""

    minimum: int
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
        span = self.maximum - self.preferred_max
        return 0.0 if span == 0 else (self.maximum - length) / span

    def band(self, length: int) -> str:
        if length < self.minimum:
            return "below_minimum"
        if length > self.maximum:
            return "above_maximum"
        if self.preferred_min <= length <= self.preferred_max:
            return "preferred"
        return "acceptable"


COMMENT_POLICIES: Dict[Union[CommunityRhythmPreset, str], CommentLengthPolicy] = {
    CommunityRhythmPreset.COMMUNITY: CommentLengthPolicy(
        minimum=15, preferred_min=18, preferred_max=45, maximum=55
    ),
    CommunityRhythmPreset.CALM: CommentLengthPolicy(
        minimum=20, preferred_min=20, preferred_max=65, maximum=65
    ),
}

# A profile used only by the compatibility adapter in PositiveSafetyValidator.
# It keeps that public API's historic 12-character and period behavior while
# retaining the old validator's safety checks. New final text must use one of
# the two public community rhythm presets above.
LEGACY_COMMENT_POLICY = CommentLengthPolicy(
    minimum=12, preferred_min=12, preferred_max=75, maximum=100
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
    )

    # Sections 11 and 12 of the V13.1 work order: report-like summaries and
    # reusable macro/opening language are not community comments.
    AI_SUMMARY_MACRO_PHRASES: ClassVar[Tuple[str, ...]] = (
        "전체적으로",
        "무엇보다",
        "특히 인상",
        "알찬 정보",
        "유익한 정보",
        "좋은 정보",
        "정리가 잘 되어",
        "한눈에",
        "구성이 돋보",
        "매력적이네요",
        "눈길을 끄네요",
        "완성도가",
        "유익한 정보",
        "잘 보고 갑니다",
        "작성자님",
        "도움이 되었습니다",
        "관점이",
        "깔끔하게 잘 정리",
        "포스팅 잘 읽었습니다",
        "좋은 정보 감사",
        "사진 보니까",
        "글 보니까",
        "포스팅 보니까",
    )

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
        "새끼",
        "지랄",
        "꺼져",
        "닥쳐",
        "좆",
        "존나",
        "ㅈㄴ",
        "개같",
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
        ";)",
        ";-)",
        "^^",
        "^_^",
        "ㅎㅎ",
        "ㅋㅋ",
        "ㅠㅠ",
        "ㅜㅜ",
    )

    _LAUGHTER_RE: ClassVar[re.Pattern[str]] = re.compile(r"[ㅋㅎㅠㅜ]{1,}")
    _EMOJI_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"[\U0001F1E6-\U0001FAFF\u2300-\u27BF\u2B50\uFE0F]"
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
            )

        if not isinstance(text, str):
            return result(False, "invalid_text", "comment text must be a string")
        if not allow_period and not legacy and ("." in normalized or "。" in normalized):
            matched = "." if "." in normalized else "。"
            return result(False, "forbidden_period", f"period is forbidden: {matched}", matched=matched)
        if length > policy.maximum:
            return result(False, "length_exceeded", f"comment exceeds {policy.maximum} characters", band="above_maximum", score=0.0)

        for phrase in cls.FORMAL_SUBSTRINGS:
            if phrase in normalized:
                return result(False, "formal_register", f"formal register is forbidden: {phrase}", matched=phrase)
        for phrase in cls.AI_SUMMARY_MACRO_PHRASES:
            if phrase in normalized:
                return result(False, "banned_macro", f"summary or macro phrase is forbidden: {phrase}", matched=phrase)
        for phrase in cls.FAKE_EXPERIENCE_PHRASES:
            if phrase in normalized:
                return result(False, "fake_experience", f"unverified past experience is forbidden: {phrase}", matched=phrase)
        for phrase in cls.ABSOLUTE_OR_PRESSURE_PHRASES:
            if phrase in normalized:
                return result(False, "absolute_or_pressure", f"absolute or pressure wording is forbidden: {phrase}", matched=phrase)
        for phrase in cls.RUDE_SLANG_PHRASES:
            if phrase in normalized.lower():
                return result(False, "rude_slang", f"rude slang is forbidden: {phrase}", matched=phrase)
        for phrase in cls.EMOTICON_PHRASES:
            if phrase in normalized:
                return result(False, "laughter_or_emoticon", f"laughter or emoticon is forbidden: {phrase}", matched=phrase)
        laughter = cls._LAUGHTER_RE.search(normalized)
        if laughter:
            return result(False, "laughter_or_emoticon", f"laughter marker is forbidden: {laughter.group()}", matched=laughter.group())
        emoji = cls._EMOJI_RE.search(normalized)
        if emoji:
            return result(False, "emoji", f"emoji is forbidden: {emoji.group()}", matched=emoji.group())

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
    ) -> FinalQualityResult:
        return cls.validate(final_text, preset=preset, source=source)

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


__all__ = [
    "COMMENT_POLICIES",
    "CommentLengthPolicy",
    "CommunityRhythmPreset",
    "FinalQualityGate",
    "FinalQualityResult",
]
