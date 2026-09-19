import uuid
from typing import Optional, List, Union, Dict, Any
from app.models import StylePlan
from services.comments.community_rhythm import CommunityRhythmPreset, PresetLike


class AIPromptBuilder:
    """
    실제 수집 자료 및 사용자 수정본 데이터 기반의 블로그 댓글 초안 프롬프트 빌더 (v3).
    - 5~6개 핵심 규칙 중심으로 간결화하고, 중복 부정 제약을 제거.
    - 어미 힌트 강제를 제거하여 문맥에 맞는 자연스러운 어미 선택 보장.
    - 핵심 소재와 본문 사실의 유기적 반응 유도 (단어 문자열 억지 삽입 금지).
    - 참고 예시 및 최근 댓글을 최대 2개로 제한하여 모방 부작용 억제.
    - 재작성 시 전체 프롬프트 반복 대신 간결한 델타 피드백 적용.
    """

    PROMPT_VERSION = "2.1.0-personalized"

    REPRESENTATIVE_EXAMPLES = [
        '- "스프랑 밥 무한리필이라니 경양식 돈까스 먹을 때 최고네요~"',
        '- "텐동 튀김 비쥬얼 진짜 예술이네요~"',
        '- "올레시장 맛있는거 진짜 많죠 넘 좋네요~"',
        '- "웨이팅 1시간 반이라니 인기 진짜 많은 곳인가 봐요"',
        '- "양꼬치보다 양갈비살이 더 맛있다니 의외네요~"',
    ]

    @classmethod
    def build(
        cls,
        title: str,
        excerpt: str = "",
        style: str = "warm_short",
        preset: PresetLike = CommunityRhythmPreset.THOUGHTFUL,
        request_id: Optional[str] = None,
        content_focus: str = "GENERAL",
        verified_anchors: Optional[List[str]] = None,
        secondary_anchors: Optional[List[str]] = None,
        style_plan: Optional[StylePlan] = None,
        recent_comments: Optional[List[str]] = None,
        recent_repeats: Optional[str] = None,
        rewrite_feedback: Optional[str] = None,
        corpus_examples: Optional[List[str]] = None,
        style_profile: Optional[Any] = None,
        corpus_stats: Optional[Dict[str, Any]] = None,
    ) -> str:
        title_s = title.strip() if title else "(제목 없음)"
        excerpt_s = excerpt.strip() if excerpt else ""
        preset_str = str(
            preset.value if isinstance(preset, CommunityRhythmPreset) else preset
        ).lower()
        core_anchors_str = ", ".join(verified_anchors) if verified_anchors else "없음"
        sec_anchors_str = ", ".join(secondary_anchors) if secondary_anchors else "없음"

        # -------------------------------------------------------------
        # 1. 1회 재작성 피드백이 있는 경우: 전체 프롬프트 반복 없이 간결한 델타 프롬프트 출력
        # -------------------------------------------------------------
        if rewrite_feedback:
            repeat_line = f"\n- 최근 중복 표현 방지: {recent_repeats}" if recent_repeats else ""
            anchor_guide = ""
            if verified_anchors:
                anchor_guide = f"\n- 핵심 소재({core_anchors_str})가 있으면 그 소재 또는 그와 직접 연결된 본문의 구체적 사실 하나에 가볍게 반응한다. 단어 자체를 억지로 문장에 넣을 필요는 없다."

            return f"""너는 네이버 블로그 이웃 글에 자연스러운 한 줄 댓글을 작성한다

[수정 요청 (1회 재작성)]
{rewrite_feedback.strip()}{repeat_line}

[문체 및 사실성 핵심 지침]
- 20~45자 내외의 자연스러운 1문장 존댓말 댓글로 작성한다.
- 설명하거나 요약하지 말고 바로 반응한다.
- ~네요, ~어요, ~요 등 어미는 문맥에 맞게 자유롭게 쓴다.
- 현재 본문에서 확인되는 내용만 사용하며, 거짓 경험이나 없는 맛·식감은 지어내지 않는다.{anchor_guide}

[현재 글]
제목: {title_s}
본문:
{excerpt_s if excerpt_s else '(본문 없음)'}

[출력]
댓글 초안 한 개만 출력한다. 설명, 분석, 따옴표, 후보 번호는 붙이지 않는다."""

        # -------------------------------------------------------------
        # 2. 신규 생성: Prompt v3 조립
        # -------------------------------------------------------------
        length_desc = "30~80자 내외의 1~2문장" if preset_str == "thoughtful" else "20~45자 내외의 간결한 1문장"
        period_rule = "- 마침표는 자연스럽게 써도 됨" if preset_str == "thoughtful" else "- 마침표는 쓰지 않음 (., 。 금지)"

        style_notes = [
            f"- 보통 {length_desc}이면 충분하다 (설명하거나 요약하지 말고 바로 반응)",
            "- ~네요, ~어요, ~요 등 어미는 문맥에 맞게 자유롭게 쓴다 (억지로 특정 어미를 맞추지 않음)",
            "- 물결(~) 또는 느낌표(!)는 어울리면 1회 정도 사용할 수 있다",
            "- 억지로 방문하겠다는 말이나 칭찬, 습관적 마무리(좋겠어요, 참고해야겠어요, 기억해둬야겠네요, 한번 가봐야겠어요, 도움이 될 것 같아요 등)를 덧붙이지 않는다",
            period_rule,
        ]

        if style_profile and getattr(style_profile, "total_samples", 0) > 0:
            endings = getattr(style_profile, "top_endings", [])
            endings_str = f", 선호 종결어미({', '.join(endings[:3])})" if endings else ""
            style_notes.append(f"- 개인화 문체 기준(v{style_profile.version}): 평균 {int(style_profile.avg_length)}자 내외{endings_str}")
            tendency = getattr(style_profile, "user_edit_tendency", [])
            if tendency:
                style_notes.append(f"- 사용자 수정 경향 반영: {', '.join(tendency)}")

        if style_plan:
            # 어미를 강제하지 않고 다양성 분석/성향 참고용 메타데이터로 반영
            reaction_hint = getattr(style_plan, "reaction_type", "")
            ending_meta = getattr(style_plan, "ending_family", "")
            meta_parts = []
            if reaction_hint:
                meta_parts.append(f"반응 톤: {reaction_hint}")
            if ending_meta:
                meta_parts.append(f"분석 어미군: {ending_meta}")
            if meta_parts:
                style_notes.append(f"- 스타일 분석 참고: {', '.join(meta_parts)} (어미는 문맥에 맞춰 완전 자율 선택)")

        style_criteria = "\n".join(style_notes)

        # 2. 동적/대표 예시: 최대 2개로 제한
        examples_to_use = corpus_examples[:2] if (corpus_examples and len(corpus_examples) >= 1) else cls.REPRESENTATIVE_EXAMPLES[:2]
        formatted_examples = []
        for ex in examples_to_use:
            cleaned_ex = ex.strip().lstrip("- ").strip('"\'')
            formatted_examples.append(f'- "{cleaned_ex}"')
        examples_str = "\n".join(formatted_examples)

        # 3. 최근 댓글 구조 중복 방지: 최대 2개로 제한
        recent_section = ""
        if recent_repeats:
            recent_section = f"""[최근 반복 표현/구조]
{recent_repeats}
위 표현이나 구조를 억지로 다른 단어로 치환하지 말고, 본문 안에서 다른 반응 지점이나 자연스러운 문장 구조를 선택한다.

"""
        elif recent_comments:
            recent_sample = recent_comments[-2:]
            recent_str = " / ".join(f'"{c.strip()}"' for c in recent_sample if c.strip())
            if recent_str:
                recent_section = f"""[최근 댓글과 구조 중복 방지]
최근 등록된 댓글: {recent_str}
위 댓글들과 동일한 문장 구조나 묘사 템플릿이 연속되지 않도록 본문의 다른 구체적인 사실에 반응한다.

"""

        # 4. 음식 글 우선 안내 (웨이팅, 양, 주문 팁, 가격, 비교 허용 & 식감/맛 날조 금지)
        food_section = ""
        if content_focus in ("FOOD_RESTAURANT", "CAFE_DESSERT", "FOOD_PRODUCT"):
            food_section = """[음식 글 우선 규칙]
- 맛이나 식감은 본문에서 직접 확인된 경우에만 말하고, 본문에 없는 맛/식감을 지어내지 마라.
- 메뉴명이나 비주얼뿐 아니라 웨이팅, 양, 주문 팁, 가격, 두 메뉴 간의 비교 등 본문에서 실제로 나온 디테일에 자연스럽게 반응해도 된다.

"""

        # 5. 핵심 소재 섹션
        anchor_section = ""
        if verified_anchors or secondary_anchors:
            anchor_section = f"""[핵심 소재]
음식/핵심: {core_anchors_str}
보조: {sec_anchors_str}
핵심 소재가 있으면 그 소재 또는 그 소재와 직접 관련된 본문 사실에 반응한다.
단어 자체를 억지로 문장에 끼워 넣지는 않는다.

"""

        prompt = f"""너는 네이버 블로그 이웃 글에 자연스러운 한 줄 댓글을 작성한다

[목표]
친한 블로그 이웃 글을 읽다가 눈에 들어온 구체적인 내용 하나에
가볍게 반응하는 자연스러운 존댓말 댓글을 쓴다

[말투 기준]
{style_criteria}

[작성 방식]
- 현재 글에서 눈에 들어온 구체적인 내용 하나에 가볍게 반응한다.
- 설명하거나 요약하지 말고 읽다가 바로 남기는 댓글처럼 작성한다.
- 한 가지 반응으로 충분하면 끝내며, 길이를 채우려고 억지 칭찬이나 방문 의향, 습관적 마무리를 덧붙이지 않는다.
- 어미는 문맥에 맞게 자율적으로 선택한다.

[사실 기준]
- 현재 본문에서 확인되는 내용에만 반응한다.
- 방문·구매·섭취·사용 경험을 만들어내지 않는다 (내가 가봤거나 먹어본 것처럼 말하지 않음).
- 본문에 없는 맛, 식감, 효과를 지어내지 않는다.
- 반응할 근거가 전혀 없으면 NEED_MORE_CONTEXT만 출력한다.

{food_section}{anchor_section}[말투 참고 예시]
(주의: 아래 예시는 말투와 호흡만 참고하는 자료입니다. 예시의 음식·장소·경험은 현재 글의 사실 근거로 사용하지 않는다.)
{examples_str}

{recent_section}[데이터]
[현재 글]
제목: {title_s}
본문:
{excerpt_s if excerpt_s else '(본문 없음)'}

[출력]
댓글 초안 한 개만 출력한다. 설명, 분석, 따옴표, 후보 번호는 붙이지 않는다."""

        return prompt
