# -*- coding: utf-8 -*-
"""
services/comments/style_normalizer.py

이모티콘 및 문체 스타일 정규화 (Style Normalization):
- 반복·과장형 이모지(🔥🔥🔥, 😂😂, ❤️❤️❤️, 😋😋😋🔥 등) 완전 제거
- 자연스러운 단일 이모지(😋, ✨, 👍, 😊 등) 희소하게(15~20%) 보존
- post_key 기반 deterministic sampling (재시도 시에도 일관된 결과 보장)
- 실제 등록(SUBMITTED) 댓글 기준 최근 5개 이력 검사 (최근 5건에 이모지가 없을 때만 보존 허용)
- 이모티콘이 없을 경우 친근한 종결형 문맥에서 가끔(25%) 단일 물결표(~) 부가
- 마침표(., 。) 완전 제거 및 소수점(1.5kg) 보존
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional

from services.draft import normalize_comment_punctuation


@dataclass(frozen=True)
class StyleNormalizeResult:
    text: str
    emoji_found: Optional[str]
    emoji_action: str  # "kept", "removed", "none"
    reason: str
    tilde_added: bool = False


class CommentStyleNormalizer:
    _EMOJI_RE = re.compile(
        r"[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]"
        r"[\ufe00-\ufe0f\U0001f3fb-\U0001f3ff]?"
        r"(?:\u200d[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf][\ufe00-\ufe0f\U0001f3fb-\U0001f3ff]?)*",
        flags=re.UNICODE,
    )

    @staticmethod
    def stable_hash(val: Any) -> int:
        """문자열 기반의 결정적 해시 정수 반환 (0 ~ 2^32-1)"""
        return int(hashlib.sha256(str(val or "").encode("utf-8")).hexdigest()[:8], 16)

    @classmethod
    def extract_emojis(cls, text: str) -> List[str]:
        """텍스트에 포함된 이모지 목록 추출"""
        if not text:
            return []
        matches = list(cls._EMOJI_RE.finditer(text))
        matched_spans = [m.span() for m in matches]
        result = [m.group() for m in matches]
        for idx, ch in enumerate(text):
            if any(start <= idx < end for start, end in matched_spans):
                continue
            if unicodedata.category(ch) in ("So",):
                result.append(ch)
        return result

    @classmethod
    def has_emoji(cls, text: str) -> bool:
        """이모지 포함 여부 판별"""
        return bool(cls.extract_emojis(text))

    @classmethod
    def strip_emojis(cls, text: str) -> str:
        """텍스트에서 모든 이모지 제거 및 공백 정리"""
        if not text:
            return ""
        cleaned = cls._EMOJI_RE.sub("", text)
        cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch) not in ("So",))
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
        2. 최근 5개 등록 댓글 이력 검사 (최근 이모지 사용 여부)
        3. post_key 기반 deterministic sampling (15~20%)
        4. keep 또는 strip 결정
        5. 이모지가 없거나 제거된 경우 가끔 단일 물결표(~) 부가
        6. 마침표(., 。) 정규화 및 소수점 원형 보존
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
            # 2개 이상 또는 연속/과장된 이모지는 전면 제거
            emoji_found = "".join(emojis)
            emoji_action = "removed"
            reason = "exaggerated_or_multiple_emojis"
            working_text = cls.strip_emojis(original_text)
        else:
            # 단일 1개 이모지
            emoji = emojis[0]
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
