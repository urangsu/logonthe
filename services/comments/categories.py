from dataclasses import dataclass
from typing import List, Dict, Any
from services.comments.intents import ReactionIntent


@dataclass
class CategoryPolicy:
    name: str
    keywords: List[str]
    actions: List[str]                  # 예: "먹어보다", "가보다", "마셔보다", "뽑아보다"
    reaction_weights: Dict[ReactionIntent, float]
    subject_suffixes: List[str]


CATEGORY_POLICIES: Dict[str, CategoryPolicy] = {
    # 1. 취미/캐릭터/굿즈 (키링, 가챠, 피규어 등 - FOOD보다 높은 식별 우선순위)
    "HOBBY_GOODS": CategoryPolicy(
        name="HOBBY_GOODS",
        keywords=[
            "키링", "랜덤키링", "스폰지밥", "굿즈", "캐릭터", "피규어", "인형", "아크릴", "포토카드",
            "랜덤박스", "가챠", "스티커", "콜라보", "한정판", "팝업", "문구", "다꾸", "키덜트", "뱃지", "마그넷"
        ],
        actions=["뽑아보다", "모아보다", "구경해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.5,
            ReactionIntent.DETAIL_PRAISE: 1.4,
            ReactionIntent.TRY_INTENT: 1.3,
            ReactionIntent.PREFERENCE: 1.2,
            ReactionIntent.PLAN_INTENT: 0.8,
            ReactionIntent.EMPATHY: 0.8,
            ReactionIntent.INFO_REACTION: 0.5,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["디자인", "캐릭터", "굿즈"]
    ),

    # 2. 맛집/식당
    "FOOD": CategoryPolicy(
        name="FOOD",
        keywords=[
            "맛집", "메뉴", "고기", "삼겹살", "장어", "돈까스", "돈카츠", "파스타", "리조또",
            "쭈꾸미", "비빔밥", "수제비", "국수", "라면", "치킨", "초밥", "덮밥",
            "디저트", "히츠마부시", "순두부", "찌개", "구이", "식당", "음식점", "점심", "저녁", "오차즈케",
            "우동", "스테이크", "피자", "카레", "버거", "샐러드", "전골", "떡볶이", "베이커리", "빵집"
        ],
        actions=["먹어보다", "주문해보다", "맛보고 싶다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.4,
            ReactionIntent.DETAIL_PRAISE: 1.5,
            ReactionIntent.TRY_INTENT: 1.3,
            ReactionIntent.PLAN_INTENT: 0.9,
            ReactionIntent.PREFERENCE: 0.9,
            ReactionIntent.EMPATHY: 0.6,
            ReactionIntent.INFO_REACTION: 0.5,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["메뉴", "음식"]
    ),

    # 3. 카페/음료
    "CAFE": CategoryPolicy(
        name="CAFE",
        keywords=[
            "카페", "커피", "라떼", "아메리카노", "디저트", "케이크", "빙수", "말차", "녹차",
            "딸기라떼", "베이커리", "음료", "원두", "찻집", "티룸", "크로플", "소금빵", "에이드", "스콘"
        ],
        actions=["마셔보다", "가보다", "들러보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.4,
            ReactionIntent.DETAIL_PRAISE: 1.5,
            ReactionIntent.TRY_INTENT: 1.2,
            ReactionIntent.PLAN_INTENT: 1.0,
            ReactionIntent.PREFERENCE: 1.0,
            ReactionIntent.EMPATHY: 0.8,
            ReactionIntent.INFO_REACTION: 0.4,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["음료", "디저트"]
    ),

    # 4. 여행/나들이
    "TRAVEL": CategoryPolicy(
        name="TRAVEL",
        keywords=[
            "여행", "산책", "공원", "전시", "미술관", "섬", "바다", "해변", "숙소", "호텔",
            "축제", "정원", "수영장", "관광", "코스", "펜션", "리조트", "드라이브", "나들이", "명소",
            "캠핑", "글램핑", "풍경", "야경", "숲", "계곡", "일몰", "등산"
        ],
        actions=["가보다", "들러보다", "둘러보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.3,
            ReactionIntent.DETAIL_PRAISE: 1.5,
            ReactionIntent.TRY_INTENT: 1.1,
            ReactionIntent.PLAN_INTENT: 1.4,
            ReactionIntent.PREFERENCE: 0.7,
            ReactionIntent.EMPATHY: 0.8,
            ReactionIntent.INFO_REACTION: 0.6,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["명소", "코스"]
    ),

    # 5. 뷰티/헤어
    "BEAUTY": CategoryPolicy(
        name="BEAUTY",
        keywords=[
            "헤어", "펌", "커트", "미용실", "스타일", "염색", "시스루", "쉐도우펌", "네일", "피부", "메이크업", "클리닉"
        ],
        actions=["참고해보다", "해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.5,
            ReactionIntent.DETAIL_PRAISE: 1.3,
            ReactionIntent.TRY_INTENT: 0.8,
            ReactionIntent.PLAN_INTENT: 0.7,
            ReactionIntent.PREFERENCE: 1.2,
            ReactionIntent.EMPATHY: 0.6,
            ReactionIntent.INFO_REACTION: 0.3,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["스타일"]
    ),

    # 6. 패션/코디
    "FASHION": CategoryPolicy(
        name="FASHION",
        keywords=[
            "코디", "패션", "자켓", "원피스", "셔츠", "팬츠", "신발", "스니커즈", "가방", "모자", "아우터", "룩북", "OOTD"
        ],
        actions=["입어보다", "참고해보다", "매치해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.5,
            ReactionIntent.DETAIL_PRAISE: 1.4,
            ReactionIntent.PREFERENCE: 1.2,
            ReactionIntent.TRY_INTENT: 0.9,
            ReactionIntent.PLAN_INTENT: 0.5,
            ReactionIntent.EMPATHY: 0.6,
            ReactionIntent.INFO_REACTION: 0.2,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["조합", "스타일"]
    ),

    # 7. 일상/살림
    "LIFESTYLE": CategoryPolicy(
        name="LIFESTYLE",
        keywords=[
            "살림", "청소", "정리", "루틴", "습관", "수납", "일상", "요리", "레시피", "소품"
        ],
        actions=["따라 해보다", "적용해보다", "참고해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.1,
            ReactionIntent.DETAIL_PRAISE: 1.2,
            ReactionIntent.EMPATHY: 1.3,
            ReactionIntent.TRY_INTENT: 1.1,
            ReactionIntent.PLAN_INTENT: 0.8,
            ReactionIntent.PREFERENCE: 0.6,
            ReactionIntent.INFO_REACTION: 0.5,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["방식", "루틴"]
    ),

    # 8. 육아
    "PARENTING": CategoryPolicy(
        name="PARENTING",
        keywords=[
            "육아", "아이", "아기", "놀이", "유치원", "어린이집", "키즈카페", "장난감", "교구", "동화"
        ],
        actions=["참고해보다", "따라 해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.4,
            ReactionIntent.DETAIL_PRAISE: 1.2,
            ReactionIntent.EMPATHY: 1.4,
            ReactionIntent.TRY_INTENT: 0.9,
            ReactionIntent.PLAN_INTENT: 0.7,
            ReactionIntent.PREFERENCE: 0.4,
            ReactionIntent.INFO_REACTION: 0.4,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["놀이", "활동"]
    ),

    # 9. 반려동물
    "PET": CategoryPolicy(
        name="PET",
        keywords=[
            "강아지", "고양이", "반려견", "반려묘", "댕댕이", "냥이", "산책", "간식", "사료", "펫용품"
        ],
        actions=["참고해보다", "눈여겨보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.5,
            ReactionIntent.DETAIL_PRAISE: 1.3,
            ReactionIntent.EMPATHY: 1.4,
            ReactionIntent.TRY_INTENT: 0.7,
            ReactionIntent.PLAN_INTENT: 0.5,
            ReactionIntent.PREFERENCE: 0.6,
            ReactionIntent.INFO_REACTION: 0.3,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["모습"]
    ),

    # 10. 도서/영화
    "BOOK_MOVIE": CategoryPolicy(
        name="BOOK_MOVIE",
        keywords=[
            "독서", "도서", "책", "영화", "드라마", "넷플릭스", "소설", "에세이", "리뷰", "감상", "웹툰"
        ],
        actions=["읽어보다", "감상해보다", "챙겨보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.1,
            ReactionIntent.DETAIL_PRAISE: 1.4,
            ReactionIntent.EMPATHY: 1.1,
            ReactionIntent.TRY_INTENT: 1.1,
            ReactionIntent.PLAN_INTENT: 1.0,
            ReactionIntent.PREFERENCE: 0.6,
            ReactionIntent.INFO_REACTION: 0.4,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["작품"]
    ),

    # 11. IT/전자기기
    "IT_GADGET": CategoryPolicy(
        name="IT_GADGET",
        keywords=[
            "스마트폰", "아이폰", "갤럭시", "노트북", "맥북", "태블릿", "아이패드", "전자기기", "모니터", "키보드", "이어폰", "앱"
        ],
        actions=["써보다", "참고해보다", "알아보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.0,
            ReactionIntent.DETAIL_PRAISE: 1.5,
            ReactionIntent.INFO_REACTION: 1.1,
            ReactionIntent.TRY_INTENT: 0.8,
            ReactionIntent.PLAN_INTENT: 0.6,
            ReactionIntent.PREFERENCE: 0.7,
            ReactionIntent.EMPATHY: 0.4,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["기능", "기기"]
    ),

    # 12. 운동/피트니스
    "FITNESS": CategoryPolicy(
        name="FITNESS",
        keywords=[
            "운동", "헬스", "피트니스", "필라테스", "요가", "러닝", "식단", "스트레칭", "홈트"
        ],
        actions=["따라 해보다", "적용해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.2,
            ReactionIntent.DETAIL_PRAISE: 1.4,
            ReactionIntent.TRY_INTENT: 1.2,
            ReactionIntent.PLAN_INTENT: 1.0,
            ReactionIntent.EMPATHY: 0.7,
            ReactionIntent.PREFERENCE: 0.5,
            ReactionIntent.INFO_REACTION: 0.4,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["루틴", "동작"]
    ),

    # 13. 인테리어
    "INTERIOR_HOME": CategoryPolicy(
        name="INTERIOR_HOME",
        keywords=[
            "인테리어", "홈스타일링", "가구", "조명", "소품", "방꾸미기", "침실", "거실", "주방"
        ],
        actions=["참고해보다", "꾸며보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.5,
            ReactionIntent.DETAIL_PRAISE: 1.5,
            ReactionIntent.PREFERENCE: 1.2,
            ReactionIntent.TRY_INTENT: 0.8,
            ReactionIntent.PLAN_INTENT: 0.8,
            ReactionIntent.EMPATHY: 0.5,
            ReactionIntent.INFO_REACTION: 0.3,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["공간", "조합"]
    ),

    # 14. 일반 제품/생활용품
    "PRODUCT": CategoryPolicy(
        name="PRODUCT",
        keywords=[
            "제품", "구매", "사용", "가격", "배송", "착용", "개봉", "언박싱", "후기", "생활용품", "가전"
        ],
        actions=["참고해보다", "살펴보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.0,
            ReactionIntent.DETAIL_PRAISE: 1.4,
            ReactionIntent.INFO_REACTION: 1.3,
            ReactionIntent.PLAN_INTENT: 0.6,
            ReactionIntent.PREFERENCE: 0.4,
            ReactionIntent.TRY_INTENT: 0.4,
            ReactionIntent.EMPATHY: 0.3,
            ReactionIntent.QUESTION: 0.1
        },
        subject_suffixes=["구성", "특징"]
    ),

    # 15. 경제/금융
    "FINANCE": CategoryPolicy(
        name="FINANCE",
        keywords=[
            "주식", "ETF", "투자", "시장", "종목", "금리", "경제", "부동산", "배당", "코인", "매매", "지표"
        ],
        actions=["참고해보다", "확인해보다"],
        reaction_weights={
            ReactionIntent.INFO_REACTION: 1.5,
            ReactionIntent.DETAIL_PRAISE: 1.1,
            ReactionIntent.PLAN_INTENT: 0.8,
            ReactionIntent.PRAISE: 0.5,
            ReactionIntent.TRY_INTENT: 0.4,
            ReactionIntent.EMPATHY: 0.4,
            ReactionIntent.QUESTION: 0.1,
            ReactionIntent.PREFERENCE: 0.1
        },
        subject_suffixes=["내용", "흐름"]
    ),

    # 16. 직장/업무
    "WORK": CategoryPolicy(
        name="WORK",
        keywords=[
            "업무", "직장", "회사", "이직", "취업", "노하우", "일잘러", "보고서", "미팅", "프로젝트"
        ],
        actions=["참고해보다", "적용해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.0,
            ReactionIntent.DETAIL_PRAISE: 1.3,
            ReactionIntent.INFO_REACTION: 1.0,
            ReactionIntent.TRY_INTENT: 0.9,
            ReactionIntent.PLAN_INTENT: 0.8,
            ReactionIntent.EMPATHY: 0.8,
            ReactionIntent.PREFERENCE: 0.4,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["방식", "노하우"]
    ),

    # 17. 미분류 (단순 일반 글)
    "UNKNOWN_TOPIC": CategoryPolicy(
        name="UNKNOWN_TOPIC",
        keywords=[],
        actions=["참고해보다", "해보다"],
        reaction_weights={
            ReactionIntent.PRAISE: 1.3,
            ReactionIntent.DETAIL_PRAISE: 1.4,
            ReactionIntent.TRY_INTENT: 1.0,
            ReactionIntent.PLAN_INTENT: 0.8,
            ReactionIntent.EMPATHY: 0.8,
            ReactionIntent.PREFERENCE: 0.6,
            ReactionIntent.INFO_REACTION: 0.5,
            ReactionIntent.QUESTION: 0.05
        },
        subject_suffixes=["내용"]
    )
}
