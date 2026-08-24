import os
import json
from typing import Dict, Any
from app.models import FeedSourceType

DEFAULT_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.json"))

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

    # AI Gemini Assistant
    "ai_clipboard_enabled": True,
    "ai_context_max_chars": 700,
    "ai_prompt_style": "warm_short",
    "append_fixed_suffix_to_ai": False,

    # Gemini Browser Mode: "existing_chrome_mac" (기존 Chrome 탭) 또는 "managed_playwright"
    "gemini_browser_mode": "existing_chrome_mac",
    "gemini_web_enabled": True,
    "gemini_mode": "new",
    "gemini_custom_url": "https://gemini.google.com/app/0a1545681329aa0a?hl=ko",
    "auto_apply_ai_comment": False
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
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.data: Dict[str, Any] = self.load()

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            self.save(DEFAULT_CONFIG_V2)
            return DEFAULT_CONFIG_V2.copy()

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            if loaded.get("schema_version", 1) < 2:
                migrated = migrate_config_v1_to_v2(loaded)
                self.save(migrated)
                return migrated

            merged = DEFAULT_CONFIG_V2.copy()
            merged.update(loaded)
            return merged
        except Exception:
            return DEFAULT_CONFIG_V2.copy()

    def save(self, data: Dict[str, Any]):
        self.data = data
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any):
        self.data[key] = value
        self.save(self.data)
