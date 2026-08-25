import os
import re
import json
import time
import hashlib
from typing import List, Dict, Any, Optional
from playwright.sync_api import sync_playwright

OUTPUT_CORPUS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "naver_comment_corpus.json"))

# 16대 카테고리별 시드 검색어 (일반 블로그 및 인기 주제 포스트 탐색용)
CATEGORY_SEEDS = {
    "FOOD": ["서울 맛집 내돈내산 후기", "파스타 맛집 솔직후기", "삼겹살 솥뚜껑 구이", "장어덮밥 히츠마부시 후기"],
    "CAFE": ["성수동 디저트 카페", "한옥 카페 아인슈페너", "소금빵 베이커리 카페", "오션뷰 감성 카페"],
    "TRAVEL": ["국내 힐링 여행 코스", "제주도 동쪽 여행 코스", "여수 밤바다 낭만포차", "강릉 바다 드라이브"],
    "HOBBY_GOODS": ["스폰지밥 랜덤키링 내돈내산", "산리오 피규어 가챠", "다이소 다꾸 스티커 후기", "포켓몬 팝업스토어 굿즈"],
    "BEAUTY": ["시스루 펌 헤어스타일 후기", "봄 웜톤 립스틱 발색", "속눈썹 펌 솔직후기", "피부과 레이저 관리 후기"],
    "FASHION": ["여자 데일리룩 출근룩", "봄 자켓 코디 추천", "나이키 운동화 착용샷", "디자이너 가방 코디"],
    "LIFESTYLE": ["원룸 인테리어 조명 방꾸미기", "살림 꿀팁 다이소 추천템", "주말 일상 브이로그 기록", "내돈내산 살림템 후기"],
    "PET": ["강아지 산책 일상 댕댕이", "고양이 츄르 간식 추천", "반려견 동반 카페 나들이", "먼치킨 고양이 장난감"],
    "IT_GADGET": ["아이패드 에어 사용기 솔직리뷰", "맥북 M3 실사용 후기", "에어팟 프로 음질 비교", "아이폰 생산성 앱 추천"],
    "BOOK_MOVIE": ["주말에 읽기 좋은 에세이 책", "베스트셀러 소설 독서 기록", "넷플릭스 영화 추천 감상평", "전시회 관람 솔직 후기"],
    "FITNESS": ["오운완 헬스 루틴 기록", "필라테스 3개월 솔직후기", "러닝 크루 10km 마라톤", "크로스핏 초보 운동 일기"],
    "PARENTING": ["어린이집 입소 준비물", "유아 식판식 아기 반찬", "돌 아기 장난감 추천", "아이랑 주말 가볼만한곳"],
    "FINANCE": ["미국 배당주 ETF 공부 기록", "청년 도약 계좌 꿀팁", "주린이 주식 매매 일지", "가계부 정산 절약 기록"],
    "WORK": ["신입사원 이직 준비 기록", "퇴사 후 프리랜서 일상", "자격증 합격 공부 꿀팁", "직장인 점심 일상"],
    "INTERIOR": ["아파트 거실 인테리어 후기", "신혼집 가구 배치 팁", "맞춤 커튼 인테리어 완성", "베란다 홈카페 꾸미기"],
    "UNKNOWN_TOPIC": ["주말 일상 힐링 기록", "오늘의 날씨 산책 일기", "소소한 하루 일상 이야기"]
}

# 매크로 패턴 필터 (제외 대상)
MACRO_FILTER = re.compile(
    r'(잘\s*보고\s*갑니다|서이추|이웃\s*신청|포스팅\s*잘|하트\s*꾹|답방|맞방|소통\s*해요|오늘도\s*좋은\s*하루|'
    r'좋은\s*글\s*감사|유익한\s*정보|공감하고\s*가요|서로이웃|블로그\s*수익|체험단|협찬)',
    re.IGNORECASE
)

# 개인정보 정제 정규식
NICKNAME_SCRUBBER = re.compile(r'@[\w\d_]+')
EMAIL_SCRUBBER = re.compile(r'[\w\.-]+@[\w\.-]+')
PHONE_SCRUBBER = re.compile(r'01[016789]-?\d{3,4}-?\d{4}')


class NaverCommentCorpusMiner:
    """
    네이버 모바일 블로그 실시간 코퍼스 수집기:
    - 16대 주제별 공개 포스트 탐색 및 양질의 실제 댓글 크롤링
    - 개인정보(닉네임, 연락처 등) 완전 비식별화
    - 매크로 및 단순 복사형 중복 댓글 필터링
    - 본문 앵커(사이드 메뉴, 팁, 디테일) 추출 및 아키타입 태깅
    """

    @staticmethod
    def anonymize_text(text: str) -> str:
        t = NICKNAME_SCRUBBER.sub("", text)
        t = EMAIL_SCRUBBER.sub("", t)
        t = PHONE_SCRUBBER.sub("", t)
        return t.strip()

    @classmethod
    def is_quality_comment(cls, text: str) -> bool:
        t = text.strip()
        if len(t) < 10 or len(t) > 180:
            return False
        if MACRO_FILTER.search(t):
            return False
        if "http://" in t or "https://" in t:
            return False
        return True

    @classmethod
    def mine_sample_corpus(cls, max_posts_per_category: int = 2) -> List[Dict[str, Any]]:
        results = []
        os.makedirs(os.path.dirname(OUTPUT_CORPUS_FILE), exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                viewport={"width": 390, "height": 844},
                is_mobile=True
            )
            page = context.new_page()

            for category, seeds in CATEGORY_SEEDS.items():
                print(f"[*] Mining category: {category}...")
                for query in seeds[:max_posts_per_category]:
                    try:
                        # 모바일 네이버 블로그 검색
                        search_url = f"https://m.search.naver.com/search.naver?where=m_blog&query={query}"
                        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(1000)

                        # 검색 결과 블로그 링크 추출
                        links = page.evaluate("""
                            () => {
                                const els = Array.from(document.querySelectorAll("a[href*='m.blog.naver.com/']"));
                                return els.map(a => a.href).filter(h => !h.includes('/search') && !h.includes('MyBlog')).slice(0, 2);
                            }
                        """)

                        for blog_url in links:
                            try:
                                post_page = context.new_page()
                                post_page.goto(blog_url, wait_until="domcontentloaded", timeout=20000)
                                post_page.wait_for_timeout(1500)

                                title = post_page.evaluate("() => document.querySelector('.se-title-text, div.tit_area h3, title')?.innerText || ''").strip()
                                excerpt = post_page.evaluate("() => document.querySelector('.se-main-container, #postViewArea')?.innerText || ''").strip()[:300]

                                # 댓글 버튼 클릭
                                cmt_btn = post_page.locator("button[data-click-area*='.re'], button.Interact__comment_btn--Wbuoq, button:has(.blind:text-is('댓글'))").first
                                if cmt_btn.count() > 0:
                                    cmt_btn.click(timeout=1000)
                                    post_page.wait_for_timeout(1500)

                                raw_comments = post_page.evaluate("""
                                    () => {
                                        const items = Array.from(document.querySelectorAll("li.u_cbox_comment, li[class*='cbox_comment']"));
                                        return items.map(el => {
                                            const txtEl = el.querySelector(".u_cbox_contents, .u_cbox_text_mention, p.text");
                                            return txtEl ? txtEl.innerText.trim() : "";
                                        }).filter(t => t.length > 5);
                                    }
                                """)

                                post_hash = hashlib.sha256(blog_url.encode('utf-8')).hexdigest()[:12]

                                for c_text in raw_comments:
                                    clean_txt = cls.anonymize_text(c_text)
                                    if cls.is_quality_comment(clean_txt):
                                        # 엔티티 앵커 추출
                                        anchors = [w for w in re.findall(r'[가-힣]{2,6}', clean_txt) if w in excerpt and w not in ["정말", "너무", "진짜", "좋네요", "보고"]]
                                        item = {
                                            "post_id_hash": post_hash,
                                            "source": "naver_blog",
                                            "category": category,
                                            "post_title": title,
                                            "comment_text": clean_txt,
                                            "anchor_terms": anchors[:3],
                                            "reaction_type": "DETAIL_PRAISE" if len(anchors) > 0 else "WARM_EMPATHY",
                                            "length": len(clean_txt)
                                        }
                                        results.append(item)

                                post_page.close()
                            except Exception as pe:
                                pass

                    except Exception as se:
                        print(f"Search query error '{query}': {se}")

            browser.close()

        with open(OUTPUT_CORPUS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"[+] Successfully mined {len(results)} high-quality real comments across {len(CATEGORY_SEEDS)} categories!")
        return results


if __name__ == "__main__":
    NaverCommentCorpusMiner.mine_sample_corpus(max_posts_per_category=1)
