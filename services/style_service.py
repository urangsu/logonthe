import random
from typing import Optional, List, Dict
from app.models import StylePlan
from src.logger import logger


class StylePlanService:
    """
    글마다 1회 독립적으로 결정되는 StylePlan 생성 서비스:
    - 20대 일상 구어 톤 다양성 보장 (반응 유형, 표현 강도, 어미 경향, 강조 여부)
    - 최근 등록 댓글에 대한 과도한 어미/어구 반복 억제 (Repetition Penalty)
    - 기술적 재시도(NEED_MORE_CONTEXT, 에러 복구) 시에는 동일 StylePlan 유지
    """

    REACTION_TYPES = ["구체적 디테일 공감", "짧은 감상", "가벼운 관심"]
    LENGTH_BANDS = ["보통", "짧게"]
    INTENSITIES = ["살짝 유쾌", "담백"]
    ENDING_FAMILIES = ["~네요", "~겠어요", "~보여요"]
    EMPHASES = ["없음", "한 번"]

    @classmethod
    def select_style_plan(
        cls,
        post_key: str,
        recent_comments: Optional[List[str]] = None,
        preset: str = "community",
        seed: Optional[int] = None,
    ) -> StylePlan:
        # 1. 최근 등록 댓글의 어미 및 시작 문구 빈도 분석 (반복 억제 가중치 산출)
        ending_weights: Dict[str, float] = {"~네요": 1.0, "~겠어요": 1.0, "~보여요": 1.0}

        if recent_comments:
            recent_sample = recent_comments[-5:]
            ending_counts = {"~네요": 0, "~겠어요": 0, "~보여요": 0}
            for text in recent_sample:
                t = text.strip()
                if t.endswith("네요") or t.endswith("네요~") or t.endswith("네"):
                    ending_counts["~네요"] += 1
                elif t.endswith("겠어요") or t.endswith("겠네요") or t.endswith("겠어요~") or t.endswith("겠네"):
                    ending_counts["~겠어요"] += 1
                elif t.endswith("보여요") or t.endswith("보이네요") or t.endswith("보여요~") or t.endswith("보인다"):
                    ending_counts["~보여요"] += 1

            for family, count in ending_counts.items():
                if count >= 2:
                    ending_weights[family] = max(0.15, ending_weights[family] - 0.4 * count)

        # 독립 RNG: post_key 또는 지정 seed 기반으로 대기시간 난수와 분리
        rng_seed = seed if seed is not None else hash(f"style:{post_key}")
        rng = random.Random(rng_seed)

        chosen_ending = rng.choices(
            list(ending_weights.keys()),
            weights=list(ending_weights.values()),
            k=1
        )[0]
        chosen_reaction = rng.choice(cls.REACTION_TYPES)
        chosen_length = rng.choice(cls.LENGTH_BANDS)
        chosen_intensity = rng.choice(cls.INTENSITIES)
        chosen_emphasis = rng.choices(cls.EMPHASES, weights=[0.75, 0.25], k=1)[0]

        plan = StylePlan(
            reaction_type=chosen_reaction,
            length_band=chosen_length,
            intensity=chosen_intensity,
            ending_family=chosen_ending,
            emphasis=chosen_emphasis,
        )

        logger.log(
            f"[STYLE_PLAN] post={post_key} reaction={plan.reaction_type} "
            f"ending={plan.ending_family} intensity={plan.intensity} len={plan.length_band}"
        )
        return plan
