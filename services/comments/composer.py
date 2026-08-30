"""Local Human-Like Comment Composer V4.1 (Community Rhythm).

Builds 20s community-style, rhythm-based comments from modular chunks
(OPEN + ANCHOR + REACTION + INTENT + SOFT_END).
Eliminates rigid full-sentence templates and guarantees real anchor evidence.
"""

from __future__ import annotations

import re
import unicodedata
import random
from collections import deque
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

from services.comments.categories import CATEGORY_POLICIES
from services.comments.entities import extract_entity_tokens, is_valid_subject
from services.comments.community_rhythm import FinalQualityGate, CommunityRhythmPreset, PresetLike
from services.comments.rhythm_bank import OPENERS, CATEGORY_REACTIONS


@dataclass(frozen=True)
class LocalCommentCandidate:
    body: str
    category: str
    anchor: str
    anchor_source: str  # "title" or "excerpt"
    evidence_span: str
    template_family: str
    opener_family: str
    reaction_family: str
    intent_family: str
    score: float = 0.0


class LocalComposerV41:
    """V4.1 Community Rhythm Composer Engine."""

    # History deque stores recent generation metadata to block 3-in-a-row repeats:
    # (normalized_text, opener_family, template_family, reaction_family, anchor)
    _recent_history: deque = deque(maxlen=30)

    @classmethod
    def reset_history(cls) -> None:
        cls._recent_history.clear()

    @staticmethod
    def _find_evidence_span(anchor: str, text: str) -> Optional[str]:
        """Find the surrounding snippet where anchor occurs in text."""
        norm_anchor = unicodedata.normalize("NFC", anchor)
        norm_text = unicodedata.normalize("NFC", text)
        idx = norm_text.find(norm_anchor)
        if idx == -1:
            return None
        start = max(0, idx - 15)
        end = min(len(norm_text), idx + len(norm_anchor) + 15)
        return norm_text[start:end].strip()

    @classmethod
    def extract_concrete_anchors(cls, title: str, excerpt: str) -> List[Tuple[str, str, str]]:
        """Extract concrete anchors with verified evidence from title & excerpt.

        Returns list of (anchor, anchor_source, evidence_span).
        """
        title_norm = unicodedata.normalize("NFC", title or "").strip()
        excerpt_norm = unicodedata.normalize("NFC", excerpt or "").strip()

        anchors: List[Tuple[str, str, str]] = []
        seen_tokens = set()

        # 1. From title
        title_tokens = extract_entity_tokens(title_norm)
        for token in title_tokens:
            if token not in seen_tokens and is_valid_subject(token):
                span = cls._find_evidence_span(token, title_norm)
                if span:
                    anchors.append((token, "title", span))
                    seen_tokens.add(token)

        # 2. From excerpt
        excerpt_tokens = extract_entity_tokens(excerpt_norm)
        for token in excerpt_tokens[:15]:
            if token not in seen_tokens and is_valid_subject(token):
                span = cls._find_evidence_span(token, excerpt_norm)
                if span:
                    anchors.append((token, "excerpt", span))
                    seen_tokens.add(token)

        return anchors

    @classmethod
    def detect_category(cls, title: str, excerpt: str) -> Tuple[str, float]:
        """Detect category and confidence margin."""
        title_tokens = extract_entity_tokens(title)
        excerpt_tokens = extract_entity_tokens(excerpt)

        cat_scores: Dict[str, int] = {cat: 0 for cat in CATEGORY_POLICIES if cat != "UNKNOWN_TOPIC"}

        for cat, policy in CATEGORY_POLICIES.items():
            if cat == "UNKNOWN_TOPIC":
                continue
            for kw in policy.keywords:
                for t in title_tokens:
                    if kw == t or (len(kw) > 2 and kw in t):
                        cat_scores[cat] += 8
                for t in excerpt_tokens[:35]:
                    if kw == t or (len(kw) > 2 and kw in t):
                        cat_scores[cat] += 2

        best_cat = "UNKNOWN_TOPIC"
        best_score = 0
        second_score = 0

        for cat, sc in sorted(cat_scores.items(), key=lambda x: x[1], reverse=True):
            if sc > best_score:
                second_score = best_score
                best_score = sc
                best_cat = cat

        if best_score < 4:
            return "COMMON", 0.5
        conf = 0.6 + min(0.38, (best_score - second_score) * 0.04)
        return best_cat, round(min(1.0, conf), 2)

    @classmethod
    def detect_category_and_subjects(
        cls,
        title: str,
        excerpt: str = ""
    ) -> Tuple[str, List[str], float]:
        """Detect category and extracted anchor list."""
        cat, conf = cls.detect_category(title, excerpt)
        anchors = [a[0] for a in cls.extract_concrete_anchors(title, excerpt)]
        return cat, anchors, conf

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed) if seed is not None else random.Random()

    @classmethod
    def score_candidate(cls, cand: LocalCommentCandidate, preset: PresetLike = CommunityRhythmPreset.COMMUNITY) -> float:
        """Score candidate naturalness and V13.1 rhythm suitability."""
        score = 0.0
        text = cand.body
        norm_text = unicodedata.normalize("NFC", text)
        length = len(norm_text)

        # 1. Real anchor evidence
        if cand.anchor and cand.evidence_span:
            score += 6.0

        # 2. Anchor in first 12 chars
        anchor_idx = norm_text.find(cand.anchor)
        if anchor_idx != -1 and anchor_idx <= 12:
            score += 3.0

        # 3. Length band
        if 18 <= length <= 45:
            score += 4.0
        elif length > 60:
            score -= 7.0

        # 4. Soft casual ending (~) or fragment ending
        if norm_text.endswith("~"):
            score += 2.0
        elif not any(norm_text.endswith(end) for end in ("다", "요", "음", "임")):
            score += 3.0

        # 5. Curated colloquial variants
        if any(v in norm_text for v in ("저두", "이쁜", "비쥬얼", "먹어보고", "가보고")):
            score += 2.0

        # 6. Penalties for formal, summary, or repetitive language
        if "." in norm_text or "。" in norm_text:
            score -= 8.0
        for formal in ("합니다", "입니다", "됩니다", "같습니다", "싶습니다"):
            if formal in norm_text:
                score -= 10.0
        for summary in ("전체적으로", "무엇보다", "특히", "구성이", "인상적"):
            if summary in norm_text:
                score -= 8.0

        # Repeated words
        if norm_text.count("저도") + norm_text.count("저두") >= 2:
            score -= 4.0
        if norm_text.count("진짜") + norm_text.count("너무") >= 2:
            score -= 4.0

        return score

    @classmethod
    def _is_repetition_blocked(cls, opener_family: str, template_family: str, norm_text: str) -> bool:
        """Check if candidate violates consecutive repetition rules."""
        # A non-empty opener cannot appear again among the last three comments.
        if opener_family != "none" and any(item[1] == opener_family for item in list(cls._recent_history)[-3:]):
            return True

        # Check identical text in recent history
        for item in cls._recent_history:
            if item[0] == norm_text:
                return True

        return False

    @classmethod
    def compose(
        cls,
        title: str,
        excerpt: str = "",
        preset: PresetLike = CommunityRhythmPreset.COMMUNITY,
        *,
        rng: Optional[random.Random] = None,
        praise_boost: bool = False,
        short_boost: bool = False,
    ) -> Tuple[Optional[LocalCommentCandidate], float]:
        """Compose a high-quality 20s community style comment candidate."""
        r = rng or random

        # 1. Extract concrete anchors
        anchors = cls.extract_concrete_anchors(title, excerpt)
        if not anchors:
            # Section 29: No generic fallback when no concrete anchor exists
            return None, 0.0

        # 2. Detect category
        cat, confidence = cls.detect_category(title, excerpt)

        policy = CATEGORY_POLICIES.get(cat)
        category_keywords = set(policy.keywords if policy else ())

        canonical_anchors: List[Tuple[str, str, str]] = []
        for raw_anchor, source, span in anchors:
            canonical = raw_anchor
            for keyword in sorted(category_keywords, key=len, reverse=True):
                if raw_anchor in {f"{keyword}{particle}" for particle in ("은", "는", "이", "가", "을", "를", "와", "과", "로", "의")}:
                    canonical = keyword
                    break
            canonical_anchors.append((canonical, source, span))
        anchors = canonical_anchors

        indexed_anchors = list(enumerate(anchors))

        def anchor_rank(indexed_item: Tuple[int, Tuple[str, str, str]]) -> Tuple[int, int, int, int]:
            index, item = indexed_item
            anchor, source, _span = item
            keyword_match = max(
                (3 if anchor == kw else 2 if len(kw) >= 2 and kw in anchor else 0)
                for kw in category_keywords
            ) if category_keywords else 0
            # Exact category entities dominate. For ties, prefer a title noun
            # close to the descriptive end of the title, then compact anchors.
            return keyword_match, 1 if source == "title" else 0, index, -abs(len(anchor) - 5)

        anchors = [item for _index, item in sorted(indexed_anchors, key=anchor_rank, reverse=True)]

        # Retrieve category reaction bank
        reactions = CATEGORY_REACTIONS.get(cat, CATEGORY_REACTIONS["COMMON"])
        if cat in {"FOOD", "CAFE", "TRAVEL", "HOBBY_GOODS"}:
            reactions = reactions + CATEGORY_REACTIONS["COMMON"]

        candidates: List[LocalCommentCandidate] = []

        # Generate combinatoric candidates across anchors
        for anchor, source, span in anchors[:4]:
            for opener_text, opener_fam in OPENERS:
                if cat not in {"FOOD", "CAFE", "TRAVEL", "HOBBY_GOODS"} and opener_fam not in {"none", "oh"}:
                    continue
                # Structure A: OPEN + REACTION
                for react_tmpl, react_fam in reactions:
                    if not cls._semantic_compatible(cat, anchor, span, react_fam):
                        continue
                    react_body = react_tmpl.replace("{anchor}", anchor)
                    body_a = f"{opener_text}{react_body}".strip()
                    cand_a = LocalCommentCandidate(
                        body=body_a,
                        category=cat,
                        anchor=anchor,
                        anchor_source=source,
                        evidence_span=span,
                        template_family="open_reaction" if opener_text else "reaction_only",
                        opener_family=opener_fam,
                        reaction_family=react_fam,
                        intent_family="none",
                    )
                    candidates.append(cand_a)

        # 3. Filter candidates through FinalQualityGate
        valid_candidates: List[LocalCommentCandidate] = []
        for cand in candidates:
            gate_res = FinalQualityGate.validate_final_text(
                cand.body, preset=preset, source="local",
                anchor_evidence=cand.evidence_span,
                semantic_compatibility=True,
                repetition_family=cand.reaction_family,
            )
            if gate_res.valid:
                cand_score = cls.score_candidate(cand, preset=preset)
                if cand.reaction_family.startswith(("food_", "cafe_", "travel_", "living_", "parenting_", "hobby_")):
                    cand_score += 2.0
                # Boosts
                if short_boost and len(cand.body) <= 35:
                    cand_score += 2.0
                if praise_boost and cand.template_family in ("open_reaction", "reaction_only"):
                    cand_score += 2.0
                valid_candidates.append(
                    LocalCommentCandidate(
                        body=cand.body,
                        category=cand.category,
                        anchor=cand.anchor,
                        anchor_source=cand.anchor_source,
                        evidence_span=cand.evidence_span,
                        template_family=cand.template_family,
                        opener_family=cand.opener_family,
                        reaction_family=cand.reaction_family,
                        intent_family=cand.intent_family,
                        score=cand_score,
                    )
                )

        if not valid_candidates:
            return None, 0.0

        # 4. Sort by score DESC, and filter repetition
        r.shuffle(valid_candidates)
        valid_candidates.sort(key=lambda c: c.score, reverse=True)

        best_cand: Optional[LocalCommentCandidate] = None
        for cand in valid_candidates:
            norm_b = unicodedata.normalize("NFC", cand.body)
            if not cls._is_repetition_blocked(cand.opener_family, cand.template_family, norm_b):
                if any(item[3] == cand.reaction_family for item in list(cls._recent_history)[-5:] if cand.reaction_family != "none"):
                    continue
                best_cand = cand
                break

        # Do not silently violate cooldown rules. Returning no local draft is
        # safer than reusing a recent sentence/reaction family.

        if best_cand:
            norm_b = unicodedata.normalize("NFC", best_cand.body)
            cls._recent_history.append(
                (norm_b, best_cand.opener_family, best_cand.template_family, best_cand.reaction_family, best_cand.anchor)
            )

        return best_cand, confidence

    @staticmethod
    def _semantic_compatible(category: str, anchor: str, evidence: str, reaction_family: str) -> bool:
        """Reject visually fluent but semantically wrong chunk combinations."""
        context = f"{anchor} {evidence}"
        if category == "FOOD":
            family_terms = {
                "food_glossy": ("윤기", "육즙"),
                "food_crispy": ("바삭", "튀김", "크리스피"),
                "food_thickness": ("두께", "두툼", "고기"),
                "food_soup_refreshing": ("국물", "찌개", "탕", "라면", "순두부"),
                "food_with_beer": ("맥주", "하이볼"),
            }
            required = family_terms.get(reaction_family)
            return not required or any(term in context for term in required)
        if category == "TRAVEL":
            family_terms = {
                "travel_ocean_healing": ("바다", "해변", "오션", "계곡", "물"),
                "travel_stay_vibe": ("숙소", "호텔", "펜션", "리조트"),
                "travel_trail_save": ("산책", "산책로", "숲길", "둘레길"),
                "travel_sunset_art": ("노을", "일몰", "선셋"),
                "travel_clear_water": ("바다", "해변", "계곡", "물", "강"),
            }
            required = family_terms.get(reaction_family)
            return not required or any(term in context for term in required)
        if category != "CAFE":
            return True
        space_terms = ("뷰", "창가", "오션", "바다", "인테리어", "공간", "통창", "마당", "테라스", "루프탑", "층고", "햇살")
        dessert_terms = ("디저트", "케이크", "빙수", "크로플", "소금빵", "베이커리", "타르트", "까눌레", "크림", "빵", "몽블랑")
        drink_terms = ("커피", "라떼", "에이드", "차", "원두", "에스프레소", "아메리카노", "음료")
        if reaction_family in {"cafe_view_what", "cafe_window_seat", "cafe_interior_sensible", "cafe_sunlight_warm", "cafe_healing_space"}:
            return any(term in anchor for term in space_terms)
        if reaction_family in {"cafe_dessert_pretty", "cafe_cream_taste", "cafe_lineup_visit", "cafe_sugar_charge"}:
            return any(term in anchor for term in dessert_terms)
        if reaction_family == "cafe_drink_color":
            return any(term in anchor for term in drink_terms) and any(term in context for term in ("색감", "딸기", "말차", "에이드", "과일"))
        if reaction_family == "cafe_coffee_aroma":
            return any(term in anchor for term in ("커피", "원두", "에스프레소", "아메리카노", "드립"))
        if reaction_family in {"cafe_color_vibe", "cafe_plating_cute"}:
            return any(term in anchor for term in dessert_terms + drink_terms)
        return True

    @classmethod
    def reset_history(cls):
        """Reset anti-repetition session history."""
        cls._recent_history.clear()


# Backward-compatible alias for existing callers
HumanLikeComposerV31 = LocalComposerV41
