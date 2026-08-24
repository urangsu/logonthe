from typing import Optional
from app.models import FeedPost


class AIPromptBuilder:
    """
    네이버 블로그 게시글 맥락(제목 + 본문 일부)을 기반으로
    Gemini / ChatGPT 등에 복사해 넣을 고품질 댓글 생성용 프롬프트를 빌드합니다.
    """
    @classmethod
    def build(cls, title: str, excerpt: str = "", style: str = "natural") -> str:
        title_s = title.strip() if title else "(제목 없음)"
        excerpt_s = excerpt.strip() if excerpt else ""

        if excerpt_s:
            prompt = f"""아래 네이버 블로그 글에 달 댓글 초안을 하나 작성해줘.

목표:
글을 실제로 꼼꼼히 읽은 사람이 남긴 것처럼 자연스럽고 구체적인 댓글.

조건:
- 1~3문장 내외로 작성
- 글 제목이나 본문에 실제로 언급된 구체적인 포인트/단어를 최소 1개 자연스럽게 언급
- 과도한 칭찬이나 광고성/홍보성 표현 절대 금지
- "좋은 정보 감사합니다", "포스팅 잘 보고 갑니다"처럼 어느 글에나 붙일 수 있는 진부한 매크로 표현은 피할 것
- 억지 질문은 하지 않되, 실제로 궁금할 만한 내용이 있을 때만 자연스러운 짧은 질문 1개 허용
- 이모지는 0~1개만 절제하여 사용
- 작성자 닉네임을 어색하게 부르지 말 것
- 부가 설명 없이 오직 생성된 '댓글 본문'만 출력

제목:
{title_s}

본문 일부:
{excerpt_s}"""
        else:
            prompt = f"""아래 네이버 블로그 글에 달 댓글 초안을 하나 작성해줘.

목표:
글을 실제로 읽은 사람이 남긴 것처럼 자연스럽고 구체적인 댓글.

조건:
- 1~3문장 내외로 작성
- 본문이 없으므로 제목에 없는 사실이나 내용을 억지로 지어내지 말 것
- 과도한 칭찬이나 상투적인 표현("잘 보고 갑니다" 등) 피하기
- 부가 설명 없이 오직 생성된 '댓글 본문'만 출력

제목:
{title_s}"""

        return prompt
