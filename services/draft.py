import re
import random
from app.models import FeedSourceType


class DraftService:
    @staticmethod
    def parse_spintax(text: str) -> str:
        """{A|B|C} 형태의 Spintax 문법을 무작위 선택하여 단일 문자열로 변환"""
        pattern = re.compile(r"\{([^{}]+)\}")
        while True:
            match = pattern.search(text)
            if not match:
                break
            choices = match.group(1).split("|")
            chosen = random.choice(choices)
            text = text[:match.start()] + chosen + text[match.end():]
        return text

    @classmethod
    def resolve_suffix(cls, source: FeedSourceType, config) -> str:
        """피드 소스 종류(이웃/추천/직접)에 따른 전용 꼬리말 반환"""
        if source == FeedSourceType.RECOMMENDATION:
            if config.get("recommendation_suffix_enabled", True):
                return config.get("recommendation_suffix", "시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)").strip()
            return ""

        # 이웃 새글 및 직접 입력 피드
        return config.get("general_suffix", config.get("fixed_suffix", "오늘도 좋은 하루 보내세요 :)")).strip()

    @classmethod
    def compose_body_and_suffix(cls, body: str, suffix: str = "") -> str:
        """본문과 꼬리말을 자연스럽게 결합 (중복 방지)"""
        b_clean = body.strip()
        s_clean = suffix.strip()

        if not s_clean:
            return b_clean
        if not b_clean:
            return s_clean
        if s_clean in b_clean:
            return b_clean

        return f"{b_clean}\n\n{s_clean}"

    @classmethod
    def generate(cls, template: str, suffix: str = "") -> str:
        """Spintax 본문 생성 후 꼬리말 결합"""
        body = cls.parse_spintax(template.strip())
        return cls.compose_body_and_suffix(body, suffix)
