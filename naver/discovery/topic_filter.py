import re
from dataclasses import dataclass
from typing import Optional, Set, Tuple

TARGET_DISCOVERY_CATEGORIES: Set[str] = {
    "FOOD", "CAFE", "PARENTING", "LIVING", "INTERIOR_HOME", "TRAVEL", "LIFESTYLE"
}

@dataclass(frozen=True)
class TopicDecision:
    allowed: bool
    blocked_category: Optional[str] = None
    evidence: Tuple[str, ...] = ()
    stage: str = "card"

class DiscoveryTopicFilter:
    """Recommendation/search exclusion gate for finance, camera gear and tech."""

    _PATTERNS = {
        "finance": (
            r"주식", r"증시", r"종목", r"ETF", r"코인", r"비트코인", r"금리", r"대출",
            r"청약", r"분양", r"배당", r"채권", r"환율", r"재테크", r"포트폴리오", r"지원금", r"환급금",
        ),
        "camera": (
            r"카메라", r"미러리스", r"DSLR", r"단렌즈", r"조리개",
            r"바디\s*(?:리뷰|성능|스펙)", r"출사", r"캐논\s*(?:EOS|R\d)",
            r"소니\s*(?:A\d|알파|FE)", r"니콘\s*(?:Z\d|NIKKOR)", r"후지필름\s*(?:X-|GFX)",
            r"탐론\s*\d", r"시그마\s*\d",
        ),
        "tech": (
            r"스마트폰", r"아이폰\s*\d", r"갤럭시\s*(?:S|Z|A)\d", r"노트북", r"맥북",
            r"아이패드", r"태블릿", r"CPU", r"GPU", r"그래픽카드", r"조립\s*PC",
            r"모니터\s*(?:리뷰|스펙|성능)", r"이어폰\s*(?:리뷰|스펙|성능)",
            r"헤드폰\s*(?:리뷰|스펙|성능)", r"벤치마크",
        ),
    }
    _WEAK_PATTERNS = {
        "camera": (r"렌즈", r"망원", r"카메라\s*바디", r"촬영\s*장비"),
        "tech": (r"언박싱", r"개봉기", r"기기\s*성능", r"전자기기"),
    }

    @classmethod
    def evaluate(cls, title: str, snippet: str = "", *, stage: str = "card") -> TopicDecision:
        combined = " ".join(part.strip() for part in (title or "", snippet or "") if part and part.strip())
        if not combined:
            return TopicDecision(True, stage=stage)
        for category, patterns in cls._PATTERNS.items():
            evidence = []
            for pattern in patterns:
                match = re.search(pattern, combined, flags=re.IGNORECASE)
                if match:
                    token = match.group(0)
                    if token not in evidence:
                        evidence.append(token)
            if evidence:
                return TopicDecision(False, category, tuple(evidence), stage)
            weak_evidence = []
            for pattern in cls._WEAK_PATTERNS.get(category, ()):
                match = re.search(pattern, combined, flags=re.IGNORECASE)
                if match and match.group(0) not in weak_evidence:
                    weak_evidence.append(match.group(0))
            if len(weak_evidence) >= 2:
                return TopicDecision(False, category, tuple(weak_evidence), stage)
        return TopicDecision(True, stage=stage)

    @classmethod
    def is_allowed(cls, title: str, snippet: str = "") -> Tuple[bool, str]:
        decision = cls.evaluate(title, snippet, stage="card")
        if decision.allowed:
            return True, "allowed"
        return False, f"blocked_{decision.blocked_category}: {', '.join(decision.evidence)}"
