"""services/reaction_planner.py
P0: 글 분류 및 반응 계획 수립 서비스 (ReactionContextPlanner & ReactionPlan).

분류 -> 반응계획 -> 짧은 프롬프트 -> 검증 4단 구조의 1~2단계를 담당.
Gemini에게 5개 카테고리 전체 지침과 장황한 규칙을 넘기지 않고,
Python에서 글 분류(Domain)와 반응 방식(reaction_mode)을 결정하여
단 하나의 맞춤형 reaction_instruction을 생성한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
    글 원문/선별 발췌를 분석하여 도메인과 reaction_mode를 확정하는 플래너.
    """

    # 맛/식감 키워드
    TASTE_TEXTURE_KEYWORDS = (
        "바삭", "촉촉", "쫀득", "쫄깃", "고소", "달달", "매콤", "얼큰", "단짠",
        "담백", "느끼", "알싸", "육즙", "풍미", "진하", "시원하", "칼칼", "부드러",
        "눅눅", "기름", "불향", "숯불향", "달콤", "새콤", "짭짤"
    )

    # 조합/특색 키워드
    COMBINATION_KEYWORDS = (
        "조합", "토핑", "치즈", "소스", "특유", "호불호", "이색", "블루치즈",
        "트러플", "마라", "바질", "시그니처", "독특", "궁합", "꿀조합", "어우러"
    )

    # 비주얼/구성 키워드
    VISUAL_KEYWORDS = (
        "비주얼", "사진", "색감", "푸짐", "플레이팅", "두툼", "노릇노릇", "먹음직",
        "양", "가득", "듬뿍", "예쁘", "비쥬얼", "비주얼이"
    )

    # 여행/장소 신호
    PLACE_KEYWORDS = (
        "여행", "관광", "숙소", "호텔", "펜션", "게스트하우스", "산책", "코스",
        "전망대", "해변", "바다", "야경", "풍경", "명소", "드라이브", "나들이",
        "캠핑", "출사", "골목", "포토존", "숲길", "둘레길", "일몰", "일출"
    )

    # 일상/개인 신호
    PERSONAL_KEYWORDS = (
        "일상", "일기", "생각", "가족", "아이", "육아", "친구", "병원", "출근",
        "퇴근", "주말", "휴일", "생일", "기념일", "합격", "도전", "건강", "감기",
        "몸살", "회복", "쾌유", "소소한", "하루", "댕댕이", "반려견", "고양이"
    )

    @classmethod
    def plan(
        cls,
        title: str,
        excerpt: str,
        food_focus_info: Optional[Dict[str, Any]] = None,
    ) -> ReactionPlan:
        title_s = (title or "").strip()
        excerpt_s = (excerpt or "").strip()
        combined = f"{title_s}\n{excerpt_s}"

        if food_focus_info is None:
            food_focus_info = FoodCommentFocus.analyze(title_s, excerpt_s)

        focus = food_focus_info.get("focus", "GENERAL")
        food_anchors = food_focus_info.get("food_anchors", [])
        secondary_anchors = food_focus_info.get("secondary_anchors", [])
        has_food_details = food_focus_info.get("has_food_details", False)

        # -------------------------------------------------------------
        # 1. FOOD 도메인 판정
        # -------------------------------------------------------------
        if focus in ("FOOD_RESTAURANT", "CAFE_DESSERT", "FOOD_PRODUCT") or has_food_details or (food_anchors and focus != "GENERAL"):
            pri = food_anchors[0] if food_anchors else "메뉴"
            sec = secondary_anchors[0] if secondary_anchors else (food_anchors[1] if len(food_anchors) > 1 else "")

            # 특색 있는 식재료/토핑/소스 추출 (블루치즈, 고르곤졸라, 트러플, 마라, 바질, 아보카도 등)
            SPECIAL_INGREDIENTS = (
                "블루치즈", "고르곤졸라", "트러플", "마라", "바질", "아보카도", "잠봉", "명란",
                "우니", "멜젓", "치즈", "흑돼지", "전복", "연어", "새우", "연탄구이", "솥뚜껑"
            )
            matched_special = [ing for ing in SPECIAL_INGREDIENTS if ing in combined]
            if matched_special:
                special_ing = matched_special[0]
                if special_ing not in food_anchors:
                    sec = pri if pri != "메뉴" else sec
                    pri = special_ing
                elif food_anchors and food_anchors[0] != special_ing:
                    sec = pri
                    pri = special_ing

            # Mode 우선순위 판별
            # 1) 본문에 맛/식감 근거 있음 -> taste_reaction
            has_taste = any(k in excerpt_s for k in cls.TASTE_TEXTURE_KEYWORDS)
            # 2) 독특한 재료/조합 있음 -> combination_curiosity
            has_combo = (len(food_anchors) >= 2 or any(k in combined for k in cls.COMBINATION_KEYWORDS) or bool(matched_special))
            # 3) 비주얼/구성만 명확함 -> visual_reaction
            has_visual = any(k in excerpt_s for k in cls.VISUAL_KEYWORDS)

            if has_combo and ("블루치즈" in combined or "호불호" in combined or "조합" in combined or len(food_anchors) >= 2 or bool(matched_special)):
                mode = "combination_curiosity"
                sec_desc = f"와 {sec}" if sec and sec != pri else ""
                instruction = (
                    f"{pri}{sec_desc} 재료와 조합에 관찰자 입장에서 반응해. "
                    "먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해."
                )
                evidence = f"조합/재료 키워드 및 메뉴({pri}{sec_desc})"
            elif has_taste:
                mode = "taste_reaction"
                instruction = (
                    f"본문에 언급된 {pri}의 맛이나 식감 디테일에 관찰자 입장에서 반응해. "
                    "본문에 없는 맛이나 식감은 지어내지 마."
                )
                evidence = f"본문 맛/식감 언급({pri})"
            elif has_visual:
                mode = "visual_reaction"
                instruction = (
                    f"{pri}의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마."
                )
                evidence = f"시각/구성 디테일({pri})"
            else:
                mode = "future_interest"
                instruction = (
                    f"{pri} 메뉴에 관심을 보이거나 나중에 맛보고 싶다는 느낌으로 가볍게 반응해."
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
        # 2. PRODUCT 도메인 판정
        # -------------------------------------------------------------
        matched_products = [sig for sig in FoodCommentFocus.NON_FOOD_PRODUCT_SIGNALS if sig in combined]
        if matched_products:
            pri = matched_products[0]
            sec = matched_products[1] if len(matched_products) > 1 else ""
            instruction = (
                f"제품의 {pri} 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마."
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
        # 3. SERVICE 도메인 판정
        # -------------------------------------------------------------
        matched_services = [sig for sig in FoodCommentFocus.NON_DINING_SERVICES if sig in combined]
        if matched_services:
            pri = matched_services[0]
            sec = matched_services[1] if len(matched_services) > 1 else ""
            instruction = (
                f"{pri} 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해."
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
        # 4. PERSONAL 도메인 판정 (일상/일기/가족/반려동물 등)
        # -------------------------------------------------------------
        matched_personal = [kw for kw in cls.PERSONAL_KEYWORDS if kw in combined]
        if matched_personal:
            pri = matched_personal[0]
            sec = matched_personal[1] if len(matched_personal) > 1 else ""
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
        # 5. PLACE / TRAVEL 도메인 판정 (여행/관광/명소/숙소 등)
        # -------------------------------------------------------------
        matched_places = [kw for kw in cls.PLACE_KEYWORDS if kw in combined]
        if matched_places:
            pri = matched_places[0]
            sec = matched_places[1] if len(matched_places) > 1 else ""
            instruction = (
                f"{pri} 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마."
            )
            return ReactionPlan(
                domain="PLACE",
                primary_anchor=pri,
                secondary_anchor=sec,
                reaction_mode="place_observation",
                evidence=f"장소/여행 신호({pri})",
                reaction_instruction=instruction,
            )

        # -------------------------------------------------------------
        # 6. GENERAL 도메인 판정
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
