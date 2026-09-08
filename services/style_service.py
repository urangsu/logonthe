import random
import hashlib
from typing import Optional, List, Dict
from app.models import StylePlan
from src.logger import logger


class StylePlanService:
    """
    글마다 1회 독립적으로 결정되는 StylePlan 생성 서비스:
    - 20대 일상 구어 톤 다양성 보장 (반응 유형, 표현 강도, 어미 경향, 강조 여부)
    - 최근 등록 댓글에 대한 과도한 어미/어구 반복 억제 (Repetition Penalty)
    - 기술적 재시도(NEED_MORE_CONTEXT, 에러 복구) 시에는 동일 StylePlan 유지
    - SHA-256 기반 결정론적 시드로 프로세스 재시작 간 일관성 유지
    """

    REACTION_TYPES_COMMUNITY = ["구체적 디테일 공감", "짧은 감상", "가벼운 관심"]
    REACTION_TYPES_THOUGHTFUL = ["구체적 디테일 공감", "정중한 소감", "진중한 응원"]
    REACTION_TYPES_ENTHUSIASTIC = ["열렬한 감탄", "적극적 관심", "공감"]

    LENGTH_BANDS = ["보통", "짧게"]
    ENDING_FAMILIES = ["~네요", "~겠어요", "~보여요"]

    @classmethod
    def select_style_plan(
        cls,
        post_key: str,
        recent_comments: Optional[List[str]] = None,
        preset: str = "community",
        seed: Optional[int] = None,
    ) -> StylePlan:
        # 1. 최근 등록 댓글의 어미 빈도 분석 (더 긴 어미 계열을 먼저 검사하여 중복 오탐 방지)
        ending_weights: Dict[str, float] = {"~네요": 1.0, "~겠어요": 1.0, "~보여요": 1.0}

        if recent_comments:
            recent_sample = recent_comments[-5:]
            ending_counts = {"~네요": 0, "~겠어요": 0, "~보여요": 0}
            for text in recent_sample:
                t = text.strip()
                if any(t.endswith(sfx) for sfx in ("겠네요", "겠네요~", "겠어요", "겠어요~", "겠네", "겠네~")):
                    ending_counts["~겠어요"] += 1
                elif any(t.endswith(sfx) for sfx in ("보이네요", "보이네요~", "보여요", "보여요~", "보인다", "보이네")):
                    ending_counts["~보여요"] += 1
                elif any(t.endswith(sfx) for sfx in ("네요", "네요~", "네", "네~", "군요", "군요~")):
                    ending_counts["~네요"] += 1

            for family, count in ending_counts.items():
                if count >= 2:
                    ending_weights[family] = max(0.15, ending_weights[family] - 0.4 * count)

        # 결정론적 시드: SHA-256 기반으로 프로세스 재시작 간 불변 시드 생성
        if seed is not None:
            rng_seed = seed
        else:
            digest = hashlib.sha256(f"style:{post_key}".encode("utf-8")).hexdigest()
            rng_seed = int(digest[:16], 16)
        rng = random.Random(rng_seed)

        # 프리셋별 허용 강도 및 반응 튜닝
        if preset == "thoughtful":
            reaction_candidates = cls.REACTION_TYPES_THOUGHTFUL
            intensity_candidates = ["담백", "차분"]
            emphasis_weights = [0.95, 0.05]
        elif preset == "enthusiastic":
            reaction_candidates = cls.REACTION_TYPES_ENTHUSIASTIC
            intensity_candidates = ["살짝 유쾌", "열정적"]
            emphasis_weights = [0.5, 0.5]
        else:
            reaction_candidates = cls.REACTION_TYPES_COMMUNITY
            intensity_candidates = ["살짝 유쾌", "담백"]
            emphasis_weights = [0.75, 0.25]

        chosen_ending = rng.choices(
            list(ending_weights.keys()),
            weights=list(ending_weights.values()),
            k=1
        )[0]
        chosen_reaction = rng.choice(reaction_candidates)
        chosen_length = rng.choice(cls.LENGTH_BANDS)
        chosen_intensity = rng.choice(intensity_candidates)
        chosen_emphasis = rng.choices(["없음", "한 번"], weights=emphasis_weights, k=1)[0]

        plan = StylePlan(
            reaction_type=chosen_reaction,
            length_band=chosen_length,
            intensity=chosen_intensity,
            ending_family=chosen_ending,
            emphasis=chosen_emphasis,
        )

        logger.log(
            f"[STYLE_PLAN] post={post_key} preset={preset} reaction={plan.reaction_type} "
            f"ending={plan.ending_family} intensity={plan.intensity} len={plan.length_band}"
        )
        return plan
