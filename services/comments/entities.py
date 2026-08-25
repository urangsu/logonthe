import re
from typing import List, Set

META_SUBJECTS: Set[str] = {
    "맛집", "카페", "여행", "후기", "리뷰", "추천", "메뉴", "정보", "제품", "일상",
    "방문", "글", "포스팅", "기록", "내돈내산", "사진", "다녀왔", "음식점", "식당",
    "서울", "부산", "제주", "성수동", "강남", "홍대", "광양", "강릉" # 단독 지역명도 구체 대상 없이는 subject 지양
}

ALLOWLIST_1CHAR: Set[str] = {"책", "앱", "펌", "룩", "옷"}

STOPWORDS: Set[str] = {
    "오늘", "이번", "정말", "너무", "그리고", "하지만", "그래서", "했어요", "합니다",
    "입니다", "있어요", "있습니다", "같아요", "생각", "공유", "안내", "위치", "근처", "어제"
}


def is_valid_subject(token: str) -> bool:
    """단어가 유효한 구체 대상(Subject Entity)인지 검증"""
    if not token or len(token) < 1:
        return False
    clean = token.strip()
    if len(clean) == 1 and clean not in ALLOWLIST_1CHAR:
        return False
    if clean in META_SUBJECTS or clean in STOPWORDS:
        return False
    return True


def extract_entity_tokens(text: str) -> List[str]:
    """텍스트에서 유효한 명사/키워드 엔티티 토큰 추출"""
    tokens = re.findall(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·&+\-]{0,15}", text)
    valid_tokens = []
    for t in tokens:
        if is_valid_subject(t):
            valid_tokens.append(t)
    return valid_tokens
