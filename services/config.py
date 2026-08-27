import os
import json
import tempfile
import hashlib
import fcntl
from pathlib import Path
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
    "assistant_mode": True,
    "like_enabled": False,
    "comment_enabled": False,
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
    "gemini_web_enabled": False,
    "gemini_mode": "new",
    "gemini_custom_url": "https://gemini.google.com/app/0a1545681329aa0a?hl=ko",

    # User Learning & Engagement Audit (v7.0)
    "user_learning_record_enabled": True,
    "auto_prompt_learning_enabled": False,
    "auto_style_apply_enabled": False,
    "my_blog_id": "",
    "engagement_audit_recent_posts": 5
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


class ConfigConflictError(RuntimeError):
    """Another process edited the configuration; reload before saving."""


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

        self.config_path = os.path.abspath(self.config_path)
        self.data: Dict[str, Any] = self.load()

    def _atomic_save(self, data: Dict[str, Any]):
        """원자적(Atomic) 파일 저장 (임시 파일 생성 후 os.replace 교체)"""
        target_dir = os.path.dirname(self.config_path)
        os.makedirs(target_dir, exist_ok=True)

        temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="config_", suffix=".tmp")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
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
        path = Path(self.config_path)
        self._original = path.read_bytes() if path.exists() else None
        if self._original is None:
            return DEFAULT_CONFIG_V2.copy()
        try:
            loaded = json.loads(self._original)
            if not isinstance(loaded, dict):
                raise ValueError('config_must_be_object')
            if loaded.get("schema_version", 1) < 2:
                migrated = migrate_config_v1_to_v2(loaded)
                # Retain unknown/legacy settings; migration is in memory until explicit save.
                migrated = dict(loaded, **migrated)
                return migrated
            merged = DEFAULT_CONFIG_V2.copy()
            merged.update(loaded)
            return merged
        except (ValueError, TypeError) as e:
            raise ValueError('설정 파일을 읽을 수 없습니다. 원본은 변경하지 않았습니다: ' + self.config_path) from e

    def save(self, data: Dict[str, Any]):
        merged = DEFAULT_CONFIG_V2.copy()
        merged.update(self.data)
        merged.update(data)
        merged["schema_version"] = 2
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path) + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = path.read_bytes() if path.exists() else None
            if current != self._original:
                raise ConfigConflictError('다른 작업이 설정을 변경했습니다. 앱을 다시 열어 최신 설정을 확인하세요.')
            if current is not None:
                backup_dir = path.parent / 'config_backups'
                backup_dir.mkdir(exist_ok=True)
                backup = backup_dir / (path.name + '.' + hashlib.sha256(current).hexdigest()[:16] + '.bak')
                if not backup.exists():
                    with open(backup, 'xb') as out:
                        out.write(current)
                        out.flush()
                        os.fsync(out.fileno())
            self._atomic_save(merged)
            self._original = path.read_bytes()
        self.data = merged

    def update_many(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """부분 딕셔너리 안전 병합 및 원자적 저장"""
        self.save(values)
        return self.data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any):
        self.update_many({key: value})
