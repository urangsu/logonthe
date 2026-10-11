"""services/reaction_planner.py
P0/P1: 글 분류 및 반응 계획 수립 서비스 (ReactionContextPlanner & ReactionPlan).

분류 -> 반응계획 -> 짧은 프롬프트 -> 검증 4단 구조의 1~2단계를 담당.
Gemini에게 5개 카테고리 전체 지침과 장황한 규칙을 넘기지 않고,
Python에서 근거 점수제(Evidence Scoring)로 도메인(FOOD, PLACE, PRODUCT, SERVICE, PERSONAL, GENERAL)과
반응 방식(reaction_mode)을 결정하여 단 하나의 맞춤형 reaction_instruction을 생성한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from services.food_comment_focus import FoodCommentFocus


@dataclass(frozen=True)
class ReactionPlan:
    """단일 게시글에 대해 확정된 반응 계획."""
    domain: str  # FOOD | PRODUCT | SERVICE | PLACE | PERSONAL | GENERAL
    primary_anchor: str
    secondary_anchor: str
    reaction_mode: str  # taste_reaction | combination_curiosity | visual_reaction | future_interest | feature_reaction | service_reaction | place_observation | writer_feeling | detail_observation
    evidence: str
    reaction_instruction: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "primary_anchor": self.primary_anchor,
            "secondary_anchor": self.secondary_anchor,
            "reaction_mode": self.reaction_mode,
            "evidence": self.evidence,
            "reaction_instruction": self.reaction_instruction,
        }


class ReactionContextPlanner:
    """
    글 원문/선별 발췌를 분석하여 근거 점수제 기반으로 도메인과 reaction_mode를 확정하는 플래너.
    """

    # 맛/식감 키워드
    TASTE_TEXTURE_KEYWORDS = (
        "바삭", "촉촉", "쫀득", "쫄깃", "고소", "달달", "매콤", "얼큰", "단짠",
        "담백", "느끼", "알싸", "육즙", "풍미", "진하", "시원하", "칼칼", "부드러",
        "눅눅", "기름", "불향", "숯불향", "달콤", "새콤", "짭짤", "식감"
    )

    # 명시적 조합 키워드 (조합 자체를 표현하는 단어)
    EXPLICIT_COMBINATION_PHRASES = (
        "조합", "어우러", "같이 먹", "함께 먹", "궁합", "꿀조합", "찍어먹", "곁들", "곁들여", "비벼먹"
    )

    # 특색 있는 식재료/토핑 (단독으로도 이색 조합 호기심 유발)
    SPECIAL_COMBINATION_INGREDIENTS = (
        "블루치즈", "고르곤졸라", "트러플", "마라", "바질", "아보카도", "잠봉", "명란",
        "우니", "멜젓", "흑돼지", "연탄구이", "솥뚜껑"
    )

    # 비주얼/구성 키워드
    VISUAL_KEYWORDS = (
        "비주얼", "사진", "색감", "푸짐", "플레이팅", "두툼", "노릇노릇", "먹음직",
        "양", "가득", "듬뿍", "예쁘", "비쥬얼", "비주얼이"
    )

    # 여행/장소 신호 (강한 키워드 + 숙소 + 공간)
    PLACE_STRONG_KEYWORDS = (
        "여행", "관광", "명소", "코스", "산책로", "둘레길", "일출", "일몰", "풍경", "야경",
        "바다", "해변", "제주", "제주도", "강릉", "부산", "경주", "출사", "투어", "드라이브",
        "전망대", "나들이", "산책길", "골목", "포토존", "숲길"
    )
    PLACE_STAY_KEYWORDS = (
        "숙소", "호텔", "펜션", "게스트하우스", "리조트", "글램핑", "캠핑", "1박", "2박"
    )

    # 개인/일상 신호 (강한 사건 vs 약한 일상어 분리)
    PERSONAL_EVENT_KEYWORDS = (
        "합격", "취업", "퇴사", "생일", "기념일", "출산", "도전", "결혼", "수술", "입원",
        "완치", "회복", "쾌유", "첫걸음", "돌잔치", "이사", "새출발", "자격증", "졸업"
    )
    PERSONAL_FEELING_KEYWORDS = (
        "일기", "소회", "생각이 많", "마음이", "힘들었", "뿌듯", "울컥", "눈물", "감사한 하루",
        "댕댕이", "반려견", "반려묘", "멍멍이"
    )
    PERSONAL_WEAK_KEYWORDS = (
        "주말", "하루", "친구", "아이", "가족", "일상", "퇴근", "출근", "휴일", "소소한", "산책"
    )

    # 제품 신호
    PRODUCT_SIGNALS = (
        *FoodCommentFocus.ORAL_CARE_PRODUCTS,
        "사용기", "언박싱", "내돈내산", "구매", "스펙", "배터리", "충전", "가성비", "내구성",
        "디자인", "장단점", "패키징", "키보드", "마우스", "모니터", "이어폰", "청소기", "가전",
        "화장품", "크림", "세럼", "거치대", "케이스", "워치", "태블릿", "노트북"
    )

    # 서비스 신호
    SERVICE_SIGNALS = (
        "시술", "미용실", "헤어", "네일", "관리", "클리닉", "피부과", "상담", "수리", "AS",
        "세차", "예약제", "원장님", "디자이너", "염색", "파마", "커트", "스케일링"
    )

    GENERIC_BANNED_ANCHORS = (
        "메뉴", "제품", "서비스", "음식", "디테일", "가게", "매장", "관리"
    )

    @classmethod
    def rank_anchors(
        cls,
        candidates: List[str],
        title: str,
        excerpt: str,
        domain: str = "GENERAL",
    ) -> List[str]:
        """
        후보 앵커 목록을 제목 등장 여부, 구체성, 출현 빈도를 점수화하여 정렬한다.
        일반어("메뉴", "제품", "서비스" 등)와 메타데이터("위치", "주차")를 강하게 배제.
        """
        if not candidates:
            return []

        title_norm = (title or "").lower()
        excerpt_norm = (excerpt or "").lower()
        combined = f"{title_norm}\n{excerpt_norm}"

        scored: List[Tuple[float, str]] = []
        for cand in candidates:
            if not cand or not isinstance(cand, str):
                continue
            cand_s = cand.strip()
            if not cand_s:
                continue

            score = 0.0

            # 1. 일반어 및 메타데이터 감점
            if cand_s in cls.GENERIC_BANNED_ANCHORS:
                score -= 50.0

            if domain == "FOOD":
                if cand_s in FoodCommentFocus.SECONDARY_KEYWORDS:
                    score -= 100.0  # 음식 도메인에서는 위치/주차 등을 앵커로 절대 선택 불가

            # 2. 제목 일치 가중치
            if cand_s.lower() in title_norm:
                if cand_s in ("미용실", "식당", "카페", "여행", "호텔", "숙소", "맛집"):
                    score += 4.0
                else:
                    score += 10.0

            # 3. 본문 출현 가중치 (본문이 있는데 본문에 없으면 감점)
            if excerpt_norm and len(excerpt_norm) > 20:
                if cand_s.lower() not in excerpt_norm:
                    score -= 8.0
                else:
                    e_cnt = excerpt_norm.count(cand_s.lower())
                    score += min(e_cnt * 3.0, 9.0)
            else:
                c_cnt = combined.count(cand_s.lower())
                score += min(c_cnt * 2.0, 6.0)

            # 4. 고유명사/시술명/메뉴명 구체성 (글자 수 가산)
            if len(cand_s) >= 3:
                score += 1.5
            if len(cand_s) >= 4:
                score += 1.5

            # 5. 복합 음식명 우선 Ranking (들깨수제비 > 수제비, 과일산도 > 산도, 김피탕 > 피탕, 잠봉뵈르 > 샌드위치/잠봉)
            if domain == "FOOD":
                is_subword = any(
                    other != cand_s and cand_s in other and (other.lower() in combined)
                    for other in candidates
                )
                is_composite = any(
                    other != cand_s and other in cand_s and (other.lower() in combined)
                    for other in candidates
                )
                if is_composite:
                    score += 6.0
                if is_subword:
                    score -= 4.0

            scored.append((score, cand_s))

        # 점수 내림차순, 동일 점수 시 긴 단어 우선
        scored.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
        return [cand for _, cand in scored]

    @classmethod
    def _is_food_candidate(cls, candidate: str) -> bool:
        roots = tuple(FoodCommentFocus.RESTAURANT_DISHES + FoodCommentFocus.CAFE_DISHES) + tuple(
            ingredient for ingredient in cls.SPECIAL_COMBINATION_INGREDIENTS if ingredient != "솥뚜껑"
        ) + (
            "만두", "김밥", "순대", "꼬들살", "연어", "닭", "치즈", "유자", "바질",
            "도삭면", "토스트", "빵", "탕", "국", "면", "밥", "떡",
        )
        return candidate not in cls.GENERIC_BANNED_ANCHORS + ("한국", "전국", "중국", "천국") and candidate.endswith(roots)

    @classmethod
    def extract_open_vocabulary_food_candidates(cls, title: str, excerpt: str = "") -> List[str]:
        """
        사전에 등록되지 않은 고유 메뉴/음식명을 제목 및 본문에서 추출한다.
        - 대괄호/소괄호/해시태그 제거
        - '맛집', '전문점', '먹거리', '먹은' 등 핵심 패턴 주변 명사 추출
        - 한국어 조사 정규화 및 불용어/지역명 접미사 배제
        """
        if not title:
            return []

        clean_t = re.sub(r"\[.*?\]|\(.*?\)|<.*?>|#\S+", " ", title)
        stop_words = {
            "후기", "솔직후기", "내돈내산", "추천", "방문기", "방문후기", "리뷰", "일상", "투어", "탐방",
            "메뉴", "메뉴판", "가격", "위치", "주차", "웨이팅", "영업시간", "오픈런", "식당", "맛집", "카페",
            "음식점", "대박", "진짜", "정말", "최고", "존맛", "먹방", "비주얼", "솔직", "내돈", "인테리어",
            "분위기", "오늘", "어제", "주말", "평일", "점심", "저녁", "시간", "사진", "포스팅"
        }
        particle_re = re.compile(r"(?:와|과|이랑|랑|을|를|이|가|은|는|도|에|의)$")
        candidates: List[str] = []

        def _clean_token(tok: str) -> Optional[str]:
            tok = tok.strip()
            if len(tok) >= 3:
                tok = particle_re.sub("", tok)
            if len(tok) < 2 or len(tok) > 10:
                return None
            if tok in stop_words or tok in cls.GENERIC_BANNED_ANCHORS or tok in FoodCommentFocus.SECONDARY_KEYWORDS:
                return None
            if tok.endswith(("역", "동", "구", "점", "길", "로", "거리")):
                return None
            if not cls._is_food_candidate(tok):
                return None
            return tok

        # 1. '맛집', '전문점' 등 앞의 수식 명사 패턴
        target_patterns = [
            r"([가-힣]{2,10})\s*(?:맛집|전문점|먹거리|집)",
            r"([가-힣]{2,10})\s*(?:먹고\s*온|먹은|먹으러|먹방)",
            r"([가-힣]{2,10})\s*(?:후기|리뷰)",
        ]
        for pat in target_patterns:
            for m in re.finditer(pat, clean_t):
                w = _clean_token(m.group(1))
                if w and w not in candidates:
                    candidates.append(w)

        # 2. 제목 내 2~10글자 한글 토큰
        for tok in re.findall(r"[가-힣]{2,10}", clean_t):
            w = _clean_token(tok)
            if w and w not in candidates:
                candidates.append(w)

        return candidates

    @classmethod
    def score_domains(
        cls,
        title: str,
        excerpt: str,
        food_focus_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """각 도메인별 증거 점수(Evidence Score)를 계산한다."""
        combined = f"{title}\n{excerpt}"
        scores: Dict[str, float] = {
            "FOOD": 0.0,
            "PLACE": 0.0,
            "PRODUCT": 0.0,
            "SERVICE": 0.0,
            "PERSONAL": 0.0,
        }

        # 1. FOOD 점수
        if food_focus_info:
            focus = food_focus_info.get("focus", "GENERAL")
            if focus in ("FOOD_RESTAURANT", "CAFE_DESSERT", "FOOD_PRODUCT"):
                scores["FOOD"] += 4.0
            if food_focus_info.get("has_food_details", False):
                scores["FOOD"] += 2.5
            food_anchors = food_focus_info.get("food_anchors", [])
            scores["FOOD"] += min(len(food_anchors) * 2.0, 4.0)

        taste_count = sum(1 for kw in cls.TASTE_TEXTURE_KEYWORDS if kw in combined)
        if taste_count > 0:
            scores["FOOD"] += min(2.0 + (taste_count - 1) * 0.5, 4.0)

        dining_terms = ("식당", "맛집", "카페", "베이커리", "메뉴", "주문", "디저트", "음식", "먹었", "맛있")
        if any(term in combined for term in dining_terms):
            scores["FOOD"] += 2.0

        # 2. PLACE 점수 (여행, 관광, 명소, 숙소)
        place_strong_hits = sum(1 for kw in cls.PLACE_STRONG_KEYWORDS if kw in combined)
        scores["PLACE"] += min(place_strong_hits * 3.0, 9.0)

        stay_hits = sum(1 for kw in cls.PLACE_STAY_KEYWORDS if kw in combined)
        scores["PLACE"] += min(stay_hits * 2.0, 4.0)

        # 3. PRODUCT 점수
        non_food_prod = [sig for sig in FoodCommentFocus.NON_FOOD_PRODUCT_SIGNALS if sig in combined]
        scores["PRODUCT"] += min(len(non_food_prod) * 3.0, 6.0)
        prod_hits = sum(1 for kw in cls.PRODUCT_SIGNALS if kw in combined)
        scores["PRODUCT"] += min(prod_hits * 2.0, 6.0)
        # Flavor words in oral-care reviews describe a product, not a meal.
        if any(kw in title for kw in FoodCommentFocus.ORAL_CARE_PRODUCTS):
            scores["PRODUCT"] += 3.0

        # 4. SERVICE 점수
        non_dining_svc = [sig for sig in FoodCommentFocus.NON_DINING_SERVICES if sig in combined]
        scores["SERVICE"] += min(len(non_dining_svc) * 3.0, 6.0)
        svc_hits = sum(1 for kw in cls.SERVICE_SIGNALS if kw in combined)
        scores["SERVICE"] += min(svc_hits * 2.0, 6.0)

        # 5. PERSONAL 점수
        event_hits = sum(1 for kw in cls.PERSONAL_EVENT_KEYWORDS if kw in combined)
        scores["PERSONAL"] += min(event_hits * 3.5, 7.0)

        feeling_hits = sum(1 for kw in cls.PERSONAL_FEELING_KEYWORDS if kw in combined)
        scores["PERSONAL"] += min(feeling_hits * 2.0, 4.0)

        # 약한 일상어는 낮은 가중치만 부여하여 강한 테마(여행/음식)를 덮지 못하게 함
        weak_hits = sum(1 for kw in cls.PERSONAL_WEAK_KEYWORDS if kw in combined)
        scores["PERSONAL"] += min(weak_hits * 0.5, 1.5)

        return scores

    @classmethod
    def discover_domain_and_terms(
        cls,
        title: str,
        excerpt: str,
    ) -> Tuple[str, List[str], List[str]]:
        """
        1차 Domain / Anchor Discovery:
        Context Selector가 본문을 선별하기 전에 가중치(preferred_anchors, preferred_terms)를 제공.
        """
        title_s = (title or "").strip()
        excerpt_s = (excerpt or "").strip()
        combined = f"{title_s}\n{excerpt_s}"

        food_info = FoodCommentFocus.analyze(title_s, excerpt_s)
        scores = cls.score_domains(title_s, excerpt_s, food_info)

        best_domain = "GENERAL"
        max_score = 0.0
        for dom, sc in scores.items():
            if sc > max_score:
                max_score = sc
                best_domain = dom

        if max_score < 1.0:
            best_domain = "GENERAL"

        preferred_anchors: List[str] = []
        preferred_terms: List[str] = []

        if best_domain == "FOOD":
            preferred_anchors.extend(food_info.get("food_anchors", []))
            preferred_anchors.extend(food_info.get("secondary_anchors", []))
            matched_spec = [ing for ing in cls.SPECIAL_COMBINATION_INGREDIENTS if ing in combined]
            preferred_anchors.extend(matched_spec)
            matched_taste = [kw for kw in cls.TASTE_TEXTURE_KEYWORDS if kw in combined]
            preferred_terms.extend(matched_taste[:5])
        elif best_domain == "PLACE":
            matched_places = [kw for kw in cls.PLACE_STRONG_KEYWORDS if kw in combined]
            matched_stays = [kw for kw in cls.PLACE_STAY_KEYWORDS if kw in combined]
            preferred_anchors.extend(matched_places[:3])
            preferred_terms.extend(matched_stays[:2])
        elif best_domain == "PRODUCT":
            matched_prods = [kw for kw in cls.PRODUCT_SIGNALS if kw in combined]
            preferred_anchors.extend(matched_prods[:3])
        elif best_domain == "SERVICE":
            matched_svcs = [kw for kw in cls.SERVICE_SIGNALS if kw in combined]
            preferred_anchors.extend(matched_svcs[:3])
        elif best_domain == "PERSONAL":
            matched_events = [kw for kw in cls.PERSONAL_EVENT_KEYWORDS if kw in combined]
            preferred_anchors.extend(matched_events[:3])

        return best_domain, preferred_anchors, preferred_terms

    @classmethod
    def plan(
        cls,
        title: str,
        excerpt: str,
        food_focus_info: Optional[Dict[str, Any]] = None,
        recent_reaction_modes: Optional[List[str]] = None,
    ) -> ReactionPlan:
        title_s = (title or "").strip()
        excerpt_s = (excerpt or "").strip()
        combined = f"{title_s}\n{excerpt_s}"

        if food_focus_info is None:
            food_focus_info = FoodCommentFocus.analyze(title_s, excerpt_s)

        scores = cls.score_domains(title_s, excerpt_s, food_focus_info)

        best_domain = "GENERAL"
        max_score = 0.0
        for dom, sc in scores.items():
            if sc > max_score:
                max_score = sc
                best_domain = dom

        if max_score < 1.0:
            best_domain = "GENERAL"

        # 최근 연속 future_interest 횟수 확인 (반복 감점용)
        recent_modes = recent_reaction_modes or []
        future_streak = 0
        for m in reversed(recent_modes):
            if m == "future_interest":
                future_streak += 1
            else:
                break
        downweight_future = (future_streak >= 2)

        # -------------------------------------------------------------
        # 1. FOOD 도메인
        # -------------------------------------------------------------
        if best_domain == "FOOD":
            food_anchors = food_focus_info.get("food_anchors", [])

            # 특색 있는 식재료 우선 수집 (블루치즈, 트러플, 마라 등)
            matched_special = [ing for ing in cls.SPECIAL_COMBINATION_INGREDIENTS if ing in combined]

            # 음식 후보군 종합 수집
            candidate_foods: List[str] = list(matched_special)
            for d in food_anchors:
                if d not in candidate_foods:
                    candidate_foods.append(d)
            for d in FoodCommentFocus.RESTAURANT_DISHES + FoodCommentFocus.CAFE_DISHES:
                if d in combined and d not in candidate_foods:
                    candidate_foods.append(d)

            # 제목 기반 open-vocabulary 후보 추출 보강
            open_vocab_foods = cls.extract_open_vocabulary_food_candidates(title_s, excerpt_s)
            for ov in open_vocab_foods:
                if ov not in candidate_foods:
                    candidate_foods.append(ov)

            candidate_foods = [f for f in candidate_foods if cls._is_food_candidate(f)]
            ranked_foods = cls.rank_anchors(candidate_foods, title_s, excerpt_s, domain="FOOD")
            # Generic anchor('메뉴', '음식', '가게') 최후 fallback으로 격하 ('anchor=메뉴' 방지)
            valid_foods = [f for f in ranked_foods if f not in cls.GENERIC_BANNED_ANCHORS]
            pri = valid_foods[0] if valid_foods else (ranked_foods[0] if ranked_foods else "메뉴")

            # 2nd food anchor: 반드시 진짜 음식/재료여야 함 (절대로 '위치', '주차' 등 메타데이터가 아님!)
            # P2: primary와 포함관계(부분문자열)인 경우도 제거 (예: 돈카츠/카츠, 크림파스타/파스타)
            sec = ""
            for rf in ranked_foods[1:]:
                if rf == pri:
                    continue
                if rf in cls.GENERIC_BANNED_ANCHORS or rf in FoodCommentFocus.SECONDARY_KEYWORDS:
                    continue
                # 포함관계 중복 제거: 한쪽이 다른 쪽의 부분문자열인 경우 스킵
                if rf in pri or pri in rf:
                    continue
                sec = rf
                break

            # Salience 점수 계산
            combo_salience = 0.0
            visual_salience = 0.0
            taste_salience = 0.0

            # 1) 조합 salience
            for ing in cls.SPECIAL_COMBINATION_INGREDIENTS:
                if ing in combined:
                    combo_salience += 4.0
            for phrase in cls.EXPLICIT_COMBINATION_PHRASES:
                if phrase in combined:
                    combo_salience += 4.0
            if any(k in combined for k in ("단짠", "궁합", "어우러")):
                combo_salience += 3.0

            # 2) 비주얼 salience (플레이팅, 큼직한 토핑, 색감 등)
            visual_strong = ("플레이팅", "비주얼", "비쥬얼", "예쁘", "색감", "압도적", "시선을 사로잡", "사진이")
            for vs in visual_strong:
                if vs in combined:
                    visual_salience += 4.0
            visual_mild = ("푸짐", "두툼", "큼직", "듬뿍", "가득", "비주얼이", "양도", "양이")
            for vm in visual_mild:
                if vm in combined:
                    visual_salience += 2.5

            # 3) 맛/식감 salience
            taste_hits = sum(1 for kw in cls.TASTE_TEXTURE_KEYWORDS if kw in combined)
            if taste_hits > 0:
                taste_salience += 3.0 + min((taste_hits - 1) * 1.5, 9.0)

            # 모드 결정
            has_special_ingredient = any(ing in combined for ing in ("블루치즈", "고르곤졸라", "트러플", "마라", "바질"))
            has_explicit_combo = any(k in combined for k in cls.EXPLICIT_COMBINATION_PHRASES)

            if (combo_salience >= 4.0 and (has_special_ingredient or has_explicit_combo)) and (combo_salience >= visual_salience and combo_salience >= taste_salience):
                mode = "combination_curiosity"
                if matched_special:
                    pri = matched_special[0]
                    sec = ""
                    for rf in ranked_foods:
                        if rf == pri:
                            continue
                        if rf in cls.GENERIC_BANNED_ANCHORS or rf in FoodCommentFocus.SECONDARY_KEYWORDS:
                            continue
                        # 포함관계 중복 제거
                        if rf in pri or pri in rf:
                            continue
                        sec = rf
                        break
                sec_desc = f"와 {sec}" if sec and sec != pri else ""
                instruction = (
                    f"{pri}{sec_desc}에 짧고 솔직하게 반응해. "
                    "궁금함·의외성·연상 중 맞는 느낌만 골라. 조합 설명은 필수가 아냐."
                )
                evidence = f"조합/특수재료 근거({pri}{sec_desc})"
            elif visual_salience >= 4.0 and visual_salience > taste_salience:
                mode = "visual_reaction"
                instruction = (
                    f"{pri}의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마."
                )
                evidence = f"시각/구성 디테일({pri})"
            elif taste_salience >= 2.5:
                mode = "taste_reaction"
                instruction = (
                    f"본문의 {pri} 맛이나 식감 중 끌리는 부분에 네 느낌을 짧게 붙여줘. "
                    "맛 설명을 다시 읊을 필요는 없어."
                )
                evidence = f"본문 맛/식감 언급({pri})"
            elif visual_salience >= 2.5:
                mode = "visual_reaction"
                instruction = (
                    f"{pri}의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마."
                )
                evidence = f"시각/구성 디테일({pri})"
            else:
                if downweight_future:
                    if visual_salience > 0:
                        mode = "visual_reaction"
                        instruction = f"{pri}의 비주얼이나 구성에 가볍게 반응해. 직접 먹어본 척하지 마."
                        evidence = f"시각/구성 디테일({pri})"
                    else:
                        mode = "detail_observation"
                        instruction = (
                            f"{pri}에서 눈에 들어온 부분에 짧고 편하게 반응해. "
                            "이번에는 방문 계획 대신 지금 든 느낌으로 끝내줘."
                        )
                        evidence = f"메뉴 디테일 관찰({pri})"
                else:
                    mode = "future_interest"
                    instruction = (
                        f"{pri}에서 떠오른 느낌을 편하게 표현해. 먹고 싶다는 말이나 방문 계획은 필수가 아냐."
                    )
                    evidence = f"메뉴 언급({pri})"

            return ReactionPlan(
                domain="FOOD",
                primary_anchor=pri,
                secondary_anchor=sec,
                reaction_mode=mode,
                evidence=evidence,
                reaction_instruction=instruction,
            )

        # -------------------------------------------------------------
        # 2. PLACE 도메인 (여행/관광/명소/숙소)
        # -------------------------------------------------------------
        if best_domain == "PLACE":
            matched_places = [kw for kw in cls.PLACE_STRONG_KEYWORDS if kw in combined]
            matched_stays = [kw for kw in cls.PLACE_STAY_KEYWORDS if kw in combined]
            all_places = matched_places + matched_stays
            ranked_places = cls.rank_anchors(all_places, title_s, excerpt_s, domain="PLACE")
            pri = ranked_places[0] if ranked_places else "여행지"
            # P2: PLACE secondary에서도 포함관계 중복 제거
            sec = ""
            for rp in ranked_places[1:]:
                if rp != pri and not (rp in pri or pri in rp):
                    sec = rp
                    break

            mode = "place_observation"
            instruction = (
                f"{pri} 관련 풍경이나 코스, 공간 중 눈에 들어온 부분에 솔직한 느낌을 붙여줘. 직접 가본 척하지 마."
            )
            evidence = f"장소/여행 신호({pri})"
            return ReactionPlan(
                domain="PLACE",
                primary_anchor=pri,
                secondary_anchor=sec,
                reaction_mode=mode,
                evidence=evidence,
                reaction_instruction=instruction,
            )

        # -------------------------------------------------------------
        # 3. PRODUCT 도메인
        # -------------------------------------------------------------
        if best_domain == "PRODUCT":
            matched_products = [sig for sig in FoodCommentFocus.NON_FOOD_PRODUCT_SIGNALS if sig in combined]
            if not matched_products:
                matched_products = [kw for kw in cls.PRODUCT_SIGNALS if kw in combined]
            ranked_prods = cls.rank_anchors(matched_products, title_s, excerpt_s, domain="PRODUCT")
            pri = ranked_prods[0] if ranked_prods else "제품"
            # P2: PRODUCT secondary에서도 포함관계 중복 제거
            sec = ""
            for rpr in ranked_prods[1:]:
                if rpr != pri and not (rpr in pri or pri in rpr):
                    sec = rpr
                    break
            instruction = (
                f"제품의 {pri} 기능이나 디자인 중 관심 가는 부분에 짧고 편하게 반응해. 직접 써본 척하지 마."
            )
            return ReactionPlan(
                domain="PRODUCT",
                primary_anchor=pri,
                secondary_anchor=sec,
                reaction_mode="feature_reaction",
                evidence=f"제품 신호({pri})",
                reaction_instruction=instruction,
            )

        # -------------------------------------------------------------
        # 4. SERVICE 도메인
        # -------------------------------------------------------------
        if best_domain == "SERVICE":
            matched_services = [sig for sig in FoodCommentFocus.NON_DINING_SERVICES if sig in combined]
            if not matched_services:
                matched_services = [kw for kw in cls.SERVICE_SIGNALS if kw in combined]
            # 구체적인 시술명이나 서비스 항목 추가 후보군
            extra_svc_candidates = [
                "레이어드컷", "피부관리", "수분 진정", "진정 케어", "그라데이션", "네일아트",
                "스케일링", "디테일링", "헤어클리닉", "손질", "케어", "마스크팩", "앰플"
            ]
            for esc in extra_svc_candidates:
                if esc in combined and esc not in matched_services:
                    matched_services.append(esc)

            ranked_svcs = cls.rank_anchors(matched_services, title_s, excerpt_s, domain="SERVICE")
            pri = ranked_svcs[0] if ranked_svcs else "서비스"
            # P2: SERVICE secondary에서도 포함관계 중복 제거
            sec = ""
            for rsv in ranked_svcs[1:]:
                if rsv != pri and not (rsv in pri or pri in rsv):
                    sec = rsv
                    break
            instruction = (
                f"{pri} 서비스의 구성이나 진행 방식 중 눈에 들어온 부분에 솔직한 느낌을 붙여줘."
            )
            return ReactionPlan(
                domain="SERVICE",
                primary_anchor=pri,
                secondary_anchor=sec,
                reaction_mode="service_reaction",
                evidence=f"서비스 신호({pri})",
                reaction_instruction=instruction,
            )

        # -------------------------------------------------------------
        # 5. PERSONAL 도메인
        # -------------------------------------------------------------
        if best_domain == "PERSONAL":
            matched_events = [kw for kw in cls.PERSONAL_EVENT_KEYWORDS if kw in combined]
            matched_feelings = [kw for kw in cls.PERSONAL_FEELING_KEYWORDS if kw in combined]
            matched_weak = [kw for kw in cls.PERSONAL_WEAK_KEYWORDS if kw in combined]
            all_pers = matched_events + matched_feelings + matched_weak
            ranked_pers = cls.rank_anchors(all_pers, title_s, excerpt_s, domain="PERSONAL")
            pri = ranked_pers[0] if ranked_pers else "일상"
            sec = ranked_pers[1] if len(ranked_pers) > 1 and ranked_pers[1] != pri else ""

            instruction = (
                "작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마."
            )
            return ReactionPlan(
                domain="PERSONAL",
                primary_anchor=pri,
                secondary_anchor=sec,
                reaction_mode="writer_feeling",
                evidence=f"일상/감정 신호({pri})",
                reaction_instruction=instruction,
            )

        # -------------------------------------------------------------
        # 6. GENERAL 도메인
        # -------------------------------------------------------------
        pri = title_s.split()[0] if title_s else "본문 디테일"
        instruction = "본문에서 가장 눈에 띄는 구체적인 사실이나 디테일 하나에 솔직하고 가볍게 반응해."
        return ReactionPlan(
            domain="GENERAL",
            primary_anchor=pri,
            secondary_anchor="",
            reaction_mode="detail_observation",
            evidence="일반 본문 디테일",
            reaction_instruction=instruction,
        )
