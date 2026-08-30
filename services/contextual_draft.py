from dataclasses import dataclass
from typing import Optional, List, Tuple
from services.comments.intents import ReactionIntent, FirstPersonIntent
from services.comments.composer import LocalComposerV41, LocalCommentCandidate
from services.comments.community_rhythm import CommunityRhythmPreset, PresetLike


@dataclass
class ContextualDraftResult:
    body: str
    category: str
    subject: str
    template_id: str
    intent: FirstPersonIntent
    reaction_intent: ReactionIntent
    confidence: float
    anchor: str = ""
    evidence_span: str = ""
    template_family: str = ""


class ContextualDraftEngine:
    """
    Human-Like Comment Composer V4.1 Engine Wrapper
    - Modular chunk composition (OPEN + ANCHOR + REACTION + INTENT)
    - Zero generic fallback when no concrete anchor is found
    - 20s community rhythm without periods, emoticons, formal endings
    """

    @classmethod
    def generate(
        cls,
        title: str,
        excerpt: str = "",
        praise_boost: bool = False,
        short_boost: bool = False,
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        rng=None,
    ) -> Optional[ContextualDraftResult]:
        """제목과 본문을 기반으로 고품질 20대 커뮤니티형 댓글 초안 생성"""
        candidate, confidence = LocalComposerV41.compose(
            title=title,
            excerpt=excerpt,
            preset=preset,
            rng=rng,
            praise_boost=praise_boost,
            short_boost=short_boost,
        )

        if not candidate:
            return None

        return ContextualDraftResult(
            body=candidate.body,
            category=candidate.category,
            subject=candidate.anchor,
            template_id=candidate.template_family,
            intent=FirstPersonIntent.NONE,
            reaction_intent=ReactionIntent.PRAISE,
            confidence=confidence,
            anchor=candidate.anchor,
            evidence_span=candidate.evidence_span,
            template_family=candidate.template_family,
        )
