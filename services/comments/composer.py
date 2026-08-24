import re
import random
from collections import deque
from typing import List, Dict, Tuple, Optional
from services.comments.intents import ReactionIntent, FirstPersonIntent, CommentCandidate
from services.comments.categories import CATEGORY_POLICIES, CategoryPolicy
from services.comments.validators import PositiveSafetyValidator


class HumanLikeComposerV31:
    """
    Human-Like Comment Composer v3.1
    Category x Reaction Matrix 기반 다중 후보 생성 + 긍정 안전성 검증 + 랭킹 엔진
    """

    STOPWORDS = {
        "오늘", "이번", "정말", "너무", "그리고", "하지만", "그래서", "포스팅", "블로그", "후기",
        "리뷰", "사진", "정보", "방문", "다녀왔", "했어요", "합니다", "입니다", "있어요",
        "있습니다", "같아요", "내돈내산", "일상", "생각", "추천", "공유", "안내", "위치"
    }

    _recent_comments: deque = deque(maxlen=15)
    _recent_openers: deque = deque(maxlen=6)
    _recent_endings: deque = deque(maxlen=6)

    @classmethod
    def extract_tokens(cls, text: str) -> List[str]:
        tokens = re.findall(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·&+\-]{1,15}", text)
        return [t for t in tokens if t not in cls.STOPWORDS and len(t) >= 2]

    @staticmethod
    def _match_keyword(kw: str, token: str) -> bool:
        """2글자 이하는 정확한 토큰 일치, 3글자 이상은 포함 여부 검사 (substring 오분류 차단)"""
        if len(kw) <= 2:
            return token == kw
        return (token == kw) or (kw in token)

    @classmethod
    def detect_category_and_subjects(cls, title: str, excerpt: str) -> Tuple[str, List[str], float]:
        title_s = title.strip()
        excerpt_s = excerpt.strip()

        title_tokens = cls.extract_tokens(title_s)
        excerpt_tokens = cls.extract_tokens(excerpt_s)

        cat_scores: Dict[str, int] = {cat: 0 for cat in CATEGORY_POLICIES if cat != "UNKNOWN_TOPIC"}

        # 1. 카테고리 점수 계산 (HOBBY_GOODS, FOOD, CAFE 등 우선순위별 정밀 토큰 매칭)
        for cat, policy in CATEGORY_POLICIES.items():
            if cat == "UNKNOWN_TOPIC":
                continue
            for kw in policy.keywords:
                # 제목 토큰 매칭 (높은 가중치)
                for t in title_tokens:
                    if cls._match_keyword(kw, t):
                        cat_scores[cat] += 8
                # 본문 앞부분 토큰 매칭
                for t in excerpt_tokens[:35]:
                    if cls._match_keyword(kw, t):
                        cat_scores[cat] += 2

        best_cat = "UNKNOWN_TOPIC"
        best_score = 0
        for cat, sc in cat_scores.items():
            if sc > best_score:
                best_score = sc
                best_cat = cat

        # 2. Subject(핵심 대상) 후보 추출 (카테고리 매칭 명사 우선 탐색)
        subjects: List[str] = []
        if best_cat != "UNKNOWN_TOPIC":
            policy = CATEGORY_POLICIES[best_cat]
            # 카테고리 대표 키워드와 직접 매칭된 토큰 우선
            for token in title_tokens:
                if any(cls._match_keyword(kw, token) for kw in policy.keywords) and token not in subjects:
                    subjects.append(token)

        # 제목의 나머지 유효 명사 토큰 추가
        for token in title_tokens:
            if len(token) >= 2 and token not in subjects:
                subjects.append(token)

        if not subjects and excerpt_tokens:
            subjects.append(excerpt_tokens[0])

        confidence = 0.5 + min(0.45, best_score * 0.05)
        return best_cat, subjects[:3], round(min(1.0, confidence), 2)

    @classmethod
    def generate_candidates(
        cls,
        title: str,
        excerpt: str,
        category: str,
        subjects: List[str]
    ) -> List[CommentCandidate]:
        """Category x Reaction Matrix에 기반하여 12~18개의 후보 댓글을 생성"""
        policy = CATEGORY_POLICIES.get(category, CATEGORY_POLICIES["UNKNOWN_TOPIC"])
        primary_subj = subjects[0] if subjects else "포스팅"
        action = policy.actions[0] if policy.actions else "참고해보다"

        candidates: List[CommentCandidate] = []

        # 1. HOBBY_GOODS (캐릭터/키링/굿즈 전용)
        if category == "HOBBY_GOODS":
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 디자인이 정말 귀엽고 예쁘네요. 사진 보니까 저도 하나 뽑아보고 싶어요 :)",
                category=category, reaction_intent=ReactionIntent.TRY_INTENT,
                first_person_intent=FirstPersonIntent.WANT_TO_VISIT, subject=primary_subj, template_id="hobby_try_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 종류가 다양해서 보는 재미가 있네요. 이런 캐릭터 굿즈 저도 참 좋아해요!",
                category=category, reaction_intent=ReactionIntent.PREFERENCE,
                first_person_intent=FirstPersonIntent.LIKE_THIS_STYLE, subject=primary_subj, template_id="hobby_pref_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 실물 비주얼이 너무 깜찍하네요. 편의점 갈 때 기억해뒀다 한번 찾아봐야겠어요 :)",
                category=category, reaction_intent=ReactionIntent.PLAN_INTENT,
                first_person_intent=FirstPersonIntent.PLAN_TO_VISIT, subject=primary_subj, template_id="hobby_plan_1"
            ))
            candidates.append(CommentCandidate(
                body=f"사진으로 디테일하게 보여주셔서 구경하는 재미가 쏠쏠하네요. {primary_subj} 너무 귀여워요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="hobby_detail_1"
            ))

        # 2. FOOD
        elif category == "FOOD":
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 비주얼이 진짜 정갈하고 맛있어 보여요. 사진 보니까 군침 도네요!",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_food_1"
            ))
            candidates.append(CommentCandidate(
                body=f"음식들이 하나같이 다 깔끔해 보여요. {primary_subj} 조합이 참 좋아 보이네요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_food_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 진짜 맛있어 보여요. 다음에 가면 저도 꼭 {action} 싶네요 :)",
                category=category, reaction_intent=ReactionIntent.TRY_INTENT,
                first_person_intent=FirstPersonIntent.WANT_TO_EAT, subject=primary_subj, template_id="try_food_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 기억해뒀다가 이쪽 가게 되면 코스에 꼭 넣어봐야겠어요 :)",
                category=category, reaction_intent=ReactionIntent.PLAN_INTENT,
                first_person_intent=FirstPersonIntent.PLAN_TO_VISIT, subject=primary_subj, template_id="plan_food_1"
            ))

        # 3. CAFE
        elif category == "CAFE":
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 색감부터 너무 예쁘네요. 사진 분위기가 참 따뜻하고 좋아요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_cafe_1"
            ))
            candidates.append(CommentCandidate(
                body=f"공간 분위기도 아늑하고 {primary_subj}도 정성 가득해 보여서 눈길이 가네요!",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_cafe_1"
            ))
            candidates.append(CommentCandidate(
                body=f"공간이 참 편안해 보이네요. 저도 이런 아늑한 분위기 카페 좋아해서 가보고 싶어요 :)",
                category=category, reaction_intent=ReactionIntent.PREFERENCE,
                first_person_intent=FirstPersonIntent.LIKE_THIS_MOOD, subject=primary_subj, template_id="pref_cafe_1"
            ))
            candidates.append(CommentCandidate(
                body=f"사진 보니까 {primary_subj} 너무 달콤해 보여요. 저도 한번 {action} 싶네요!",
                category=category, reaction_intent=ReactionIntent.TRY_INTENT,
                first_person_intent=FirstPersonIntent.WANT_TO_DRINK, subject=primary_subj, template_id="try_cafe_1"
            ))

        # 4. TRAVEL
        elif category == "TRAVEL":
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 풍경이 정말 평화로워 보이네요. 사진만 봐도 마음이 편안해져요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_travel_1"
            ))
            candidates.append(CommentCandidate(
                body=f"산책하듯 여유롭게 둘러볼 수 있는 점이 참 좋네요. {primary_subj} 운치 있어 보여요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_travel_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 분위기가 참 좋네요. 저도 날씨 좋을 때 천천히 둘러보러 가보고 싶어요 :)",
                category=category, reaction_intent=ReactionIntent.TRY_INTENT,
                first_person_intent=FirstPersonIntent.WANT_TO_VISIT, subject=primary_subj, template_id="try_travel_1"
            ))
            candidates.append(CommentCandidate(
                body=f"다음에 이쪽으로 여행 가게 되면 {primary_subj} 코스에 꼭 넣어봐야겠네요!",
                category=category, reaction_intent=ReactionIntent.PLAN_INTENT,
                first_person_intent=FirstPersonIntent.PLAN_TO_VISIT, subject=primary_subj, template_id="plan_travel_1"
            ))

        # 5. BEAUTY / FASHION
        elif category in ("BEAUTY", "FASHION"):
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 느낌이 자연스럽고 참 예쁘네요. 전체적인 분위기가 너무 잘 어울려요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_beauty_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 스타일이 참 깔끔하네요. 저도 이런 자연스러운 느낌 좋아해요 :)",
                category=category, reaction_intent=ReactionIntent.PREFERENCE,
                first_person_intent=FirstPersonIntent.LIKE_THIS_STYLE, subject=primary_subj, template_id="pref_style_1"
            ))

        # 6. GENERAL / 기타
        else:
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 내용이 눈에 쏙 들어오네요. 사진이랑 같이 보니 느낌이 잘 전해져요 :)",
                category=category, reaction_intent=ReactionIntent.PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="praise_gen_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 특징이 한눈에 잘 보여서 어떤 내용인지 편하게 보기 좋네요 :)",
                category=category, reaction_intent=ReactionIntent.DETAIL_PRAISE,
                first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="detail_gen_1"
            ))
            candidates.append(CommentCandidate(
                body=f"{primary_subj} 방법이 좋아 보여요. 저도 기회 되면 한번 {action} 싶네요 :)",
                category=category, reaction_intent=ReactionIntent.TRY_INTENT,
                first_person_intent=FirstPersonIntent.CURIOUS_TO_SEE, subject=primary_subj, template_id="try_gen_1"
            ))

        # 공통 공감 및 정보 반응 후보 추가
        candidates.append(CommentCandidate(
            body=f"{primary_subj} 보니까 괜히 기분 좋아지네요. 이런 분위기 좋아하는 분들 정말 많을 것 같아요 :)",
            category=category, reaction_intent=ReactionIntent.EMPATHY,
            first_person_intent=FirstPersonIntent.NONE, subject=primary_subj, template_id="empathy_common_1"
        ))
        candidates.append(CommentCandidate(
            body=f"{primary_subj} 정보까지 함께 볼 수 있어서 참고하기 참 좋겠어요 :)",
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
        """후보들을 검증하고 점수화하여 최적의 긍정 인간형 댓글을 선택"""
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

            # 5. 최근 생성 문장 유사도 및 오프너/엔딩 중복 패널티
            for prev in cls._recent_comments:
                if cand.body[:15] == prev[:15] or cand.body == prev:
                    score -= 8.0

            opener = cand.body.split()[0] if cand.body.split() else ""
            if opener and list(cls._recent_openers).count(opener) >= 2:
                score -= 3.0

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

        scored_candidates.sort(key=lambda x: x.score, reverse=True)
        top_candidates = scored_candidates[:min(3, len(scored_candidates))]
        chosen = random.choice(top_candidates)

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
    ) -> CommentCandidate:
        """게시글의 제목과 본문 맥락으로부터 최고 품질의 긍정 인간형 댓글 생성"""
        category, subjects, conf = cls.detect_category_and_subjects(title, excerpt)
        candidates = cls.generate_candidates(title, excerpt, category, subjects)
        return cls.rank_and_select(candidates, category, praise_boost=praise_boost, short_boost=short_boost)
