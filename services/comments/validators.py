import re
from typing import Tuple, List, Optional
from services.comments.intents import CommentCandidate
from services.comments.community_rhythm import (
    COMMENT_POLICIES,
    CommunityRhythmPreset,
    FinalQualityGate,
    FinalQualityResult,
)


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
        "저희 아이도", "우리 강아지도", "우리 고양이도", "저희 집도", "저희 가족도", "저도 예전에",
        "더라구요", "더군요"
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

    BANNED_KKOK_MACRO = [
        "꼭 가보고", "꼭 가봐야", "꼭 먹어", "꼭 들러", "꼭 써보고", "꼭 방문",
        "꼭 가볼", "꼭 봐야", "꼭 참고", "꼭 찾아", "꼭 사야", "꼭 뽑아", "반드시", "무조건"
    ]

    BANNED_AI_SUMMARY = [
        "전체적으로", "무엇보다", "특히 인상", "인상적이네요", "인상적입니다",
        "알찬 정보", "유용한 정보", "좋은 정보", "정리가 잘 되어", "한눈에",
        "구성이 돋보이", "매력적이네요", "눈길을 끄네요", "완성도가"
    ]

    BANNED_EXAGGERATIONS = [
        "취향저격", "취저", "못 참죠", "못참죠", "방문각", "구매각", "강추", "대박"
    ]

    BANNED_EMOTICONS = [
        ":)", ":D", "^^", "ㅎㅎ", "ㅋㅋ", "☺️", "😊", "😄", "😆", "❤️", "💕", "ㅠㅠ", "ㅜㅜ"
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

        # 9. '꼭' / '반드시' 매크로 어휘 검사
        for phrase in cls.BANNED_KKOK_MACRO:
            if phrase in text:
                return False, f"banned_kkok_phrase: '{phrase}'"

        # 10. AI 요약/보고서 어투 검사
        for phrase in cls.BANNED_AI_SUMMARY:
            if phrase in text:
                return False, f"banned_ai_summary: '{phrase}'"

        # 11. 과장/인터넷 유행어 검사
        for phrase in cls.BANNED_EXAGGERATIONS:
            if phrase in text:
                return False, f"banned_exaggeration: '{phrase}'"

        # 12. 이모티콘 및 웃는 문자 금지 검사
        for emo in cls.BANNED_EMOTICONS:
            if emo in text:
                return False, f"banned_emoticon: '{emo}'"

        # 13. 길이 적합성 검사 (12자 이상 100자 이하, 101자 이상 거부)
        if len(text) < 12 or len(text) > 100:
            return False, f"length_out_of_bounds: {len(text)}자 (허용: 12~100자)"

        # V13.1 shared final-text policy. Keep the historic checks above in
        # place so this public API retains its existing reason taxonomy and
        # behavior, while every candidate also receives the new hard bans.
        quality = FinalQualityGate.validate_candidate_text(text, legacy=True)
        if not quality.valid:
            legacy_codes = {
                "fake_experience": "banned_fake_experience",
                "absolute_or_pressure": "banned_kkok_phrase",
                "laughter_or_emoticon": "banned_emoticon",
                "emoji": "banned_emoticon",
            }
            code = legacy_codes.get(quality.code, quality.code)
            matched = f": '{quality.matched}'" if quality.matched else ""
            return False, f"{code}{matched}"

        return True, None
