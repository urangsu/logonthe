from dataclasses import dataclass
from typing import Optional, List, Tuple
from services.comments.intents import ReactionIntent, FirstPersonIntent, CommentCandidate
from services.comments.composer import HumanLikeComposerV31


@dataclass
class ContextualDraftResult:
    body: str
    category: str
    subject: str
    template_id: str
    intent: FirstPersonIntent
    reaction_intent: ReactionIntent
    confidence: float  # 실제 분류 모델 신뢰도


class ContextualDraftEngine:
    """
    Human-Like Comment Composer v3.1 Engine Wrapper
    - 16대 주제별 카테고리 x 8대 반응유형(ReactionIntent) 결합
    - ActionForms 기반 자연스러운 한국어 구문 및 META Subject 필터링
    - '나'의 취향/미래 의향이 가미된 자연스러운 1~2문장의 한국어 댓글 생성
    - 과거 경험 위조 / 부정적 평가 / 상투적 매크로 전면 배제
    """

    @classmethod
    def generate(
        cls,
        title: str,
        excerpt: str = "",
        praise_boost: bool = False,
        short_boost: bool = False
    ) -> ContextualDraftResult:
        """제목과 본문을 기반으로 고품질 긍정 인간형 댓글 초안 생성"""
        candidate, confidence = HumanLikeComposerV31.compose(
            title=title,
            excerpt=excerpt,
            praise_boost=praise_boost,
            short_boost=short_boost
        )

        return ContextualDraftResult(
            body=candidate.body,
            category=candidate.category,
            subject=candidate.subject,
            template_id=candidate.template_id,
            intent=candidate.first_person_intent,
            reaction_intent=candidate.reaction_intent,
            confidence=confidence
        )
