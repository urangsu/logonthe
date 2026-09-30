import re
from typing import Any, Dict, List, Tuple


class FoodCommentFocus:
    """Classifies blog posts for food/dining focus and extracts food anchors."""

    # Strong restaurant signals (menus, dining keywords)
    STRONG_RESTAURANT_KEYWORDS = [
        "맛집", "식당", "음식점", "밥집", "고깃집", "횟집", "한식집", "중식당", "일식집", "양식당",
        "존맛", "먹방", "외식", "회식"
    ]

    WEAK_RESTAURANT_KEYWORDS = [
        "메뉴", "주문", "식사", "먹었", "맛있", "꿀맛"
    ]

    RESTAURANT_KEYWORDS = STRONG_RESTAURANT_KEYWORDS + WEAK_RESTAURANT_KEYWORDS

    RESTAURANT_DISHES = [
        "돈카츠", "안심돈카츠", "등심돈카츠", "돈까스", "카츠", "치즈카츠", "치즈돈까스", "국밥", "순대국", "돼지국밥", "덮밥", "파스타", "리조또",
        "라멘", "우동", "초밥", "스시", "사시미", "광어회", "연어회", "방어회", "모둠회", "모듬회", "참치회", "활어회", "숙성회", "물회", "세꼬시",
        "삼겹살", "목살", "갈비", "우대갈비", "흑돼지",
        "곱창", "막창", "대창", "쭈꾸미", "닭갈비", "닭구이", "숯불닭갈비", "치킨", "피자", "화덕피자", "고르곤졸라", "고르곤졸라피자", "블루치즈",
        "버거", "수제버거", "국수", "칼국수", "비빔국수", "냉면", "평양냉면", "찌개", "된장찌개",
        "김치찌개", "전골", "곱창전골", "샤브샤브", "샤브", "해물탕", "해물뚝배기", "해물",
        "전복", "장어", "장어구이", "민물장어", "육회", "비빔밥", "숯불구이", "숯불", "구이", "볶음밥", "안동소주",
        "스테이크", "바베큐", "수육", "보쌈", "족발", "찜닭", "마라탕", "마라샹궈", "마라", "훠궈", "쌀국수",
        "분짜", "팟타이", "카레", "돈부리", "텐동", "에비텐동", "솥밥", "가지솥밥", "도미솥밥", "누룽지",
        "떡볶이", "로제떡볶이", "마라떡볶이", "국물떡볶이", "오마카세", "스시오마카세", "튀김",
        "들깨수제비", "수제비", "김피탕", "피탕"
    ]

    RAW_FISH_PATTERNS = [
        re.compile(r"(?<![가-힣])((?:광어|연어|방어|모둠|모듬|참치|활어|숙성|생선)\s+회)(?![가-힣])"),
        re.compile(r"(?<![가-힣])(회\s+(?:한\s*접시|포장|세트|뜨[는고]))(?![가-힣])"),
    ]

    # Cafe & Dessert signals
    CAFE_KEYWORDS = [
        "카페", "디저트", "베이커리", "베이커리카페", "빵집", "과자점", "찻집"
    ]

    CAFE_DISHES = [
        "소금빵", "식빵", "초코식빵", "크루아상", "케이크", "치즈케이크", "딸기케이크",
        "쿠키", "빙수", "망고빙수", "라떼", "딸기라떼", "바닐라라떼", "커피", "아메리카노", "에스프레소", "크림라떼",
        "에이드", "말차", "말차라떼", "녹차", "젤라또", "아이스크림", "찹쌀떡", "요거트찹쌀떡",
        "와플", "크로플", "도넛", "타르트", "마카롱", "스콘", "휘낭시에", "마들렌",
        "베이글", "샌드위치", "푸딩", "밀크티", "요거트", "그릭요거트",
        "팬케이크", "수플레", "수플레팬케이크",
        "과일산도", "산도", "잠봉뵈르", "잠봉"
    ]

    # Convenience store & retail food product signals
    PRODUCT_BRAND_SIGNALS = [
        "gs25", "cu", "세븐일레븐", "이마트24", "미니스톱", "편의점", "신상디저트", "신제품",
        "편의점신상", "신상", "한정판", "한정선"
    ]

    # Non-dining service / wellness signals that should NOT be misclassified as dining
    NON_DINING_SERVICES = [
        "마사지", "스웨디시", "아로마", "에스테틱", "피부관리", "피부과", "성형외과", "체형교정",
        "네일", "네일아트", "미용실", "헤어샵", "왁싱", "필라테스", "피티", "헬스장", "숙소", "호텔", "펜션", "모텔", "게스트하우스"
    ]

    # Non-food product review signals (appliances, home, living)
    NON_FOOD_PRODUCT_SIGNALS = [
        "탄소매트", "온열매트", "전기매트", "온수매트", "매트", "토퍼", "매트리스",
        "청소기", "건조기", "세탁기", "공기청정기", "가습기", "제습기", "로봇청소기",
        "의자", "침대", "베개", "소파", "책상", "가구", "인테리어",
        "노트북", "모니터", "키보드", "마우스", "스마트폰", "태블릿", "이어폰", "헤드폰",
        "화장품", "앰플", "세럼", "크림", "선크림", "샴푸", "트리트먼트", "바디워시",
        "자동차", "타이어", "블랙박스", "네비게이션", "헬멧", "자전거", "텐트", "캠핑용품"
    ]

    # Secondary / Non-food anchors that should NOT take precedence over food details
    SECONDARY_KEYWORDS = [
        "주차", "주차장", "주차자리", "발렛", "위치", "접근성", "역세권", "매장", "내부",
        "인테리어", "분위기", "창가", "창가자리", "오션뷰", "마운틴뷰", "뷰", "테라스",
        "단체석", "룸", "예약", "웨이팅", "영업시간", "브레이크타임", "가성비", "친절"
    ]

    @classmethod
    def _match_dishes(cls, dish_list: List[str], text: str) -> List[str]:
        matched: List[str] = []
        for dish in dish_list:
            if len(dish) <= 2:
                # 2글자 이하 단어는 단어 경계를 확인하여 '침구 이용', '버거워' 등의 부분 문자열 오탐 방지
                pattern = rf"(?<![가-힣]){re.escape(dish)}(?![가-힣])"
                if re.search(pattern, text):
                    matched.append(dish)
            else:
                if dish in text:
                    matched.append(dish)
        return matched

    @classmethod
    def analyze(cls, title: str, excerpt: str) -> Dict[str, Any]:
        """Analyzes title and excerpt to determine content focus and extracted anchors."""
        title_norm = (title or "").lower()
        excerpt_norm = (excerpt or "").lower()
        combined_text = f"{title_norm} {excerpt_norm}"

        food_anchors: List[str] = []
        secondary_anchors: List[str] = []

        # 1. 비음식 서비스 및 비음식 제품 신호 확인
        is_non_dining_service = any(kw in combined_text for kw in cls.NON_DINING_SERVICES)
        has_non_food_product_title = any(kw in title_norm for kw in cls.NON_FOOD_PRODUCT_SIGNALS)

        # 2. Secondary place/convenience anchors
        for kw in cls.SECONDARY_KEYWORDS:
            if kw in combined_text and kw not in secondary_anchors:
                secondary_anchors.append(kw)

        # 3. 디시 및 키워드 매칭
        matched_cafe_dishes = cls._match_dishes(cls.CAFE_DISHES, combined_text)
        matched_restaurant_dishes = cls._match_dishes(cls.RESTAURANT_DISHES, combined_text)
        for pat in cls.RAW_FISH_PATTERNS:
            for m in pat.finditer(combined_text):
                matched_restaurant_dishes.append(m.group(1))

        # 제목에 명확한 식당/카페 신호가 있는지 확인
        title_has_restaurant_dish = any(d in title_norm for d in cls.RESTAURANT_DISHES)
        title_has_cafe_dish = any(d in title_norm for d in cls.CAFE_DISHES)
        title_has_strong_restaurant = any(kw in title_norm for kw in cls.STRONG_RESTAURANT_KEYWORDS)
        title_has_cafe = any(kw in title_norm for kw in cls.CAFE_KEYWORDS)
        title_has_food = (
            title_has_strong_restaurant
            or title_has_cafe
            or title_has_restaurant_dish
            or title_has_cafe_dish
        )

        # 제목이 명확히 비음식 제품/가전/가구 리뷰인데 제목에 음식 신호가 전혀 없으면 GENERAL로 분류 (탄소매트 등 오탐 방지)
        if has_non_food_product_title and not title_has_food:
            return {
                "focus": "GENERAL",
                "food_anchors": [],
                "secondary_anchors": secondary_anchors,
                "has_food_details": False,
            }

        # 4. 편의점 / 가공식품 신제품
        has_product_brand = any(brand in combined_text for brand in cls.PRODUCT_BRAND_SIGNALS)
        if has_product_brand and (matched_cafe_dishes or "디저트" in combined_text or "젤라또" in combined_text or "찹쌀떡" in combined_text):
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

        # 비음식 서비스(마사지, 미용 등)가 우세하고 실제 음식 디시가 없으면 GENERAL
        if is_non_dining_service and not matched_restaurant_dishes and not matched_cafe_dishes:
            return {
                "focus": "GENERAL",
                "food_anchors": [],
                "secondary_anchors": secondary_anchors,
                "has_food_details": False,
            }

        # 5. Cafe / Dessert 판정
        has_cafe_kw = any(kw in combined_text for kw in cls.CAFE_KEYWORDS)
        is_cafe = (
            (has_cafe_kw and not is_non_dining_service and (matched_cafe_dishes or title_has_cafe))
            or (matched_cafe_dishes and not matched_restaurant_dishes and (title_has_food or len(matched_cafe_dishes) >= 2))
        )
        if is_cafe:
            for d in matched_cafe_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            food_anchors.sort(key=lambda a: (0 if a in title_norm else 1, -len(a)))
            return {
                "focus": "CAFE_DESSERT",
                "food_anchors": food_anchors,
                "secondary_anchors": secondary_anchors,
                "has_food_details": len(food_anchors) > 0,
            }

        # 6. Restaurant / Dining 판정
        # 강한 맛집 키워드 또는 구체적 디시가 존재하고, 제목 신호나 명확한 본문 증거가 있을 때
        has_strong_rest = any(kw in combined_text for kw in cls.STRONG_RESTAURANT_KEYWORDS)
        is_restaurant = False
        if title_has_strong_restaurant or title_has_restaurant_dish:
            is_restaurant = True
        elif has_strong_rest and (matched_restaurant_dishes or any(kw in combined_text for kw in ("메뉴", "주문", "식사"))):
            is_restaurant = True
        elif len(matched_restaurant_dishes) >= 2:
            is_restaurant = True
        elif matched_restaurant_dishes and any(kw in combined_text for kw in cls.WEAK_RESTAURANT_KEYWORDS):
            is_restaurant = True

        if is_restaurant:
            for d in matched_restaurant_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            for d in matched_cafe_dishes:
                if d not in food_anchors:
                    food_anchors.append(d)
            food_anchors.sort(key=lambda a: (0 if a in title_norm else 1, -len(a)))
            return {
                "focus": "FOOD_RESTAURANT",
                "food_anchors": food_anchors,
                "secondary_anchors": secondary_anchors,
                "has_food_details": len(food_anchors) > 0,
            }

        # 7. General non-food post
        return {
            "focus": "GENERAL",
            "food_anchors": [],
            "secondary_anchors": secondary_anchors,
            "has_food_details": False,
        }
