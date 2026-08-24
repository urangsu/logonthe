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
    "comment_template": "{좋은|유익한|멋진} 포스팅 잘 읽었습니다!",
    "fixed_suffix": "오늘도 좋은 하루 보내세요 :)",
    "secret_comment": False,
    "browser_mode": "persistent",
    "direct_urls": []
}


def migrate_config_v1_to_v2(old_data: Dict[str, Any]) -> Dict[str, Any]:
    """기존 v1 config 구조를 v2 schema로 안전하게 변환"""
    cfg = DEFAULT_CONFIG_V2.copy()
    
    # 댓글 템플릿 마이그레이션
    if "comment_template" in old_data:
        cfg["comment_template"] = old_data["comment_template"]
    if "secret_comment" in old_data:
        cfg["secret_comment"] = bool(old_data["secret_comment"])
    if "browser_mode" in old_data:
        cfg["browser_mode"] = old_data["browser_mode"]
    
    # max_pages(페이지당 약 10개) -> max_feed_items 변환
    if "max_pages" in old_data:
        try:
            cfg["max_feed_items"] = max(5, int(old_data["max_pages"]) * 10)
        except Exception:
            cfg["max_feed_items"] = 20

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
