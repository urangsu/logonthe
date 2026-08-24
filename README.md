# Naver Blog Auto Bot (네이버 블로그 공감 & 댓글 자동화 프로그램)

네이버 블로그를 대상으로 **1번 기능: 공감(하트) 자동 클릭 및 페이지 자동 넘어감**과 **2번 기능: 댓글 자동 작성**을 지원하는 파이썬 GUI 프로그램입니다.

---

## 🌟 주요 기능

### 1. 1번 기능: 공감(하트) 자동 누르기 & 페이지 전환
- 게시글 방문 후 아래로 스크롤 다운하며 공감(하트) 버튼을 찾아 클릭합니다.
- `<a ng-if="currentPage!=page" class="item" aria-label="N페이지">` 숫자 페이지 및 `<a ng-if="paginationCtrl.hasNextPage()" class="button_next" ng-click="paginationCtrl.goNextGroup()">` 그룹 이동 버튼을 자동으로 파악하여 클릭 및 이동합니다.
- 클릭 및 이동 시 **0 ~ 2초 사이의 무작위 난수 딜레이(Random Delay)**를 적용하여 네이버 안티 디텍션을 회피합니다.

### 2. 2번 기능: 댓글 자동 작성
- 키워드 검색 또는 대상 블로그 URL 목록을 받아 `mainFrame` iframe 내 댓글 창을 자동 입력합니다.
- Spintax 문구 지원 (`{좋은|유익한|멋진} 포스팅 잘 보고 갑니다!`)으로 댓글 다변화.
- 비밀댓글 옵션 선택 및 중복 작성 방지 DB (`data/history.json`) 지원.

### 3. 로그인 및 세션 관리
- 최초 1회 전용 브라우저를 열어 네이버 로그인을 완료하면 세션/쿠키가 `data/user_profile`에 저장되어 이후 실행 시 캡차 없이 로그인 상태가 자동 유지됩니다.

---

## 🚀 설치 및 실행 방법 (Mac / Windows 공통)

### 1. 필수 라이브러리 및 브라우저 드라이버 설치 (최초 1회만 실행)
터미널에서 아래 명령어를 실행합니다:

```bash
cd /Volumes/무제/jusik/naver-blog-bot
pip3 install -r requirements.txt
playwright install chromium
```
*(이미 설치를 완료해 두었습니다)*

### 2. 프로그램 실행
터미널에서 `python3` 명령어로 실행합니다:

```bash
python3 /Volumes/무제/jusik/naver-blog-bot/main.py
```
또는 `naver-blog-bot` 폴더 내부에서:
```bash
cd /Volumes/무제/jusik/naver-blog-bot
python3 main.py
```


---

## 📂 폴더 구조

```
naver-blog-bot/
├── README.md               # 가이드 문서
├── requirements.txt        # 패키지 목록
├── config.json             # 기본 설정
├── main.py                 # CustomTkinter GUI 실행 파일
├── data/
│   ├── history.json        # 작성 완료된 게시글 DB
│   └── user_profile/       # 네이버 로그인 세션 저장소
└── src/
    ├── browser.py          # Playwright 세션 관리자
    ├── collector.py        # 네이버 블로그 검색/URL 수집기
    ├── liker.py            # [1번 기능] 공감 클릭 & 페이징 전환 엔진
    ├── commenter.py        # [2번 기능] 댓글 작성 엔진
    ├── spintax.py          # 스핀텍스 템플릿 변환기
    └── logger.py           # 작업 로그 기록기
```
