"""
Execution run policy module.
Provides deterministic, immutable run policies for general vs neighbor vs neighbor sweep modes,
and ensures user comment preferences are safely preserved across mode toggles and restarts.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
from app.models import FeedSourceType


class RunMode(str, Enum):
    NORMAL = "normal"
    NEIGHBOR_NORMAL = "neighbor_normal"
    NEIGHBOR_SWEEP = "neighbor_sweep"


@dataclass(frozen=True)
class RunPolicy:
    mode: RunMode
    source_type: FeedSourceType
    effective_like_enabled: bool
    effective_comment_enabled: bool
    neighbor_mutual_only: bool
    is_sweep_mode: bool
    sweep_stop_consecutive_liked: int = 4


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    if hasattr(config, "get") and callable(getattr(config, "get")):
        return config.get(key, default)
    if hasattr(config, "data") and isinstance(getattr(config, "data"), dict):
        return config.data.get(key, default)
    return default


def _get_cfg_dict(config_service_or_dict: Any) -> Dict[str, Any]:
    if isinstance(config_service_or_dict, dict):
        return config_service_or_dict
    if hasattr(config_service_or_dict, "data") and isinstance(getattr(config_service_or_dict, "data"), dict):
        return config_service_or_dict.data
    return {}


def resolve_run_policy(
    config: Any,
    source_type_override: Optional[FeedSourceType] = None,
    sweep_mode_override: Optional[bool] = None,
) -> RunPolicy:
    """
    Resolves an immutable RunPolicy from config and runtime overrides.
    """
    raw_source = _cfg_get(config, "feed_source_type", _cfg_get(config, "feed_source", "recommendation"))
    if source_type_override is not None:
        source_type = source_type_override
    elif isinstance(raw_source, FeedSourceType):
        source_type = raw_source
    else:
        try:
            source_type = FeedSourceType(str(raw_source).lower())
        except ValueError:
            source_type = FeedSourceType.RECOMMENDATION

    if sweep_mode_override is not None:
        is_sweep = bool(sweep_mode_override and source_type == FeedSourceType.NEIGHBOR)
    else:
        is_sweep = bool(_cfg_get(config, "neighbor_like_sweep_mode", False) and source_type == FeedSourceType.NEIGHBOR)

    configured_like = bool(_cfg_get(config, "like_enabled", True))
    configured_comment = bool(_cfg_get(config, "comment_enabled", True))
    mutual_only = bool(_cfg_get(config, "neighbor_mutual_only", True))

    if source_type == FeedSourceType.NEIGHBOR:
        if is_sweep:
            mode = RunMode.NEIGHBOR_SWEEP
            effective_like = True
            effective_comment = False
            mutual_only = True
        else:
            mode = RunMode.NEIGHBOR_NORMAL
            effective_like = configured_like
            effective_comment = configured_comment
    else:
        mode = RunMode.NORMAL
        effective_like = configured_like
        effective_comment = configured_comment
        mutual_only = False

    return RunPolicy(
        mode=mode,
        source_type=source_type,
        effective_like_enabled=effective_like,
        effective_comment_enabled=effective_comment,
        neighbor_mutual_only=mutual_only,
        is_sweep_mode=is_sweep,
        sweep_stop_consecutive_liked=int(_cfg_get(config, "sweep_stop_consecutive_liked", 4)),
    )


def enter_sweep_mode(config_service_or_dict: Any) -> RunPolicy:
    """
    Persistently switches into neighbor sweep mode while backing up
    the user's original comment configuration so it survives restarts.
    """
    is_service = hasattr(config_service_or_dict, "get") and hasattr(config_service_or_dict, "save")
    cfg = _get_cfg_dict(config_service_or_dict)

    # Backup current user comment settings if not already backed up
    if "saved_general_comment_mode" not in cfg:
        cfg["saved_general_comment_mode"] = {
            "comment_enabled": _cfg_get(config_service_or_dict, "comment_enabled", True),
            "auto_comment_submit_enabled": _cfg_get(config_service_or_dict, "auto_comment_submit_enabled", False),
        }

    cfg["neighbor_like_sweep_mode"] = True
    cfg["comment_enabled"] = False

    if is_service and hasattr(config_service_or_dict, "save"):
        try:
            config_service_or_dict.save(cfg) if callable(getattr(config_service_or_dict, "save")) else None
        except Exception:
            pass

    return resolve_run_policy(cfg)


def exit_sweep_mode(config_service_or_dict: Any) -> RunPolicy:
    """
    Exits neighbor sweep mode and restores original user comment settings.
    """
    is_service = hasattr(config_service_or_dict, "get") and hasattr(config_service_or_dict, "save")
    cfg = _get_cfg_dict(config_service_or_dict)

    saved = cfg.pop("saved_general_comment_mode", None)
    if isinstance(saved, dict):
        cfg["comment_enabled"] = saved.get("comment_enabled", True)
        if "auto_comment_submit_enabled" in saved:
            cfg["auto_comment_submit_enabled"] = saved["auto_comment_submit_enabled"]

    cfg["neighbor_like_sweep_mode"] = False

    if is_service and hasattr(config_service_or_dict, "save"):
        try:
            config_service_or_dict.save(cfg) if callable(getattr(config_service_or_dict, "save")) else None
        except Exception:
            pass

    return resolve_run_policy(cfg)


def ensure_comment_mode_consistency(config_service_or_dict: Any) -> None:
    """
    Called on startup to ensure config is consistent if app previously exited in sweep mode.
    """
    cfg = _get_cfg_dict(config_service_or_dict)
    if not cfg.get("neighbor_like_sweep_mode", False) and "saved_general_comment_mode" in cfg:
        exit_sweep_mode(config_service_or_dict)
