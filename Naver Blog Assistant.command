#!/bin/zsh
set -u
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  osascript -e 'display alert "Naver Blog Assistant" message "python3를 찾지 못했습니다. Python 3를 설치한 뒤 다시 실행하세요." as warning'
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import customtkinter, playwright' >/dev/null 2>&1; then
  osascript -e 'display alert "Naver Blog Assistant" message "필수 의존성이 없습니다. 터미널에서 python3 -m pip install -r requirements.txt && playwright install chromium 을 한 번 실행하세요." as warning'
  exit 1
fi

exec "$PYTHON_BIN" main.py
