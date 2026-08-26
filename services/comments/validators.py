import re
from typing import Tuple, List, Optional
from services.comments.intents import CommentCandidate


class PositiveSafetyValidator:
    """
    댓글의 긍정성, 안전성, 진정성을 검증하고 부정적/평가적/위조 경험을 원천 차단하는 필터
    """

    BANNED_NEGATIVE = [
        "생각보다", "의외로", "그나마", "나쁘지", "무난하", "아쉽", "그래도",
        "호불호", "취향 탈", "별로", "애매", "부족하", "실망"
    ]

    BANNED_JUDGMENT = [
        "저라면", "제가 보기에는", "개인적으로는", "더 나은", "이쪽이 낫", "저쪽보다", "비교해보면"
    ]

    BANNED_MACRO = [
        "유익한 정보", "잘 보고 갑니다", "작성자님", "인상적입니다", "도움이 되었습니다",
        "관점이", "깔끔하게 잘 정리", "포스팅 잘 읽었습니다", "좋은 정보 감사"
    ]

    BANNED_FAKE_EXPERIENCES = [
        "저도 가봤", "저도 먹어봤", "저도 써봤", "저도 구매했", "저도 이용해봤",
        "저희 아이도", "우리 강아지도", "우리 고양이도", "저희 집도", "저희 가족도", "저도 예전에"
    ]

    BANNED_BODY_EVALUATIONS = [
        "얼굴이 작", "살이 빠", "몸매", "피부가 하", "나이 들어", "어려 보여", "몸 좋아"
    ]

    BANNED_PRODUCT_ENDORSEMENTS = [
        "믿고 사도", "꼭 사야겠", "구매각", "효과 확실", "무조건 사야"
    ]

    BANNED_FINANCE_ENDORSEMENTS = [
        "매수해도", "이 종목 좋아", "전망이 맞는", "수익 보장", "무조건 오를"
    ]

    BANNED_QUESTION_INTRUSIONS = [
        "어디 사세요", "몇 살", "직업이", "자녀", "얼마 버", "수익 얼마"
    ]

    @classmethod
    def validate_candidate(cls, candidate: CommentCandidate) -> Tuple[bool, Optional[str]]:
        """후보 텍스트를 검증하여 통과 여부와 거부 사유 반환"""
        text = candidate.body

        # 1. 부정적 어휘 검사
        for phrase in cls.BANNED_NEGATIVE:
            if phrase in text:
                return False, f"banned_negative: '{phrase}'"

        # 2. 상대 선택 평가/비교 검사
        for phrase in cls.BANNED_JUDGMENT:
            if phrase in text:
                return False, f"banned_judgment: '{phrase}'"

        # 3. 상투적 매크로 문구 검사
        for phrase in cls.BANNED_MACRO:
            if phrase in text:
                return False, f"banned_macro: '{phrase}'"

        # 4. 과거 경험 위조 검사
        for phrase in cls.BANNED_FAKE_EXPERIENCES:
            if phrase in text:
                return False, f"banned_fake_experience: '{phrase}'"

        # 5. 신체/외모 평가 검사
        for phrase in cls.BANNED_BODY_EVALUATIONS:
            if phrase in text:
                return False, f"banned_body_evaluation: '{phrase}'"

        # 6. 상업적 제품 구매/효과 보증 검사
        for phrase in cls.BANNED_PRODUCT_ENDORSEMENTS:
            if phrase in text:
                return False, f"banned_product_endorsement: '{phrase}'"

        # 7. 금융/투자 보증 검사
        for phrase in cls.BANNED_FINANCE_ENDORSEMENTS:
            if phrase in text:
                return False, f"banned_finance_endorsement: '{phrase}'"

        # 8. 사적 질문 침해 검사
        for phrase in cls.BANNED_QUESTION_INTRUSIONS:
            if phrase in text:
                return False, f"banned_private_question: '{phrase}'"

        # 9. 길이 적합성 검사 (12자 이상 100자 이하, 101자 이상 거부)
        if len(text) < 12 or len(text) > 100:
            return False, f"length_out_of_bounds: {len(text)}자 (허용: 12~100자)"

        return True, None
