import os
import json
import tempfile
from typing import Dict, Any, Optional
from app.models import FeedSourceType
from src.logger import logger

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME_CONFIG_PATH = os.path.join(WORKSPACE_DIR, "data", "config.json")
ROOT_CONFIG_PATH = os.path.join(WORKSPACE_DIR, "config.json")

DEFAULT_CONFIG_V2: Dict[str, Any] = {
    "schema_version": 2,
    "feed_source": FeedSourceType.NEIGHBOR.value,
    "max_feed_items": 20,
    "like_enabled": True,
    "comment_enabled": True,
    "comment_template": "{사진 분위기가 너무 좋네요|정말 좋아 보여요|보기만 해도 기분 좋아지는 글이네요} :)",
    "general_suffix": "오늘도 좋은 하루 보내세요 :)",
    "fixed_suffix": "오늘도 좋은 하루 보내세요 :)",
    "recommendation_suffix_enabled": True,
    "recommendation_suffix": "시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)",
    "secret_comment": False,
    "browser_mode": "persistent",
    "direct_urls": [],

    # Pacing (작업 간격 및 랜덤 휴지)
    "pacing_enabled": True,
    "action_delay_min": 1.0,
    "action_delay_max": 2.5,
    "next_post_delay_min": 2.0,
    "next_post_delay_max": 5.0,
    "random_pause_enabled": True,
    "random_pause_chance": 0.10,
    "random_pause_min": 8.0,
    "random_pause_max": 20.0,

    # Like Popularity Guard (공감수 999+ 및 일방문자 1만+ 가드)
    "like_popularity_guard_enabled": True,
    "like_count_skip_threshold": 999,
    "daily_visitor_guard_enabled": True,
    "daily_visitor_skip_threshold": 10000,
    "daily_visitor_unknown_policy": "skip_like",

    # AI Gemini & Human-Like Composer Assistant
    "ai_clipboard_enabled": True,
    "ai_context_max_chars": 700,
    "ai_prompt_style": "warm_short",
    "append_fixed_suffix_to_ai": False,

    # Gemini Browser Mode: "existing_chrome_mac" 또는 "managed_playwright"
    "gemini_browser_mode": "existing_chrome_mac",
    "gemini_web_enabled": True,
    "gemini_mode": "new",
    "gemini_custom_url": "https://gemini.google.com/app/0a1545681329aa0a?hl=ko"
}


def migrate_config_v1_to_v2(old_data: Dict[str, Any]) -> Dict[str, Any]:
    """기존 config 구조를 v2 schema로 안전하게 변환"""
    cfg = DEFAULT_CONFIG_V2.copy()

    for k, v in old_data.items():
        if k in cfg:
            cfg[k] = v

    if "max_pages" in old_data:
        try:
            cfg["max_feed_items"] = max(5, int(old_data["max_pages"]) * 10)
        except Exception:
            cfg["max_feed_items"] = 20

    if "fixed_suffix" in old_data and "general_suffix" not in old_data:
        cfg["general_suffix"] = old_data["fixed_suffix"]

    cfg["schema_version"] = 2
    return cfg


class ConfigService:
    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = config_path
        elif os.path.exists(RUNTIME_CONFIG_PATH):
            self.config_path = RUNTIME_CONFIG_PATH
        elif os.path.exists(ROOT_CONFIG_PATH):
            self.config_path = ROOT_CONFIG_PATH
        else:
            self.config_path = RUNTIME_CONFIG_PATH

        self.data: Dict[str, Any] = self.load()

    def _atomic_save(self, data: Dict[str, Any]):
        """원자적(Atomic) 파일 저장 (임시 파일 생성 후 os.replace 교체)"""
        target_dir = os.path.dirname(self.config_path)
        os.makedirs(target_dir, exist_ok=True)

        temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="config_", suffix=".tmp")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.config_path)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            logger.log(f"[CONFIG] 설정 원자적 저장 실패: {e}", "WARNING")
            raise

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            self._atomic_save(DEFAULT_CONFIG_V2)
            return DEFAULT_CONFIG_V2.copy()

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            if loaded.get("schema_version", 1) < 2:
                migrated = migrate_config_v1_to_v2(loaded)
                self._atomic_save(migrated)
                return migrated

            merged = DEFAULT_CONFIG_V2.copy()
            merged.update(loaded)
            return merged
        except Exception as e:
            logger.log(f"[CONFIG] 설정 로드 중 예외, 기본값 적용: {e}", "WARNING")
            return DEFAULT_CONFIG_V2.copy()

    def save(self, data: Dict[str, Any]):
        merged = DEFAULT_CONFIG_V2.copy()
        merged.update(self.data)
        merged.update(data)
        merged["schema_version"] = 2
        self._atomic_save(merged)
        self.data = merged

    def update_many(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """부분 딕셔너리 안전 병합 및 원자적 저장"""
        merged = DEFAULT_CONFIG_V2.copy()
        merged.update(self.data)
        merged.update(values)
        merged["schema_version"] = 2
        self._atomic_save(merged)
        self.data = merged
        return self.data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any):
        self.data[key] = value
        self.update_many({key: value})
