import re
import random
from typing import Optional
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

    @staticmethod
    def clean_ai_response(raw_text: str, expected_request_id: Optional[str] = None) -> Optional[str]:
        """
        Gemini 등 AI 웹 UI에서 추출된 응답 텍스트를 강력하게 정제 및 검증 (v5.0):
        1) Request ID 마커([[CMT:{id}]]...[[/CMT]]) 우선 추출 및 일치 여부 확인
        2) 'Gemini의 응답', 'Gemini\'s Response' 등 UI 툴바 헤더 텍스트 제거
        3) 마크다운 코드블록(```) 및 따옴표 제거
        4) 헤더만 복사되었거나 내용이 빈 경우 None 반환
        """
        if not raw_text or not raw_text.strip():
            return None

        text = raw_text.strip()

        # 1. Request ID 마커 우선 추출
        if expected_request_id:
            marker_pattern = rf"\[\[CMT:{re.escape(expected_request_id)}\]\](.*?)\[\[/CMT\]\]"
            match = re.search(marker_pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                text = match.group(1).strip()
            else:
                # 범용 마커 추출 폴백
                general_match = re.search(r"\[\[CMT:[a-zA-Z0-9_-]+\]\](.*?)\[\[/CMT\]\]", text, re.DOTALL | re.IGNORECASE)
                if general_match:
                    text = general_match.group(1).strip()

        # 남아있는 마커 태그 청소
        text = re.sub(r"\[\[/?CMT(?::[a-zA-Z0-9_-]+)?\]\]", "", text).strip()

        # 2. 마크다운 코드블록 제거
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("text") or text.startswith("markdown"):
                parts = text.split("\n", 1)
                text = parts[1] if len(parts) > 1 else ""

        # 3. Gemini UI 헤더/프리픽스 정규식 제거
        prefix_patterns = [
            r'^(?:Gemini의\s*응답|Gemini\'s\s*Response|Gemini\s*Response|Gemini|모델의\s*답변|답변\s*:|댓글\s*:|초안\s*:)\s*[\n:]*\s*',
            r'^(?:Here\s*is\s*the\s*comment|댓글\s*내용)\s*[\n:]*\s*'
        ]
        for pat in prefix_patterns:
            text = re.sub(pat, '', text, flags=re.IGNORECASE).strip()

        # 4. 독립 라인으로 들어간 UI 단어들 제거 (복사, 공유 등)
        ui_lines = {"복사", "copy", "공유", "share", "좋아요", "싫어요", "다시 시도", "regenerate"}
        lines = [line.strip() for line in text.splitlines() if line.strip() and line.strip().lower() not in ui_lines]
        text = "\n".join(lines).strip()

        # 5. 양쪽 감싼 따옴표 제거
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()

        # 6. '꼭', '반드시', '대박', '강추', '취저' 등 매크로/유행어 및 AI 요약 서두 자동 소거
        text = re.sub(r'^(?:전체적으로|무엇보다도|무엇보다|특히)\s*[,:]*\s*', '', text)
        text = re.sub(r'\b(?:꼭|반드시|무조건|대박|취저|취향저격|강추)\s+', '', text)
        text = re.sub(r'\s+(?:꼭|반드시|무조건|대박|취저|취향저격|강추)\s+', ' ', text)
        text = re.sub(r'꼭\s*(가보고|가봐야|먹어|방문|써보고|가야|참고|봐야|사야|뽑아)', r'\1', text)

        # 7. 모든 이모지 및 웃는 텍스트 이모티콘/문자 자동 소거
        text = re.sub(r'[\U00010000-\U0010ffff]', '', text)  # 4-byte Unicode emojis
        text = re.sub(r'[\u2600-\u27bf]', '', text)          # Miscellaneous symbols
        text = re.sub(r'[❤️💖💕✨😊😄😃😆☺️😋👍👏🎉]+', '', text)
        text = re.sub(r'[ㅎㅋ]{1,}', '', text)               # ㅎㅎ, ㅋㅋ
        text = re.sub(r'[\^]{2,}', '', text)                 # ^^
        text = re.sub(r'[:;][\)-DpP]+', '', text)            # :), :D, ;)
        text = re.sub(r'[ㅠㅜ]{1,}', '', text)               # ㅠㅠ, ㅜㅜ

        # 문장 부호 및 공백 정돈
        text = re.sub(r'\s+([.!?])', r'\1', text)
        text = re.sub(r'[!]{2,}', '!', text)
        text = re.sub(r'[?]{2,}', '?', text)
        text = re.sub(r'[.]{2,}', '.', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # 8. 검증 게이트: UI 헤더 단독이거나 너무 짧은 경우 무효화
        invalid_literals = {
            "gemini의 응답", "gemini's response", "gemini", "응답", "답변",
            "model-response", "response"
        }
        if text.lower() in invalid_literals or len(text) < 5:
            return None

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
