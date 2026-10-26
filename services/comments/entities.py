import re
from typing import List, Set

META_SUBJECTS: Set[str] = {
    "맛집", "카페", "여행", "후기", "리뷰", "추천", "메뉴", "정보", "제품", "일상",
    "방문", "글", "포스팅", "기록", "내돈내산", "사진", "다녀왔", "음식점", "식당",
    "서울", "부산", "제주", "성수동", "강남", "홍대", "광양", "강릉", "남해", "속초",
    "포항", "을지로", "익선동", "한남동", "망원동", "연남동", "강남역", "경주", "도산공원",
    "애월", "경포대", "여수", "다낭", "방콕", "가평", "도쿄", "반포", "중앙시장", "죽도시장",
    "독일마을", "골목", "샵", "노포", "대형", "근교"
}

ALLOWLIST_1CHAR: Set[str] = {"책", "앱", "펌", "룩", "옷", "뷰"}

STOPWORDS: Set[str] = {
    "오늘", "이번", "정말", "너무", "그리고", "하지만", "그래서", "했어요", "합니다",
    "입니다", "있어요", "있습니다", "같아요", "생각", "공유", "안내", "위치", "근처", "어제",
    "줄서는", "만드는", "집에서", "여름", "겨울", "가을", "봄", "주말", "평일", "아침",
    "점심", "저녁", "신상", "솔직후기", "매콤", "칼칼한", "달달하고", "바삭한", "쫀득하고",
    "제철", "마무리", "살수율", "코스", "조합", "감성", "개조", "모습", "분위기", "특징"
}


def is_valid_subject(token: str) -> bool:
    """단어가 유효한 구체 대상(Subject Entity)인지 검증"""
    if not token or len(token) < 1:
        return False
    clean = token.strip()
    if clean.isdigit():
        return False
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
