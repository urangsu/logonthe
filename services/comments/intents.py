from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


class ReactionIntent(str, Enum):
    PRAISE = "praise"                 # 기본 긍정 칭찬/감탄
    DETAIL_PRAISE = "detail_praise"   # 구체 포인트/방식 관찰 칭찬
    TRY_INTENT = "try_intent"         # '나'의 시도/체험/방문 의향 (미래형)
    PLAN_INTENT = "plan_intent"       # '나'의 여행/주문/루틴 계획 편입
    PREFERENCE = "preference"         # '나'의 취향/선호 공감 (현재형)
    EMPATHY = "empathy"               # 친근한 공감 및 반응
    INFO_REACTION = "info_reaction"   # 본문 공유 정보에 대한 긍정적 반응
    QUESTION = "question"             # 부담 없는 자연스러운 질문 (극소수)


class FirstPersonIntent(str, Enum):
    NONE = "none"
    WANT_TO_VISIT = "want_to_visit"
    PLAN_TO_VISIT = "plan_to_visit"
    WANT_TO_EAT = "want_to_eat"
    WANT_TO_DRINK = "want_to_drink"
    LIKE_THIS_STYLE = "like_this_style"
    LIKE_THIS_MOOD = "like_this_mood"
    WOULD_CHOOSE = "would_choose"
    CURIOUS_TO_SEE = "curious_to_see"


@dataclass
class CommentCandidate:
    body: str
    category: str
    reaction_intent: ReactionIntent
    first_person_intent: FirstPersonIntent
    subject: str
    template_id: str
    evidence: Optional[str] = None
    score: float = 0.0
    positivity_score: float = 1.0
    rejected: bool = False
    rejection_reason: Optional[str] = None
