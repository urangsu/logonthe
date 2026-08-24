import re
import random
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from app.models import FeedSourceType


@dataclass
class ContextualDraftResult:
    body: str
    category: str
    subject: str
    template_id: str
    confidence: float  # 0.0 ~ 1.0


class ContextualDraftEngine:
    """
    네이버 블로그 게시글의 제목과 본문 핵심 텍스트를 분석하여
    글 내용과 자연스럽게 연결된 따뜻하고 짧은 칭찬형 로컬 댓글을 생성하는 규칙 기반 엔진.
    (외부 LLM / API 의존성 없음)
    """

    CATEGORIES = {
        "FOOD": [
            "맛집", "메뉴", "고기", "삼겹살", "장어", "돈까스", "돈카츠", "파스타", "리조또",
            "쭈꾸미", "비빔밥", "수제비", "빵", "국수", "라면", "치킨", "회", "초밥", "덮밥",
            "디저트", "히츠마부시", "순두부", "찌개", "구이", "식당", "음식점", "점심", "저녁"
        ],
        "CAFE": [
            "카페", "커피", "라떼", "아메리카노", "디저트", "케이크", "빙수", "말차", "녹차",
            "딸기라떼", "베이커리", "음료", "원두", "찻집", "티룸"
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

    TEMPLATES = {
        "FOOD": [
            ("food_1", "{subject} 진짜 맛있어 보여요. 사진 보니까 더 먹어보고 싶네요 :)"),
            ("food_2", "{subject}부터 눈에 확 들어오네요. 보기만 해도 군침 도네요!"),
            ("food_3", "{subject} 비주얼이 너무 좋아 보여요. 저도 한번 맛보고 싶네요 :)"),
            ("food_4", "음식들이 다 정갈하고 맛있어 보여요. {subject} 특히 눈길이 가네요!")
        ],
        "CAFE": [
            ("cafe_1", "{subject}가 눈에 확 들어오네요. 분위기도 편안해 보여서 좋네요 :)"),
            ("cafe_2", "사진 분위기가 편안해서 참 좋네요. {subject}도 한번 맛보고 싶어요 :)"),
            ("cafe_3", "{subject} 색감부터 너무 좋아 보여요. 이런 아늑한 카페 분위기 참 좋네요!"),
            ("cafe_4", "공간 분위기도 아늑해 보이고 {subject}도 너무 맛있어 보이네요 :)")
        ],
        "TRAVEL": [
            ("travel_1", "{subject} 분위기가 정말 좋아 보이네요. 천천히 둘러보기 좋을 것 같아요 :)"),
            ("travel_2", "{subject} 사진 보니까 저도 한번 가보고 싶네요. 편안한 분위기가 참 좋아요!"),
            ("travel_3", "사진만 봐도 {subject} 힐링되는 느낌이네요. 풍경이 참 예뻐요 :)"),
            ("travel_4", "여유롭게 산책하며 둘러보기 좋아 보이네요. 사진 분위기가 참 좋아요 :)")
        ],
        "BEAUTY": [
            ("beauty_1", "{subject} 느낌이 자연스럽고 깔끔해 보여요. 분위기가 잘 살아나네요 :)"),
            ("beauty_2", "{subject} 스타일이 너무 잘 어울리시네요. 깔끔해서 보기 좋아요!")
        ],
        "PRODUCT": [
            ("prod_1", "{subject}가 눈에 쏙 들어오네요. 솔직한 후기라 더 편하게 봤어요 :)"),
            ("prod_2", "{subject} 깔끔하게 정리해 주셔서 보기 편하네요. 좋은 글 잘 봤습니다 :)")
        ],
        "FINANCE": [
            ("fin_1", "{subject} 관점이 눈에 들어오네요. 내용이 깔끔하게 정리되어 보기 편했어요."),
            ("fin_2", "{subject} 관련해서 흐름을 편하게 볼 수 있어서 좋았어요.")
        ],
        "GENERAL": [
            ("gen_1", "{subject} 얘기가 특히 눈에 들어오네요. 글 분위기가 편안해서 좋아요 :)"),
            ("gen_2", "{subject} 보니까 더 관심이 가네요. 사진이랑 같이 보니 느낌이 잘 전해져요!"),
            ("gen_no_subj_1", "사진 분위기가 참 편안하고 좋네요. 부담 없이 기분 좋게 읽었어요 :)"),
            ("gen_no_subj_2", "보기만 해도 기분 좋아지는 글이네요. 사진 분위기가 너무 따뜻해요 :)")
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
        combined = f"{title_s} {excerpt_s}"

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
        # 제목의 명사성 토큰 우선
        for token in title_tokens:
            if len(token) >= 2 and token not in cls.STOPWORDS:
                # 카테고리 대표 키워드와 매칭되거나 유의미한 단어
                if best_cat != "GENERAL" and any(k in token for k in cls.CATEGORIES[best_cat]):
                    subject = token
                    break
                elif len(token) >= 3:
                    subject = token
                    break

        if not subject and title_tokens:
            subject = title_tokens[0]

        # 3. 신뢰도(Confidence) 계산
        confidence = 0.4
        if title_s:
            confidence += 0.2
        if len(excerpt_s) >= 80:
            confidence += 0.2
        if subject:
            confidence += 0.2

        return best_cat, subject, round(min(1.0, confidence), 2)

    @classmethod
    def generate(cls, title: str, excerpt: str = "") -> ContextualDraftResult:
        """제목과 본문을 기반으로 자연스러운 로컬 댓글 초안 생성"""
        category, subject, conf = cls.detect_category_and_subject(title, excerpt)

        # 템플릿 목록 선택
        tmpl_list = cls.TEMPLATES.get(category, cls.TEMPLATES["GENERAL"])

        # subject가 없을 때의 fallback
        if not subject:
            tmpl_list = [t for t in cls.TEMPLATES["GENERAL"] if "{subject}" not in t[1]]

        # 최근 사용된 template_id 회피
        available = [t for t in tmpl_list if t[0] not in cls._recent_template_ids]
        if not available:
            cls._recent_template_ids.clear()
            available = tmpl_list

        chosen_id, chosen_tmpl = random.choice(available)
        cls._recent_template_ids.append(chosen_id)

        # 템플릿에 subject 대입
        body = chosen_tmpl.replace("{subject}", subject) if subject else chosen_tmpl

        return ContextualDraftResult(
            body=body,
            category=category,
            subject=subject,
            template_id=chosen_id,
            confidence=conf
        )
