import re
import random
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from app.models import FeedSourceType


class FirstPersonIntent(str, Enum):
    NONE = "none"
    WANT_TO_VISIT = "want_to_visit"
    PLAN_TO_VISIT = "plan_to_visit"
    WANT_TO_EAT = "want_to_eat"
    WANT_TO_DRINK = "want_to_drink"
    LIKE_THIS_STYLE = "like_this_style"
    WOULD_CHOOSE = "would_choose"
    CURIOUS_TO_SEE = "curious_to_see"


@dataclass
class ContextualDraftResult:
    body: str
    category: str
    subject: str
    template_id: str
    intent: FirstPersonIntent
    confidence: float  # 0.0 ~ 1.0


class ContextualDraftEngine:
    """
    Human-Like Comment Composer v2.1
    - 6대 주제별(맛집/카페/여행/뷰티/제품/금융) 어휘 분석 및 구체적 Subject 추출
    - '나'의 취향/의향(FirstPersonIntent)이 자연스럽게 가미된 1~2문장의 따뜻한 한국어 대화체 댓글 생성
    - 과거 경험 위조 금지 (미래 의향 및 현재 공감만 허용)
    - 상투적 매크로 표현 배제 및 다채로운 문장 구조 조합
    """

    CATEGORIES = {
        "FOOD": [
            "맛집", "메뉴", "고기", "삼겹살", "장어", "돈까스", "돈카츠", "파스타", "리조또",
            "쭈꾸미", "비빔밥", "수제비", "빵", "국수", "라면", "치킨", "회", "초밥", "덮밥",
            "디저트", "히츠마부시", "순두부", "찌개", "구이", "식당", "음식점", "점심", "저녁", "오차즈케"
        ],
        "CAFE": [
            "카페", "커피", "라떼", "아메리카노", "디저트", "케이크", "빙수", "말차", "녹차",
            "딸기라떼", "베이커리", "음료", "원두", "찻집", "티룸", "크로플", "소금빵"
        ],
        "TRAVEL": [
            "여행", "산책", "공원", "전시", "미술관", "섬", "바다", "해변", "숙소", "호텔",
            "축제", "정원", "수영장", "관광", "코스", "펜션", "리조트", "드라이브", "나들이", "명소"
        ],
        "BEAUTY": [
            "헤어", "펌", "커트", "미용실", "스타일", "염색", "시스루", "쉐도우펌", "네일", "피부"
        ],
        "PRODUCT": [
            "제품", "구매", "사용", "가격", "배송", "착용", "개봉", "언박싱", "후기", "아이폰", "가전", "케이스"
        ],
        "FINANCE": [
            "주식", "ETF", "투자", "시장", "종목", "금리", "경제", "부동산", "배당", "코인", "매매"
        ]
    }

    STOPWORDS = {
        "오늘", "이번", "정말", "너무", "그리고", "하지만", "그래서", "포스팅", "블로그", "후기",
        "리뷰", "사진", "정보", "방문", "다녀왔", "했어요", "합니다", "입니다", "있어요",
        "있습니다", "같아요", "내돈내산", "일상", "생각", "추천", "공유", "안내", "위치"
    }

    # Human-like Multi-Intent Templates
    TEMPLATES = {
        "FOOD": [
            ("food_intent_1", "{subject} 진짜 맛있어 보여요. 다음에 가면 저도 꼭 먹어보고 싶네요 :)", FirstPersonIntent.WANT_TO_EAT),
            ("food_intent_2", "사진 보니까 {subject} 비주얼이 너무 좋네요. 저라면 이 메뉴부터 바로 주문해볼 것 같아요!", FirstPersonIntent.WOULD_CHOOSE),
            ("food_intent_3", "{subject}부터 눈에 확 들어오네요. 근처 갈 일 있으면 기억해뒀다 가보고 싶어요 :)", FirstPersonIntent.PLAN_TO_VISIT),
            ("food_pure_1", "{subject} 비주얼이 진짜 정갈하고 맛있어 보여요. 사진 보니까 군침 도네요!", FirstPersonIntent.NONE),
            ("food_pure_2", "음식들이 하나같이 다 깔끔해 보여요. {subject} 조합이 정말 좋아 보이네요 :)", FirstPersonIntent.NONE)
        ],
        "CAFE": [
            ("cafe_intent_1", "공간 분위기가 참 아늑하고 좋네요. 저도 이런 편안한 카페 좋아해서 한번 가보고 싶어요 :)", FirstPersonIntent.WANT_TO_VISIT),
            ("cafe_intent_2", "{subject} 색감부터 너무 예쁘네요. 다음에 가면 저도 이거 한번 마셔보고 싶어요!", FirstPersonIntent.WANT_TO_DRINK),
            ("cafe_intent_3", "{subject}도 맛있어 보이고 분위기도 취향이네요. 이쪽 갈 때 코스에 넣어봐야겠어요 :)", FirstPersonIntent.PLAN_TO_VISIT),
            ("cafe_pure_1", "사진 분위기가 따뜻해서 참 좋네요. {subject}도 너무 달콤하고 맛있어 보여요 :)", FirstPersonIntent.NONE),
            ("cafe_pure_2", "{subject}가 눈에 쏙 들어오네요. 여유롭게 커피 한잔하기 딱 좋아 보여요!", FirstPersonIntent.NONE)
        ],
        "TRAVEL": [
            ("travel_intent_1", "{subject} 풍경이 정말 평화로워 보이네요. 저도 기회 되면 천천히 둘러보러 가보고 싶어요 :)", FirstPersonIntent.WANT_TO_VISIT),
            ("travel_intent_2", "사진 보니까 힐링되는 느낌이에요. 다음에 이쪽 여행 가면 코스에 꼭 넣어봐야겠어요!", FirstPersonIntent.PLAN_TO_VISIT),
            ("travel_intent_3", "산책하듯 여유롭게 걷기 참 좋아 보이네요. 저도 날씨 좋을 때 한번 가보고 싶어요 :)", FirstPersonIntent.WANT_TO_VISIT),
            ("travel_pure_1", "{subject} 사진만 봐도 마음이 편안해지네요. 풍경이 참 예쁘고 운치 있어요 :)", FirstPersonIntent.NONE),
            ("travel_pure_2", "탁 트인 풍경이랑 분위기가 너무 멋지네요. 둘러보기 정말 좋아 보여요!", FirstPersonIntent.NONE)
        ],
        "BEAUTY": [
            ("beauty_intent_1", "{subject} 느낌이 자연스럽고 너무 예쁘네요. 저도 이런 깔끔한 스타일 좋아해요 :)", FirstPersonIntent.LIKE_THIS_STYLE),
            ("beauty_pure_1", "{subject} 스타일이 참 깔끔하게 잘 어울리시네요. 분위기가 너무 좋아요!", FirstPersonIntent.NONE)
        ],
        "PRODUCT": [
            ("prod_intent_1", "{subject} 눈여겨보고 있었는데 실사용 느낌이 잘 전해져서 좋네요. 솔직한 글 잘 봤어요 :)", FirstPersonIntent.CURIOUS_TO_SEE),
            ("prod_pure_1", "{subject} 깔끔하게 정리해 주셔서 보기 편하네요. 사진이랑 같이 보니 이해가 쏙 돼요 :)", FirstPersonIntent.NONE)
        ],
        "FINANCE": [
            ("fin_intent_1", "{subject} 관점이 특히 흥미롭네요. 핵심 흐름을 편하게 볼 수 있어서 좋았습니다.", FirstPersonIntent.NONE),
            ("fin_pure_1", "{subject} 관련 내용이 깔끔하게 잘 정리되어 있네요. 유익하게 잘 읽었습니다.", FirstPersonIntent.NONE)
        ],
        "GENERAL": [
            ("gen_intent_1", "{subject} 얘기 보니까 저도 관심이 가네요. 글 분위기가 편안해서 참 좋아요 :)", FirstPersonIntent.CURIOUS_TO_SEE),
            ("gen_pure_1", "{subject} 사진이랑 같이 보니까 느낌이 생생하게 전해지네요. 기분 좋게 잘 읽었습니다 :)", FirstPersonIntent.NONE),
            ("gen_no_subj_1", "사진 분위기가 참 따뜻하고 편안해서 좋네요. 부담 없이 기분 좋게 읽고 갑니다 :)", FirstPersonIntent.NONE),
            ("gen_no_subj_2", "보기만 해도 기분 좋아지는 글이네요. 사진 구도와 분위기가 너무 예뻐요 :)", FirstPersonIntent.NONE)
        ]
    }

    _recent_template_ids: deque = deque(maxlen=6)

    @classmethod
    def _extract_tokens(cls, text: str) -> List[str]:
        tokens = re.findall(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·&+\-]{1,15}", text)
        return [t for t in tokens if t not in cls.STOPWORDS and len(t) >= 2]

    @classmethod
    def detect_category_and_subject(cls, title: str, excerpt: str) -> Tuple[str, str, float]:
        title_s = title.strip()
        excerpt_s = excerpt.strip()

        # 1. 카테고리 점수 계산
        cat_scores: Dict[str, int] = {cat: 0 for cat in cls.CATEGORIES}
        title_tokens = cls._extract_tokens(title_s)
        excerpt_tokens = cls._extract_tokens(excerpt_s)

        for cat, keywords in cls.CATEGORIES.items():
            for kw in keywords:
                if kw in title_s:
                    cat_scores[cat] += 5
                for t in excerpt_tokens[:30]:
                    if kw in t:
                        cat_scores[cat] += 1

        best_cat = "GENERAL"
        best_score = 0
        for cat, sc in cat_scores.items():
            if sc > best_score:
                best_score = sc
                best_cat = cat

        # 2. Subject(핵심 대상) 후보 탐색
        subject = ""
        for token in title_tokens:
            if len(token) >= 2 and token not in cls.STOPWORDS:
                if best_cat != "GENERAL" and any(k in token for k in cls.CATEGORIES[best_cat]):
                    subject = token
                    break
                elif len(token) >= 3:
                    subject = token
                    break

        if not subject and title_tokens:
            subject = title_tokens[0]

        # 3. 신뢰도(Confidence) 계산
        confidence = 0.5
        if title_s:
            confidence += 0.2
        if len(excerpt_s) >= 80:
            confidence += 0.2
        if subject:
            confidence += 0.1

        return best_cat, subject, round(min(1.0, confidence), 2)

    @classmethod
    def generate(cls, title: str, excerpt: str = "") -> ContextualDraftResult:
        """제목과 본문을 분석하여 인간다운 First-Person 의향이 담긴 로컬 댓글 초안 생성"""
        category, subject, conf = cls.detect_category_and_subject(title, excerpt)

        tmpl_list = cls.TEMPLATES.get(category, cls.TEMPLATES["GENERAL"])

        if not subject:
            tmpl_list = [t for t in cls.TEMPLATES["GENERAL"] if "{subject}" not in t[1]]

        # 최근 템플릿 중복 회피
        available = [t for t in tmpl_list if t[0] not in cls._recent_template_ids]
        if not available:
            cls._recent_template_ids.clear()
            available = tmpl_list

        chosen_id, chosen_tmpl, chosen_intent = random.choice(available)
        cls._recent_template_ids.append(chosen_id)

        body = chosen_tmpl.replace("{subject}", subject) if subject else chosen_tmpl

        return ContextualDraftResult(
            body=body,
            category=category,
            subject=subject,
            template_id=chosen_id,
            intent=chosen_intent,
            confidence=conf
        )
