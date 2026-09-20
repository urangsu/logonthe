from dataclasses import dataclass
from typing import Optional, Any, Dict
from services.comments.community_rhythm import CommunityRhythmPreset, COMMENT_POLICIES


@dataclass(frozen=True)
class CommentStylePolicy:
    """
    프롬프트 생성, 초안 검증, 에디터 주입, 최종 제출 검증 전 과정에서
    단일하게 공유되는 불변 문체 및 품질 정책 객체.
    """
    preset: str = "community"
    target_length_desc: str = "20~60자"
    max_sentences: int = 2
    min_length: int = 10
    max_length: int = 80
    allow_period: bool = True
    allow_soft_laughter: bool = False
    allow_soft_emoji: bool = False
    max_combined_decorations: int = 0
    style_instruction: str = ""

    @classmethod
    def from_context(
        cls,
        preset: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        style_profile: Optional[Any] = None,
        action_plan: Optional[Any] = None,
    ) -> "CommentStylePolicy":
        cfg = config or {}
        p_str = (preset or cfg.get("comment_style_preset", "community")).lower()
        is_thoughtful = (p_str == "thoughtful" or p_str == CommunityRhythmPreset.THOUGHTFUL.value)

        policy_obj = COMMENT_POLICIES.get(
            CommunityRhythmPreset.THOUGHTFUL if is_thoughtful else CommunityRhythmPreset.COMMUNITY
        )
        min_len = policy_obj.minimum if policy_obj else (16 if is_thoughtful else 10)
        max_len = policy_obj.maximum if policy_obj else (100 if is_thoughtful else 80)
        target_desc = "30~80자" if is_thoughtful else "20~60자"

        # 검토된 학습 데이터가 충분하거나(5건 이상) 명시적 설정이 있는 경우에만 소프트 스타일 허용
        total_samples = getattr(style_profile, "total_samples", 0) if style_profile else 0
        cfg_allow_laughter = cfg.get("allow_soft_laughter", False)
        cfg_allow_emoji = cfg.get("allow_soft_emoji", False)

        learned_laughter = bool(
            style_profile
            and total_samples >= 5
            and getattr(style_profile, "laughter_ratio", 0.0) >= 0.05
        )
        learned_emoji = bool(
            style_profile
            and total_samples >= 5
            and getattr(style_profile, "emoji_ratio", 0.0) >= 0.03
        )

        allow_laughter = bool(cfg_allow_laughter or learned_laughter)
        allow_emoji = bool(cfg_allow_emoji or learned_emoji)
        max_decorations = 1 if (allow_laughter or allow_emoji) else 0

        if allow_laughter and allow_emoji:
            decor_instruction = "ㅎㅎ 또는 이모지는 어울릴 때 선택적으로 합계 최대 1개 가능"
        elif allow_laughter:
            decor_instruction = "ㅎㅎ는 어울릴 때 선택적으로 1회 가능, 이모지는 사용 안 함"
        elif allow_emoji:
            decor_instruction = "이모지는 어울릴 때 선택적으로 1개 가능, 웃음 문자는 사용 안 함"
        else:
            decor_instruction = "장식 없음 (웃음·이모지 사용 안 함)"

        style_instruction = f"보통 1문장, 필요하면 2문장 ({target_desc} 내외). {decor_instruction}"

        return cls(
            preset="thoughtful" if is_thoughtful else "community",
            target_length_desc=target_desc,
            max_sentences=2,
            min_length=min_len,
            max_length=max_len,
            allow_period=True,  # 자연스러운 1~2문장에 표준 마침표 전면 허용
            allow_soft_laughter=allow_laughter,
            allow_soft_emoji=allow_emoji,
            max_combined_decorations=max_decorations,
            style_instruction=style_instruction,
        )
