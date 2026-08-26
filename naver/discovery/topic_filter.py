import re
from typing import Tuple, Set
from services.comments.composer import HumanLikeComposerV31


TARGET_DISCOVERY_CATEGORIES: Set[str] = {
    "FOOD",
    "CAFE",
    "PARENTING",
    "LIVING",
    "INTERIOR_HOME",
    "TRAVEL",
    "LIFESTYLE"
}

# 강한 제외 키워드 (IT/스펙/금융/정책/속보)
BANNED_DISCOVERY_KEYWORDS = [
    "아이폰", "갤럭시", "스마트폰", "노트북", "카메라", "렌즈", "gpu", "cpu",
    "지원금", "정부정책", "주식", "코인", "증시", "속보", "사건", "환급금",
    "대출", "부동산 분양", "청약", "프로필 나이 학력"
]

# 생활형 카테고리 특화 키워드 매핑
FOOD_KEYWORDS = ["맛집", "식당", "먹방", "밥집", "고기", "구이", "파스타", "피자", "초밥", "찌개", "국밥", "메뉴", "점심", "저녁", "야식", "안주", "레시피", "요리", "학센", "플래터", "한식", "일식", "양식", "중식", "분식", "외식"]
CAFE_KEYWORDS = ["카페", "디저트", "베이커리", "빵집", "라떼", "커피", "브런치", "케이크", "에이드", "티룸", "스콘"]
PARENTING_KEYWORDS = ["육아", "아이랑", "아기랑", "어린이집", "유치원", "키즈카페", "초등", "맘스타그램", "아이와", "가족외식"]
LIVING_KEYWORDS = ["살림", "정리수납", "집꾸미기", "인테리어", "생활용품", "주방용품", "집밥", "다이소", "홈스타일링"]
TRAVEL_KEYWORDS = ["여행", "나들이", "산책", "투어", "숙소", "호텔", "펜션", "글램핑", "캠핑", "드라이브", "관광", "바다", "계곡", "휴가", "1박2일", "2박3일"]
LIFESTYLE_KEYWORDS = ["일상", "일기", "기록", "산책", "주말", "데이트", "동네", "소소한"]


class DiscoveryTopicFilter:
    """
    관심주제 탐색 전용 Whitelist 카테고리 게이트 (v9-lite)
    - 생활형 카테고리(FOOD, CAFE, PARENTING, LIVING, TRAVEL, LIFESTYLE)만 통과
    - IT/기기/금융/이슈/UNKNOWN_TOPIC 원천 탈락
    """

    @classmethod
    def is_allowed(cls, title: str, snippet: str = "") -> Tuple[bool, str]:
        t_clean = title.strip()
        t_lower = t_clean.lower()

        # 1. 강한 제외 키워드 검사 (보조 안전장치)
        for kw in BANNED_DISCOVERY_KEYWORDS:
            if kw.lower() in t_lower:
                return False, f"banned_keyword: '{kw}'"

        # 2. 생활형 특화 키워드 직접 우선 매칭 (검색 제목 최적화)
        if any(k in t_clean for k in FOOD_KEYWORDS):
            return True, "FOOD"
        if any(k in t_clean for k in CAFE_KEYWORDS):
            return True, "CAFE"
        if any(k in t_clean for k in PARENTING_KEYWORDS):
            return True, "PARENTING"
        if any(k in t_clean for k in LIVING_KEYWORDS):
            return True, "LIVING"
        if any(k in t_clean for k in TRAVEL_KEYWORDS):
            return True, "TRAVEL"
        if any(k in t_clean for k in LIFESTYLE_KEYWORDS):
            return True, "LIFESTYLE"

        # 3. 범용 엔진 카테고리 판별
        cat, _, confidence = HumanLikeComposerV31.detect_category_and_subjects(t_clean, snippet)

        # 4. Whitelist 검증
        if cat in TARGET_DISCOVERY_CATEGORIES:
            return True, cat

        return False, f"disallowed_category: '{cat}'"
