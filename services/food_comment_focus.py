import re
from typing import Any, Dict, List, Tuple


class FoodCommentFocus:
    """Classifies blog posts for food/dining focus and extracts food anchors."""

    # Strong restaurant signals (menus, dining keywords)
    RESTAURANT_KEYWORDS = [
        "맛집", "식당", "음식점", "밥집", "고깃집", "횟집", "한식", "중식", "일식", "양식",
        "메뉴", "주문", "식사", "먹었", "맛있", "존맛", "꿀맛", "먹방", "외식", "회식"
    ]

    RESTAURANT_DISHES = [
        "돈까스", "카츠", "치즈카츠", "국밥", "순대국", "돼지국밥", "덮밥", "파스타", "리조또",
        "라멘", "우동", "초밥", "스시", "사시미", "회", "삼겹살", "목살", "갈비", "우대갈비",
        "곱창", "막창", "대창", "쭈꾸미", "닭갈비", "닭구이", "숯불닭갈비", "치킨", "피자",
        "버거", "수제버거", "국수", "칼국수", "비빔국수", "냉면", "평양냉면", "찌개", "된장찌개",
        "김치찌개", "전골", "곱창전골", "샤브샤브", "샤브", "해물탕", "해물뚝배기", "해물",
        "전복", "장어", "육회", "비빔밥", "숯불구이", "숯불", "구이", "볶음밥", "안동소주",
        "스테이크", "바베큐", "수육", "보쌈", "족발", "찜닭", "마라탕", "마라샹궈", "쌀국수",
        "분짜", "팟타이", "카레", "돈부리"
    ]

    # Cafe & Dessert signals
    CAFE_KEYWORDS = [
        "카페", "디저트", "베이커리", "베이커리카페", "빵집", "과자점", "찻집"
    ]

    CAFE_DISHES = [
        "소금빵", "식빵", "초코식빵", "크루아상", "케이크", "치즈케이크", "딸기케이크",
        "쿠키", "빙수", "망고빙수", "라떼", "딸기라떼", "바닐라라떼", "커피", "아메리카노",
        "에이드", "말차", "말차라떼", "녹차", "젤라또", "아이스크림", "찹쌀떡", "요거트찹쌀떡",
        "와플", "크로플", "도넛", "타르트", "마카롱", "스콘", "휘낭시에", "마들렌",
        "베이글", "샌드위치", "푸딩", "밀크티", "요거트", "그릭요거트"
    ]

    # Convenience store & retail food product signals
    PRODUCT_BRAND_SIGNALS = [
        "gs25", "cu", "세븐일레븐", "이마트24", "미니스톱", "편의점", "신상디저트", "신제품",
        "편의점신상", "신상", "한정판", "한정선"
    ]

    # Secondary / Non-food anchors that should NOT take precedence over food details
    SECONDARY_KEYWORDS = [
        "주차", "주차장", "주차자리", "발렛", "위치", "접근성", "역세권", "매장", "내부",
        "인테리어", "분위기", "창가", "창가자리", "오션뷰", "마운틴뷰", "뷰", "테라스",
        "단체석", "룸", "예약", "웨이팅", "영업시간", "브레이크타임", "가성비", "친절"
    ]

    @classmethod
    def analyze(cls, title: str, excerpt: str) -> Dict[str, Any]:
        """Analyzes title and excerpt to determine content focus and extracted anchors."""
        title_norm = (title or "").lower()
        excerpt_norm = (excerpt or "").lower()
        combined_text = f"{title_norm} {excerpt_norm}"

        food_anchors: List[str] = []
        secondary_anchors: List[str] = []

        # 1. Search for secondary place/convenience anchors first
        for kw in cls.SECONDARY_KEYWORDS:
            if kw in combined_text and kw not in secondary_anchors:
                secondary_anchors.append(kw)

        # 2. Check for Convenience Store / Packaged Food Products
        has_product_brand = any(brand in combined_text for brand in cls.PRODUCT_BRAND_SIGNALS)
        matched_cafe_dishes = [d for d in cls.CAFE_DISHES if d in combined_text]
        matched_restaurant_dishes = [d for d in cls.RESTAURANT_DISHES if d in combined_text]

        if has_product_brand and (matched_cafe_dishes or "디저트" in combined_text or "젤라또" in combined_text or "찹쌀떡" in combined_text):
            # Prioritize extracted dessert/product terms
            for d in matched_cafe_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            if "젤라또" in combined_text and "젤라또" not in food_anchors:
                food_anchors.append("젤라또")
            if "찹쌀떡" in combined_text and "찹쌀떡" not in food_anchors:
                food_anchors.append("찹쌀떡")
            return {
                "focus": "FOOD_PRODUCT",
                "food_anchors": food_anchors,
                "secondary_anchors": secondary_anchors,
                "has_food_details": len(food_anchors) > 0,
            }

        # 3. Check for Cafe / Dessert
        has_cafe_kw = any(kw in combined_text for kw in cls.CAFE_KEYWORDS)
        if has_cafe_kw or (matched_cafe_dishes and not matched_restaurant_dishes):
            for d in matched_cafe_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            return {
                "focus": "CAFE_DESSERT",
                "food_anchors": food_anchors,
                "secondary_anchors": secondary_anchors,
                "has_food_details": len(food_anchors) > 0,
            }

        # 4. Check for Restaurant / Dining
        has_restaurant_kw = any(kw in combined_text for kw in cls.RESTAURANT_KEYWORDS)
        if has_restaurant_kw or matched_restaurant_dishes:
            for d in matched_restaurant_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            # Also include any cafe dishes if mentioned in dining (e.g. side desserts)
            for d in matched_cafe_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            return {
                "focus": "FOOD_RESTAURANT",
                "food_anchors": food_anchors,
                "secondary_anchors": secondary_anchors,
                "has_food_details": len(food_anchors) > 0,
            }

        # 5. General non-food post
        return {
            "focus": "GENERAL",
            "food_anchors": [],
            "secondary_anchors": secondary_anchors,
            "has_food_details": False,
        }
