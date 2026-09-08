import os
import json
import math
import random
import tempfile
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from src.logger import logger

SAMPLING_HISTORY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "sampling_history.json"))


class SamplingHistoryManager:
    """
    캠페인 단위 랜덤 댓글 선정 결과의 영속성 및 멱등성을 보장하는 관리자:
    - 선정 단위: campaign_id + account_id + post_key
    - 동일 캠페인 재실행 시 기존 선정 결과를 정확히 재사용
    - 엄격한 확률 경계 검증: 0.0=미선정, 1.0=100%선정, roll < chance 비교
    - 대기시간 난수와 분리된 독립 RNG 스트림 사용
    """

    def __init__(self, file_path: str = SAMPLING_HISTORY_PATH):
        self.file_path = file_path
        self._records: Dict[str, Dict[str, Any]] = {}
        self._rng = random.Random()
        self.load()

    def load(self):
        if not os.path.exists(self.file_path):
            self._records = {}
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._records = data.get("records", {})
        except Exception as e:
            logger.log(f"[SAMPLING] 샘플링 이력 파일 로드 중 예외: {e}", "WARNING")
            self._records = {}

    def save(self):
        target_dir = os.path.dirname(self.file_path)
        os.makedirs(target_dir, exist_ok=True)

        payload = {
            "schema_version": 1,
            "updated_at": datetime.now().isoformat(),
            "records": self._records
        }

        temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="sampling_", suffix=".tmp")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.file_path)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            logger.log(f"[SAMPLING] 샘플링 이력 원자적 저장 실패: {e}", "WARNING")

    @staticmethod
    def validate_chance(chance: Any) -> float:
        """확률값을 0.0 ~ 1.0 범위로 엄격하게 검증 및 정규화"""
        try:
            val = float(chance)
            if math.isnan(val) or math.isinf(val):
                return 0.0
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.0

    def get_or_create_sample(
        self,
        campaign_id: str,
        account_id: str,
        post_key: str,
        chance: float,
        seed: Optional[int] = None,
    ) -> Tuple[bool, float]:
        """
        캠페인·계정·게시글 식별자에 대해 선정 여부를 결정하거나 기존 결정을 재사용합니다.
        - chance: 0.0 ~ 1.0
        - 반환: (is_selected, roll)
        """
        valid_chance = self.validate_chance(chance)
        record_key = f"{campaign_id}:{account_id}:{post_key}"

        # 1. 기존 선정 결과가 있으면 소급 변경 없이 즉시 재사용
        if record_key in self._records:
            existing = self._records[record_key]
            return existing["selected"], existing["roll"]

        # 2. 신규 추첨 (독립 RNG 사용)
        local_rng = random.Random(seed) if seed is not None else self._rng
        roll = local_rng.random()

        if valid_chance <= 0.0:
            selected = False
            roll = 0.5  # Deterministic representation for 0%
        elif valid_chance >= 1.0:
            selected = True
            roll = 0.0  # Deterministic representation for 100%
        else:
            selected = (roll < valid_chance)

        record = {
            "campaign_id": campaign_id,
            "account_id": account_id,
            "post_key": post_key,
            "chance": valid_chance,
            "roll": roll,
            "selected": selected,
            "policy_version": "v1.0",
            "created_at": datetime.now().isoformat()
        }

        self._records[record_key] = record
        self.save()

        logger.log(
            f"[SAMPLING] post={post_key} campaign={campaign_id} "
            f"chance={valid_chance*100:.1f}% roll={roll:.4f} selected={selected}"
        )
        return selected, roll
