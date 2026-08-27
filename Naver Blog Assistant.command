#!/bin/zsh
set -eu
HERE="${0:A:h}"
PROJECT="$HERE"
if [[ ! -f "$PROJECT/main.py" ]]; then
  PROJECT="/Volumes/무제/jusik/naver-blog-bot"
fi
fail() { print -r -- "$1"; read -r "?Enter를 누르면 닫습니다. "; exit 1; }
[[ -d "$PROJECT" && -f "$PROJECT/main.py" ]] || fail '프로젝트를 찾을 수 없습니다. 외장 볼륨 무제가 연결되어 있는지 확인하세요.'
[[ -x "$PROJECT/.venv/bin/python" ]] || fail '최초 설치가 필요합니다. 프로젝트의 Naver Blog Assistant Setup.command를 먼저 실행하세요.'
cd "$PROJECT"
"$PROJECT/.venv/bin/python" -c 'import tkinter, customtkinter, playwright, requests, google.auth, google_auth_oauthlib, keyring' || fail '의존성이 누락되었습니다. Setup.command로 설치를 복구하세요. 설정과 로그인 프로필은 유지됩니다.'
"$PROJECT/.venv/bin/python" main.py || fail '앱이 종료되었습니다. 위 오류와 프로필 사용 여부를 확인하세요. 원본 설정과 로그인 데이터는 삭제하지 않았습니다.'
