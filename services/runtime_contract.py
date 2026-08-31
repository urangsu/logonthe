import os
import json
from dataclasses import dataclass

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME_CONTRACT_PATH = os.path.join(WORKSPACE_DIR, "browser_extension", "runtime_contract.json")


@dataclass(frozen=True)
class RuntimeContract:
    extension_version: str
    runtime_build: str
    protocol_version: int
    bridge_schema_version: int


def load_runtime_contract() -> RuntimeContract:
    """browser_extension/runtime_contract.json을 단일 진실 공급원(Source of Truth)으로 로드"""
    if os.path.exists(RUNTIME_CONTRACT_PATH):
        try:
            with open(RUNTIME_CONTRACT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return RuntimeContract(
                    extension_version=str(data.get("extensionVersion", "13.2.3")),
                    runtime_build=str(data.get("runtimeBuild", "13.2.3-r1")),
                    protocol_version=int(data.get("protocolVersion", 3)),
                    bridge_schema_version=int(data.get("bridgeSchemaVersion", 2)),
                )
        except Exception:
            pass

    return RuntimeContract(
        extension_version="13.2.3",
        runtime_build="13.2.3-r1",
        protocol_version=3,
        bridge_schema_version=2,
    )
