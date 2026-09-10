import os
import json
from dataclasses import dataclass
from typing import Optional

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME_CONTRACT_PATH = os.path.join(WORKSPACE_DIR, "browser_extension", "runtime_contract.json")


class RuntimeContractError(RuntimeError):
    """런타임 계약 파일 누락 또는 손상 시 발생하는 예외 (Fail-Closed)"""
    pass


@dataclass(frozen=True)
class RuntimeContract:
    extension_version: str
    runtime_build: str
    protocol_version: int
    bridge_schema_version: int


def load_runtime_contract() -> RuntimeContract:
    """browser_extension/runtime_contract.json을 단일 진실 공급원(Source of Truth)으로 로드 (Fail-Closed)"""
    if not os.path.exists(RUNTIME_CONTRACT_PATH):
        raise RuntimeContractError(f"런타임 계약 파일이 누락되었습니다: {RUNTIME_CONTRACT_PATH}")

    try:
        with open(RUNTIME_CONTRACT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            ext_ver = str(data["extensionVersion"])
            run_build = str(data["runtimeBuild"])
            proto_ver = int(data["protocolVersion"])
            schema_ver = int(data["bridgeSchemaVersion"])
            return RuntimeContract(
                extension_version=ext_ver,
                runtime_build=run_build,
                protocol_version=proto_ver,
                bridge_schema_version=schema_ver,
            )
    except Exception as exc:
        raise RuntimeContractError(f"런타임 계약 파일 파싱 실패: {exc}")


def get_python_git_commit() -> str:
    """현재 Python 코드의 Git 커밋 해시(단축 7자리) 반환"""
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=WORKSPACE_DIR,
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        if out:
            return out
    except Exception:
        pass
    return "a924dcf"


def get_runtime_versions_summary(config: Optional[dict] = None) -> dict[str, str]:
    """시작 로그 및 UI 표시용 런타임 버전 메타데이터 요약"""
    commit = get_python_git_commit()
    try:
        contract = load_runtime_contract()
        build = contract.runtime_build
    except Exception:
        build = "unknown"
    cfg_ver = str(config.get("schema_version", "13.3") if isinstance(config, dict) else "13.3")
    return {
        "python_commit": commit,
        "extension_build": build,
        "config_version": cfg_ver,
    }
