import re
from dataclasses import dataclass
from typing import Optional, Set, Tuple, List, Dict

TARGET_DISCOVERY_CATEGORIES: Set[str] = {
    "FOOD", "CAFE", "PARENTING", "LIVING", "INTERIOR_HOME", "TRAVEL", "LIFESTYLE"
}


@dataclass(frozen=True)
class TopicDecision:
    allowed: bool
    detected_category: Optional[str] = None
    confidence: str = "unknown"
    blocked_category: Optional[str] = None
    positive_evidence: Tuple[str, ...] = ()
    negative_evidence: Tuple[str, ...] = ()
    reason_code: str = ""
    stage: str = "card"

    @property
    def evidence(self) -> Tuple[str, ...]:
        """Backward compatibility for existing callers inspecting decision.evidence."""
        if not self.allowed and self.negative_evidence:
            return self.negative_evidence
        return self.positive_evidence or self.negative_evidence


class DiscoveryTopicFilter:
    """
    V13.3 Positive Category Gate + Contextual Negative Discovery Filter
    - 생활형 positive anchors (FOOD, CAFE, PARENTING, LIVING, TRAVEL, LIFESTYLE)
    - Title level strong exclusion (금융, IT 기기 스펙, 카메라 장비 스펙)
    - Snippet/Detail level contextual exclusion & contextual exception handling
    - Non-target/Unknown post rejection for Recommendation and Search
    """

    _POSITIVE_PATTERNS: Dict[str, Tuple[str, ...]] = {
        "FOOD": (
            r"맛집", r"식당", r"음식", r"메뉴", r"먹어", r"치킨", r"떡볶이", r"고기",
            r"갈비", r"게장", r"밀키트", r"레시피", r"요리", r"빵", r"소금빵", r"집밥",
            r"양식\s*맛집", r"양식", r"한식", r"일식", r"중식", r"분식", r"밥집", r"구이",
            r"파스타", r"피자", r"초밥", r"찌개", r"국밥", r"안주", r"야식", r"점심",
            r"저녁", r"외식", r"학센", r"플래터", r"텐동", r"마라탕", r"삼겹살", r"돼지고기",
            r"소고기", r"곱창", r"샤브", r"칼국수", r"우동", r"돈까스", r"라멘", r"홈베이킹",
            r"애플파이", r"베이킹",
        ),
        "CAFE": (
            r"카페", r"커피", r"라떼", r"디저트", r"케이크", r"브런치", r"베이커리",
            r"스콘", r"에이드", r"티룸", r"크로플", r"베이글", r"갸또", r"타르트",
            r"빙수", r"밀크티", r"도넛", r"휘낭시에", r"마카롱", r"에스프레소",
        ),
        "PARENTING": (
            r"육아", r"아이랑", r"아기랑", r"키즈", r"어린이", r"아이와", r"가족\s*나들이",
            r"어린이집", r"유치원", r"키즈카페", r"초등", r"맘스타그램", r"가족\s*외식",
            r"돌아기", r"육아일기", r"육아템",
        ),
        "LIVING": (
            r"살림", r"수납", r"정리", r"주방용품", r"생활용품", r"집꾸미기", r"홈스타일링",
            r"다이소", r"정리\s*수납", r"인테리어", r"식기건조대", r"침구", r"주방\s*정리",
            r"살림\s*꿀팁", r"욕실\s*정리", r"바디워시", r"가구\s*배치", r"방꾸미기",
        ),
        "TRAVEL": (
            r"여행", r"나들이", r"숙소", r"호텔", r"관광", r"1박\s*2일", r"2박\s*3일",
            r"휴양지", r"촬영지", r"펜션", r"글램핑", r"캠핑", r"드라이브", r"산책로",
            r"투어", r"해변", r"바다", r"계곡", r"휴가", r"오션뷰", r"명소", r"리조트",
            r"제주", r"국내여행", r"해외여행", r"유럽여행", r"명당", r"출사지",
        ),
        "LIFESTYLE": (
            r"일상", r"주말\s*기록", r"데이트", r"동네\s*산책", r"일기", r"소소한\s*일상",
            r"주말\s*일상", r"하루\s*기록", r"일상\s*블로그", r"산책", r"주말",
            r"휴일", r"spring", r"굿즈", r"랜덤박스", r"키링", r"콘택트렌즈",
        ),
    }

    _TITLE_STRONG_EXCLUSIONS: Dict[str, Tuple[str, ...]] = {
        "finance": (
            r"ETF", r"배당주", r"포트폴리오\s*정리", r"포트폴리오", r"코인", r"비트코인",
            r"가상화폐", r"증시\s*전망", r"주가\s*전망", r"청약\s*경쟁률", r"아파트\s*분양",
            r"부동산\s*분양", r"분양가", r"대출\s*금리", r"청년\s*(?:월세|청년)?\s*지원금",
            r"환급금\s*신청", r"채권\s*투자", r"재테크\s*전략", r"주식\s*(?:전망|투자|추천|매수|분석)",
        ),
        "camera": (
            r"미러리스\s*카메라", r"DSLR", r"단렌즈\s*리뷰", r"망원\s*렌즈",
            r"소니\s*(?:A\d|알파|FE\s*\d)", r"캐논\s*(?:EOS|R\d)", r"니콘\s*(?:Z\d|NIKKOR)",
            r"후지필름\s*(?:X-|GFX)", r"탐론\s*\d", r"시그마\s*\d",
            r"카메라\s*바디\s*(?:리뷰|스펙|성능)", r"렌즈\s*(?:리뷰|스펙|성능|추천)",
        ),
        "tech": (
            r"스마트폰\s*(?:스펙|비교|성능)", r"아이폰\s*\d+\s*(?:프로|맥스|플러스|출시일|성능|비교)?",
            r"갤럭시\s*(?:S|Z|A)\d+", r"노트북\s*(?:성능|스펙|비교|추천)",
            r"맥북\s*(?:프로|에어)", r"아이패드\s*(?:프로|에어)?", r"CPU\s*(?:GPU|벤치마크)?",
            r"GPU\s*성능", r"그래픽카드", r"조립\s*PC", r"모니터\s*(?:리뷰|스펙|성능)",
            r"이어폰\s*(?:리뷰|스펙|성능)", r"헤드폰\s*(?:리뷰|스펙|성능)", r"벤치마크",
        ),
    }

    @classmethod
    def _detect_positive_category(cls, title: str, snippet: str = "") -> Tuple[Optional[str], Tuple[str, ...]]:
        """제목 및 본문에서 생활형 타겟 카테고리와 긍정적 증거 탐색"""
        title_clean = title or ""
        snippet_clean = snippet or ""

        # 1. Title matching gets highest priority
        for cat, patterns in cls._POSITIVE_PATTERNS.items():
            ev = []
            for p in patterns:
                m = re.search(p, title_clean, flags=re.IGNORECASE)
                if m:
                    ev.append(m.group(0))
            if ev:
                return cat, tuple(ev)

        # 2. Snippet matching if title is ambiguous
        for cat, patterns in cls._POSITIVE_PATTERNS.items():
            ev = []
            for p in patterns:
                m = re.search(p, snippet_clean, flags=re.IGNORECASE)
                if m:
                    ev.append(m.group(0))
            if ev:
                return cat, tuple(ev)

        return None, ()

    @classmethod
    def _detect_title_strong_exclusion(cls, title: str) -> Tuple[Optional[str], Tuple[str, ...]]:
        """제목에 명백한 비대상 주제(금융, 카메라 기기, IT 기기)가 있는지 검사"""
        title_clean = title or ""
        for cat, patterns in cls._TITLE_STRONG_EXCLUSIONS.items():
            ev = []
            for p in patterns:
                m = re.search(p, title_clean, flags=re.IGNORECASE)
                if m:
                    ev.append(m.group(0))
            if ev:
                return cat, tuple(ev)
        return None, ()

    @classmethod
    def _detect_contextual_negatives(
        cls,
        title: str,
        snippet: str,
        detected_pos_cat: Optional[str]
    ) -> Tuple[Optional[str], Tuple[str, ...]]:
        """문맥을 고려하여 부수적 언급(주식회사, 단순 환율/분양/출사/노트북)은 예외 처리하고 실제 비대상 탐색"""
        combined = f"{title or ''} {snippet or ''}".strip()
        if not combined:
            return None, ()

        # --- Finance Contextual Check ---
        finance_ev = []
        # 주식: '주식회사'는 제외
        sub_no_corp = re.sub(r"주식회사\s*\S*", "", combined)
        if re.search(r"주식\s*(?:투자|시장|매수|매도|계좌|종목|전망|포트폴리오)", sub_no_corp, flags=re.IGNORECASE):
            finance_ev.append("주식투자")
        elif not detected_pos_cat and re.search(r"\b주식\b", sub_no_corp):
            finance_ev.append("주식")

        for p in (r"ETF", r"배당(?:주)?", r"코인", r"비트코인", r"가상화폐", r"증시", r"재테크", r"포트폴리오", r"지원금", r"환급금", r"대출\s*금리"):
            m = re.search(p, combined, flags=re.IGNORECASE)
            if m:
                finance_ev.append(m.group(0))

        # 분양: 문맥 예외 (아파트/청약/입주/오피스텔 분양 또는 분양가일 때만 block)
        if re.search(r"(?:아파트|청약|입주|오피스텔|상가|부동산)\s*분양|분양가|분양\s*일정", combined):
            finance_ev.append("부동산분양")
        elif not detected_pos_cat and re.search(r"분양", combined):
            finance_ev.append("분양")

        # 환율: 문맥 예외 (투자/외환/달러전망일 때만 block, 여행/면세/구매 시 단순 환율 언급은 허용)
        if re.search(r"환율\s*(?:투자|외환|전망|급등|하락|달러\s*전망|증시)", combined):
            finance_ev.append("환율투자")
        elif not detected_pos_cat and re.search(r"환율", combined):
            finance_ev.append("환율")

        if finance_ev:
            return "finance", tuple(finance_ev)

        # --- Camera Contextual Check ---
        camera_ev = []
        # 출사: 문맥 예외 (렌즈, 바디, 미러리스, DSLR 등 기기 키워드가 동반될 때만 block)
        if re.search(r"출사\s*(?:용|후기|사진)?\s*(?:렌즈|미러리스|카메라\s*바디|DSLR|망원|조리개)", combined, flags=re.IGNORECASE):
            camera_ev.append("출사장비")
        elif re.search(r"(?:렌즈|미러리스|카메라\s*바디|DSLR|망원|조리개).{0,20}출사", combined, flags=re.IGNORECASE):
            camera_ev.append("출사장비")
        elif not detected_pos_cat and re.search(r"출사", combined):
            camera_ev.append("출사")

        for p in (
            r"미러리스\s*카메라", r"DSLR", r"단렌즈", r"망원\s*렌즈",
            r"소니\s*(?:A\d|알파|FE\s*\d)", r"캐논\s*(?:EOS|R\d)", r"니콘\s*(?:Z\d|NIKKOR)",
            r"후지필름\s*(?:X-|GFX)", r"탐론\s*\d", r"시그마\s*\d", r"카메라\s*바디",
        ):
            m = re.search(p, combined, flags=re.IGNORECASE)
            if m:
                camera_ev.append(m.group(0))

        if camera_ev:
            return "camera", tuple(camera_ev)

        # --- Tech Contextual Check ---
        tech_ev = []
        # 노트북: '노트북으로 작업하기 좋은 카페'는 허용
        if re.search(r"노트북\s*(?:스펙|성능|비교|추천|리뷰|배터리|탑재)", combined, flags=re.IGNORECASE):
            tech_ev.append("노트북스펙")
        elif not detected_pos_cat and re.search(r"노트북", combined, flags=re.IGNORECASE):
            tech_ev.append("노트북")

        for p in (
            r"스마트폰\s*(?:스펙|비교)", r"아이폰\s*\d+", r"갤럭시\s*(?:S|Z|A)\d+",
            r"맥북\s*(?:프로|에어)", r"아이패드", r"CPU", r"GPU", r"그래픽카드", r"조립\s*PC",
            r"모니터\s*(?:리뷰|스펙|성능)", r"이어폰\s*(?:리뷰|스펙|성능)", r"헤드폰\s*(?:리뷰|스펙|성능)",
            r"벤치마크",
        ):
            m = re.search(p, combined, flags=re.IGNORECASE)
            if m:
                tech_ev.append(m.group(0))

        if tech_ev:
            return "tech", tuple(tech_ev)

        return None, ()

    @classmethod
    def _has_weak_lifestyle_evidence(cls, text: str, expected_category: str) -> tuple[bool, tuple[str, ...]]:
        patterns = {
            "FOOD": [r"맛집", r"식당", r"음식", r"메뉴", r"밥", r"요리", r"고기", r"국", r"찌개", r"술", r"한끼", r"배달", r"포장", r"맛있", r"먹었", r"먹방", r"푸드", r"레시피"],
            "CAFE": [r"카페", r"커피", r"디저트", r"베이커리", r"빵", r"음료", r"티", r"라떼", r"원두", r"공간", r"인테리어", r"바리스타", r"빙수"],
            "PARENTING": [r"아이", r"육아", r"아기", r"딸", r"아들", r"맘", r"키즈", r"유아", r"돌", r"어린이", r"유치원", r"출산", r"임신"],
            "LIVING": [r"살림", r"청소", r"정리", r"인테리어", r"가구", r"소품", r"집", r"꾸미기", r"주방", r"욕실", r"다이소", r"자취"],
            "TRAVEL": [r"여행", r"나들이", r"숙소", r"호텔", r"펜션", r"바다", r"산", r"드라이브", r"코스", r"명소", r"관광", r"휴가", r"국내", r"해외"],
            "LIFESTYLE": [r"일상", r"기록", r"하루", r"후기", r"추천", r"주말", r"선물", r"체험", r"소소", r"리뷰", r"공간", r"동네", r"방문"]
        }
        cand = patterns.get(expected_category, patterns["LIFESTYLE"])
        found = []
        for p in cand:
            m = re.search(p, text)
            if m:
                found.append(m.group(0))
        return (len(found) > 0, tuple(found))

    @classmethod
    def evaluate(
        cls,
        title: str,
        snippet: str = "",
        *,
        stage: str = "card",
        expected_category: Optional[str] = None
    ) -> TopicDecision:
        # 1. Title strong negative check
        title_neg_cat, title_neg_ev = cls._detect_title_strong_exclusion(title)
        if title_neg_cat:
            return TopicDecision(
                allowed=False,
                blocked_category=title_neg_cat,
                negative_evidence=title_neg_ev,
                reason_code="title_strong_negative",
                stage=stage
            )

        # 2. Positive category detection
        pos_cat, pos_ev = cls._detect_positive_category(title, snippet)

        # 3. Contextual negative detection with positive category awareness
        ctx_neg_cat, ctx_neg_ev = cls._detect_contextual_negatives(title, snippet, pos_cat)

        # 4. If positive category detected in TARGET_DISCOVERY_CATEGORIES:
        if pos_cat in TARGET_DISCOVERY_CATEGORIES:
            if ctx_neg_cat:
                return TopicDecision(
                    allowed=False,
                    detected_category=pos_cat,
                    blocked_category=ctx_neg_cat,
                    positive_evidence=pos_ev,
                    negative_evidence=ctx_neg_ev,
                    reason_code="body_strong_negative",
                    stage=stage
                )
            return TopicDecision(
                allowed=True,
                detected_category=pos_cat,
                confidence="high",
                positive_evidence=pos_ev,
                reason_code="positive_category_match",
                stage=stage
            )

        # 5. If expected_category provided (e.g. from QuerySpec) and not blocked by contextual negative:
        if expected_category and expected_category in TARGET_DISCOVERY_CATEGORIES:
            if ctx_neg_cat:
                return TopicDecision(
                    allowed=False,
                    detected_category=expected_category,
                    blocked_category=ctx_neg_cat,
                    negative_evidence=ctx_neg_ev,
                    reason_code="expected_category_blocked",
                    stage=stage
                )

            # expected_category여도 최소한의 라이프스타일/카테고리 약한 긍정 단서가 있어야 카드 단계 통과
            has_weak, weak_ev = cls._has_weak_lifestyle_evidence(f"{title} {snippet}", expected_category)
            if has_weak:
                return TopicDecision(
                    allowed=True,
                    detected_category=expected_category,
                    confidence="medium",
                    positive_evidence=weak_ev,
                    reason_code="expected_category_match",
                    stage=stage
                )
            else:
                return TopicDecision(
                    allowed=False,
                    detected_category="UNKNOWN",
                    reason_code="not_target_category",
                    stage=stage
                )

        # 6. If not matched to any target lifestyle category:
        if ctx_neg_cat:
            return TopicDecision(
                allowed=False,
                blocked_category=ctx_neg_cat,
                negative_evidence=ctx_neg_ev,
                reason_code="blocked_negative",
                stage=stage
            )

        # Non-target / Unknown (Recommendation & discovery skips non-lifestyle topics)
        return TopicDecision(
            allowed=False,
            detected_category="UNKNOWN",
            confidence="low",
            reason_code="not_target_category",
            stage=stage
        )

    @classmethod
    def is_allowed(
        cls,
        title: str,
        snippet: str = "",
        expected_category: Optional[str] = None
    ) -> Tuple[bool, str]:
        decision = cls.evaluate(title, snippet, stage="card", expected_category=expected_category)
        if decision.allowed:
            return True, f"allowed_{decision.detected_category or 'target'}"
        if decision.blocked_category:
            return False, f"blocked_{decision.blocked_category}: {', '.join(decision.negative_evidence)}"
        return False, f"skip_{decision.reason_code}"
