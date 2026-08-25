import re
from collections import deque
from typing import List, Dict, Tuple, Optional
from services.comments.intents import ReactionIntent, FirstPersonIntent, CommentCandidate
from services.comments.categories import CATEGORY_POLICIES, CategoryPolicy
from services.comments.validators import PositiveSafetyValidator
from services.comments.action_forms import CATEGORY_ACTION_FORMS, ActionForms
from services.comments.entities import extract_entity_tokens, is_valid_subject


class HumanLikeComposerV31:
    """
    Human-Like Comment Composer v3.1 (Stabilized)
    - Category x Reaction Matrix 기반 12~18개 다중 후보 생성
    - ActionForms 기반 자연스러운 한국어 문법 보장
    - META Subject(맛집, 후기 등) 자동 제외 및 구체 엔티티 바인딩
    - 긍정 안전성 검증 및 결정론적 최고점(Deterministic Best) 랭킹 엔진
    """

    _recent_comments: deque = deque(maxlen=15)
    _recent_openers: deque = deque(maxlen=6)

    @staticmethod
    def _match_keyword(kw: str, token: str) -> bool:
        if len(kw) <= 2:
            return token == kw
        return (token == kw) or (kw in token)

    @classmethod
    def detect_category_and_subjects(cls, title: str, excerpt: str) -> Tuple[str, List[str], float]:
        title_s = title.strip()
        excerpt_s = excerpt.strip()

        title_tokens = extract_entity_tokens(title_s)
        excerpt_tokens = extract_entity_tokens(excerpt_s)

        cat_scores: Dict[str, int] = {cat: 0 for cat in CATEGORY_POLICIES if cat != "UNKNOWN_TOPIC"}

        # 1. 카테고리 점수 계산
        for cat, policy in CATEGORY_POLICIES.items():
            if cat == "UNKNOWN_TOPIC":
                continue
            for kw in policy.keywords:
                for t in title_tokens:
                    if cls._match_keyword(kw, t):
                        cat_scores[cat] += 8
                for t in excerpt_tokens[:35]:
                    if cls._match_keyword(kw, t):
                        cat_scores[cat] += 2

        best_cat = "UNKNOWN_TOPIC"
        best_score = 0
        second_score = 0

        for cat, sc in sorted(cat_scores.items(), key=lambda x: x[1], reverse=True):
            if sc > best_score:
                second_score = best_score
                best_score = sc
                best_cat = cat

        # Confidence margin 계산 (1위와 2위 점수 차이가 미미하면 UNKNOWN_TOPIC)
        if best_score < 4:
            best_cat = "UNKNOWN_TOPIC"
            confidence = 0.5
        else:
            confidence = 0.6 + min(0.38, (best_score - second_score) * 0.04)

        # 2. Subject(핵심 대상) 후보 추출 (META 단어 제외)
        subjects: List[str] = []
        if best_cat != "UNKNOWN_TOPIC":
            policy = CATEGORY_POLICIES[best_cat]
            for token in title_tokens:
                if any(cls._match_keyword(kw, token) for kw in policy.keywords) and is_valid_subject(token) and token not in subjects:
                    subjects.append(token)

        for token in title_tokens:
            if is_valid_subject(token) and token not in subjects:
                subjects.append(token)

        for token in excerpt_tokens[:10]:
            if is_valid_subject(token) and token not in subjects:
                subjects.append(token)

        if not subjects:
            subjects = ["공간" if best_cat in ("CAFE", "TRAVEL", "INTERIOR_HOME") else "내용"]

        return best_cat, subjects[:3], round(min(1.0, confidence), 2)

    @classmethod
    def generate_candidates(
        cls,
        title: str,
        excerpt: str,
        category: str,
        subjects: List[str],
        confidence: float = 0.8
    ) -> List[CommentCandidate]:
        """Category x Reaction Matrix에 기반하여 12~18개의 후보 댓글을 생성"""
        primary_subj = subjects[0] if subjects else "분위기"
        actions = CATEGORY_ACTION_FORMS.get(category, CATEGORY_ACTION_FORMS["UNKNOWN_TOPIC"])

        candidates: List[CommentCandidate] = []

        # 1. PRAISE (긍정 감탄 칭찬) - 3~4개
        if category == "HOBBY_GOODS":
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 실물이 정말 깜찍하고 예쁘네요. 사진 보니까 눈길이 확 가요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_hobby_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 캐릭터 비주얼이 너무 귀엽네요. 보는 내내 미소가 지어져요!",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_hobby_2"
            ))
        elif category in ("FOOD", "CAFE"):
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 비주얼이 진짜 정갈하고 맛있어 보여요. 사진 보니까 군침 도네요!",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_food_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 색감부터 너무 예쁘네요. 사진 분위기가 참 따뜻하고 좋아요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_cafe_1"
            ))
        elif category == "TRAVEL":
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 풍경이 정말 평화로워 보이네요. 사진만 봐도 마음이 편안해져요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_travel_1"
            ))
            candidates.append(CommentCandidate(
                body=f"탁 트인 분위기가 너무 멋지네요. {primary_subj} 풍경 참 좋아 보여요!",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_travel_2"
            ))
        else:
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 내용이 눈에 쏙 들어오네요. 사진이랑 같이 보니 느낌이 잘 전해져요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_gen_1"
            ))
            candidates.append(CommentCandidate(
                body=f"전체적으로 깔끔하고 알차서 기분 좋게 읽기 좋네요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_gen_2"
            ))

        # 2. DETAIL_PRAISE (관찰 디테일 칭찬) - 3~4개
        if category in ("FOOD", "CAFE"):
            candidates.append(CommentCandidate(
                body=f"음식들이 하나같이 다 깔끔해 보여요. {primary_subj} 조합이 참 좋아 보이네요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_food_1"
            ))
            candidates.append(CommentCandidate(
                body=f"공간 분위기도 아늑하고 {primary_subj}도 정성 가득해 보여서 눈길이 가네요!",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_cafe_1"
            ))
        elif category == "HOBBY_GOODS":
            candidates.append(CommentCandidate(
                body=f"사진으로 디테일하게 보여주셔서 구경하는 재미가 쏠쏠하네요. {primary_subj} 너무 귀여워요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_hobby_1"
            ))
        elif category == "TRAVEL":
            candidates.append(CommentCandidate(
                body=f"산책하듯 여유롭게 둘러볼 수 있는 점이 참 좋네요. {primary_subj} 운치 있어 보여요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_travel_1"
            ))
        else:
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 특징이 한눈에 잘 보여서 어떤 내용인지 편하게 보기 좋네요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_gen_1"
            ))

        # 3. TRY_INTENT ('나'의 미래 시도 의향) - 3개 (ActionForms 활용)
        for idx, phrase in enumerate(actions.try_phrases[:3]):
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 보니까 {phrase}",
                category=category, reaction_intent=ReactionIntent.TRY_INTENT,
                first_person_intent=FirstPersonIntent.WANT_TO_EAT if category == "FOOD" else FirstPersonIntent.WANT_TO_VISIT,
                subject=primary_subj, template_id=f"try_action_{idx+1}"
            ))

        # 4. PLAN_INTENT ('나'의 계획 편입) - 3개 (ActionForms 활용)
        for idx, phrase in enumerate(actions.plan_phrases[:3]):
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 기억해뒀다가 {phrase}",
                category=category, reaction_intent=ReactionIntent.PLAN_INTENT,
                first_person_intent=FirstPersonIntent.PLAN_TO_VISIT,
                subject=primary_subj, template_id=f"plan_action_{idx+1}"
            ))

        # 5. PREFERENCE ('나'의 취향 공감) - 2개
        if category in ("HOBBY_GOODS", "BEAUTY", "FASHION"):
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 스타일이 참 깔끔하네요. 저도 이런 자연스러운 느낌 좋아해요 :)",
                category=category, reaction_intent=ReactionIntent.PREFERENCE,
                first_person_intent=FirstPersonIntent.LIKE_THIS_STYLE, subject=primary_subj, template_id="pref_style_1"
            ))
        else:
            candidates.append(CommentCandidate(
                body=f"전체적인 분위기가 참 마음에 드네요. 저도 이런 따뜻한 무드 좋아해요 :)",
                category=category, reaction_intent=ReactionIntent.PREFERENCE,
                first_person_intent=FirstPersonIntent.LIKE_THIS_MOOD, subject=primary_subj, template_id="pref_mood_1"
            ))

        # 6. EMPATHY & INFO_REACTION - 2개
        candidates.append(CommentCandidate(
            body=f"{primary_subj} 보니까 괜히 기분 좋아지네요. 이런 분위기 좋아하는 분들 정말 많을 것 같아요 :)",
            category=category, reaction_intent=ReactionIntent.EMPATHY,
            first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="empathy_common_1"
        ))
        candidates.append(CommentCandidate(
            body=f"{primary_subj} 정보까지 함께 볼 수 있어서 가기 전에 참고하기 참 좋겠어요 :)",
            category=category, reaction_intent=ReactionIntent.INFO_REACTION,
            first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="info_common_1"
        ))

        return candidates

    @classmethod
    def rank_and_select(
        cls,
        candidates: List[CommentCandidate],
        category: str,
        praise_boost: bool = False,
        short_boost: bool = False
    ) -> CommentCandidate:
        """후보들을 검증하고 점수화하여 최고 품질의 댓글을 결정론적(Deterministic)으로 선택"""
        policy = CATEGORY_POLICIES.get(category, CATEGORY_POLICIES["UNKNOWN_TOPIC"])
        weights = policy.reaction_weights

        scored_candidates: List[CommentCandidate] = []

        for cand in candidates:
            # 1. 안전성 검증
            valid, reject_reason = PositiveSafetyValidator.validate_candidate(cand)
            if not valid:
                cand.rejected = True
                cand.rejection_reason = reject_reason
                continue

            # 2. 기본 가중치
            base_w = weights.get(cand.reaction_intent, 1.0)
            score = 10.0 * base_w

            # 3. 칭찬 및 긍정 부스트
            if cand.reaction_intent in (ReactionIntent.PRAISE, ReactionIntent.DETAIL_PRAISE):
                score += 4.0
            if praise_boost and cand.reaction_intent in (ReactionIntent.PRAISE, ReactionIntent.DETAIL_PRAISE):
                score += 8.0

            # 4. 길이 적합도
            length = len(cand.body)
            if 30 <= length <= 65:
                score += 3.0
            if short_boost and length <= 45:
                score += 6.0

            # 5. 최근 생성 문장 유사도 및 오프너 중복 패널티
            for prev in cls._recent_comments:
                if cand.body[:15] == prev[:15] or cand.body == prev:
                    score -= 10.0

            opener = cand.body.split()[0] if cand.body.split() else ""
            if opener and list(cls._recent_openers).count(opener) >= 2:
                score -= 4.0

            cand.score = score
            scored_candidates.append(cand)

        if not scored_candidates:
            fallback_text = "사진 분위기가 참 따뜻하고 편안해서 좋네요. 기분 좋게 잘 읽었습니다 :)"
            fallback_cand = CommentCandidate(
                body=fallback_text, category=category,
                reaction_intent=ReactionIntent.PRAISE, first_person_intent=FirstPersonIntent.NONE,
                subject="", template_id="safe_fallback", score=5.0
            )
            return fallback_cand

        # 결정론적 최고점 정렬 (점수 1위 선택)
        scored_candidates.sort(key=lambda x: x.score, reverse=True)
        chosen = scored_candidates[0]

        cls._recent_comments.append(chosen.body)
        opener = chosen.body.split()[0] if chosen.body.split() else ""
        if opener:
            cls._recent_openers.append(opener)

        return chosen

    @classmethod
    def compose(
        cls,
        title: str,
        excerpt: str = "",
        praise_boost: bool = False,
        short_boost: bool = False
    ) -> Tuple[CommentCandidate, float]:
        """게시글 맥락으로부터 최고 품질 댓글과 실제 분류 확신도 반환"""
        category, subjects, confidence = cls.detect_category_and_subjects(title, excerpt)
        candidates = cls.generate_candidates(title, excerpt, category, subjects, confidence=confidence)
        chosen = cls.rank_and_select(candidates, category, praise_boost=praise_boost, short_boost=short_boost)
        return chosen, confidence
