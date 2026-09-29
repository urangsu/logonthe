# -*- coding: utf-8 -*-
"""
services/comments/style_normalizer.py

이모티콘 및 문체 스타일 정규화 (Style Normalization):
- 유니코드 'So' 카테고리 전체를 이모지로 간주하지 않고 엄격한 이모지 패턴 및 NON_EMOJI_SYMBOLS 보호 (20℃, 1.5㎏, ©, ™ 원형 보존)
- 허용 가능한 Soft Emoji Whitelist (😋, ✨, 👍, 😊, 👏, 🤤, 🤍, ❤️, 👀, ☕, 🌿, 🙌 등) 기반 필터링
- 반복·과장형 이모지(🔥🔥🔥, 😂😂, ❤️❤️❤️, 😋😋😋🔥 등) 완전 제거
- 자연스러운 단일 Soft Emoji는 15~20% 확률로 희소하게 보존
- post_key 기반 deterministic sampling (재시도 시에도 일관된 결과 보장)
- 실제 등록(SUBMITTED) 댓글 기준 최근 5개 이력 검사 (최근 5건에 이모지가 없을 때만 보존 허용)
- 이모티콘이 없거나 제거된 경우 친근한 종결형 문맥에서 가끔(25%) 단일 물결표(~) 부가
- 마침표(., 。) 완전 제거 및 소수점(1.5kg) 보존
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional, Set

from services.draft import normalize_comment_punctuation


# 단위, 학술, 법적 기호 등 절대 이모지로 간주하지 않는 특수 기호 집합
NON_EMOJI_SYMBOLS: Set[str] = frozenset({
    "℃", "℉", "㎏", "㎎", "㎞", "㎝", "㎜", "㏄", "㎖", "ℓ", "㎡", "㎦",
    "©", "™", "®", "℗", "℠", "№", "℡", "§", "¶", "†", "‡", "※", "°",
})

# 댓글에서 자연스럽고 친근한 반응으로 허용되는 Soft Emoji Whitelist
SOFT_EMOJI_WHITELIST: Set[str] = frozenset({
    "😋",  # 맛있는 음식/비주얼
    "✨",  # 매장 분위기/깔끔함/비주얼
    "👍",  # 추천/엄지척
    "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿",
    "😊",  # 미소/친절/만족
    "👏",  # 박수/칭찬
    "🤤",  # 군침/맛있어 보임
    "🤍", "❤️",  # 하트
    "👀",  # 시선/관심
    "☕",  # 커피/카페
    "🌿",  # 자연/분위기
    "🙌",  # 호응
})


@dataclass(frozen=True)
class StyleNormalizeResult:
    text: str
    emoji_found: Optional[str]
    emoji_action: str  # "kept", "removed", "none"
    reason: str
    tilde_added: bool = False


class CommentStyleNormalizer:
    _EMOJI_RE = re.compile(
        r"(?:\u2764\ufe0f|"
        r"[\U0001F1E6-\U0001F1FF]{2}|"
        r"[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF]"
        r"[\ufe00-\ufe0f\U0001f3fb-\U0001f3ff]?"
        r"(?:\u200d[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF][\ufe00-\ufe0f\U0001f3fb-\U0001f3ff]?)*|"
        r"[\u2600-\u27bf][\ufe00-\ufe0f]?"
        r")",
        flags=re.UNICODE,
    )

    NON_EMOJI_SYMBOLS = NON_EMOJI_SYMBOLS
    SOFT_EMOJI_WHITELIST = SOFT_EMOJI_WHITELIST

    @staticmethod
    def stable_hash(val: Any) -> int:
        """문자열 기반의 결정적 해시 정수 반환 (0 ~ 2^32-1)"""
        return int(hashlib.sha256(str(val or "").encode("utf-8")).hexdigest()[:8], 16)

    @classmethod
    def extract_emojis(cls, text: str) -> List[str]:
        """
        텍스트에 포함된 이모지 목록 추출.
        - 'So' 카테고리 전체를 이모지로 간주하지 않음 (20℃, 1.5㎏, ©, ™ 등 보존)
        - NON_EMOJI_SYMBOLS 명시적 제외
        """
        if not text:
            return []
        matches = [m.group() for m in cls._EMOJI_RE.finditer(text)]
        return [em for em in matches if em not in cls.NON_EMOJI_SYMBOLS]

    @classmethod
    def has_emoji(cls, text: str) -> bool:
        """이모지 포함 여부 판별"""
        return bool(cls.extract_emojis(text))

    @classmethod
    def strip_emojis(cls, text: str) -> str:
        """
        텍스트에서 이모지만 제거하고 일반 특수 기호(℃, ㎏, ©, ™ 등)는 원형 보존.
        """
        if not text:
            return ""

        def _replace_match(m: re.Match) -> str:
            token = m.group()
            if token in cls.NON_EMOJI_SYMBOLS:
                return token
            return ""

        cleaned = cls._EMOJI_RE.sub(_replace_match, text)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        return cleaned

    @classmethod
    def normalize_style(
        cls,
        text: str,
        *,
        post_key: str = "",
        recent_comments: Optional[List[str]] = None,
        allow_emoji_sampling: bool = True,
        emoji_sampling_threshold: int = 20,  # 15~20% 확률
        emoji_history_window: int = 5,       # 최근 5개 등록 댓글 검사
        allow_tilde_when_no_emoji: bool = True,
        tilde_sampling_threshold: int = 25,  # 이모티콘 없을 때 가끔 물결표 (25%)
    ) -> StyleNormalizeResult:
        """
        Gemini 생성 댓글을 스타일 정규화한다:
        1. 이모지 추출 및 반복/과장형 검사
        2. Soft Emoji Whitelist 검사
        3. 최근 5개 등록 댓글 이력 검사 (최근 이모지 사용 여부)
        4. post_key 기반 deterministic sampling (15~20%)
        5. keep 또는 strip 결정
        6. 이모지가 없거나 제거된 경우 가끔 단일 물결표(~) 부가
        7. 마침표(., 。) 정규화 및 소수점(1.5kg) 원형 보존
        """
        if not isinstance(text, str) or not text.strip():
            return StyleNormalizeResult(
                text="", emoji_found=None, emoji_action="none", reason="empty_input"
            )

        original_text = text.strip()
        emojis = cls.extract_emojis(original_text)
        tilde_added = False

        if not emojis:
            emoji_found = None
            emoji_action = "none"
            reason = "no_emoji_in_raw"
            working_text = original_text
        elif len(emojis) > 1:
            # 2개 이상 또는 연속/과장된 이모지는 전면 제거 (😋😋😋🔥, 🔥🔥🔥 등)
            emoji_found = "".join(emojis)
            emoji_action = "removed"
            reason = "exaggerated_or_multiple_emojis"
            working_text = cls.strip_emojis(original_text)
        else:
            # 단일 1개 이모지
            emoji = emojis[0]
            if emoji not in cls.SOFT_EMOJI_WHITELIST:
                # 권장되지 않는 과장/이질적 이모지 (예: 🔥, 💀 등) 제거
                emoji_found = emoji
                emoji_action = "removed"
                reason = "non_whitelisted_emoji"
                working_text = cls.strip_emojis(original_text)
            else:
                recent_window = (recent_comments or [])[-emoji_history_window:]
                recent_emoji_count = sum(1 for c in recent_window if cls.has_emoji(c))
                sample_val = cls.stable_hash(f"emoji:{post_key}") % 100 if post_key else 0

                if recent_emoji_count > 0:
                    emoji_found = emoji
                    emoji_action = "removed"
                    reason = f"recent_emoji_cooldown_{recent_emoji_count}"
                    working_text = cls.strip_emojis(original_text)
                elif allow_emoji_sampling and sample_val >= emoji_sampling_threshold:
                    emoji_found = emoji
                    emoji_action = "removed"
                    reason = f"deterministic_sampling_miss_{sample_val}"
                    working_text = cls.strip_emojis(original_text)
                else:
                    emoji_found = emoji
                    emoji_action = "kept"
                    reason = "recent_emoji_count_0"
                    working_text = original_text

        # 기본 공백 정리
        working_text = re.sub(r"[ \t]+", " ", working_text).strip()

        # 이모티콘이 없거나 제거된 경우 친근한 대화체 문맥에서 가끔 단일 물결표(~) 부가
        if emoji_action != "kept" and allow_tilde_when_no_emoji and working_text:
            if "~" not in working_text and not working_text.endswith(("!", "?", ".", "。")):
                tilde_sample = cls.stable_hash(f"tilde:{post_key}") % 100 if post_key else 0
                if tilde_sample < tilde_sampling_threshold:
                    if re.search(
                        r"(?:요|네요|어요|아요|죠|구요|고요|습니다|답니다|겠어요|보여요|같아요)$",
                        working_text,
                    ):
                        working_text = working_text + "~"
                        tilde_added = True

        # 마침표 정규화 및 소수점(1.5kg) 보존
        final_norm = normalize_comment_punctuation(working_text)

        return StyleNormalizeResult(
            text=final_norm,
            emoji_found=emoji_found,
            emoji_action=emoji_action,
            reason=reason,
            tilde_added=tilde_added,
        )
