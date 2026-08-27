#!/bin/zsh
set -eu
PROJECT="${0:A:h}"
fail() { print -r -- "$1"; read -r "?Enter를 누르면 닫습니다. "; exit 1; }
[[ -f "$PROJECT/requirements.txt" ]] || fail '프로젝트 안에서 설치기를 실행하세요.'
command -v python3 >/dev/null || fail 'Python 3.10 이상이 필요합니다. python.org의 macOS 설치 프로그램을 사용하세요.'
python3 -c 'import sys, tkinter; assert sys.version_info >= (3, 10)' || fail 'Tkinter를 포함한 Python 3.10 이상이 필요합니다.'
cd "$PROJECT"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || fail '전용 Python 환경을 만들지 못했습니다. 볼륨 쓰기 권한을 확인하세요.'
fi
.venv/bin/python -m pip install -r requirements.txt || fail '패키지 설치 실패. 네트워크를 확인하고 다시 실행하세요.'
.venv/bin/python -m playwright install chromium || fail 'Chromium 설치 실패. 네트워크를 확인하고 다시 실행하세요.'
print '설치 완료. Naver Blog Assistant.command를 더블클릭하세요.'
print '기존 설정·댓글 이력·로그인 프로필은 초기화하지 않았습니다.'
read -r '?Enter를 누르면 닫습니다. '
