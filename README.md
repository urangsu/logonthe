# 📱 Naver Feed Assistant (네이버 피드 어시스턴트)

> **Human-in-the-loop 모바일 피드 순회 · 안전한 공감 · 댓글 초안 자동입력 · Enter 승인 시스템**

---

## 🌟 핵심 특징

1. **Human-in-the-loop 반자동 승인 워크플로**:
   - 프로그램이 피드를 순회하며 공감을 확인하고 댓글 초안을 입력해 줍니다.
   - 사용자가 내용을 수정한 뒤 **`Enter`를 누르면 최종 등록**되며 다음 글로 자동 이동합니다.
2. **실수 클릭/취소 방지 3단계 상태 판별 (`LikeState`)**:
   - `LIKED` (기 공감): 아무것도 하지 않음 (기존 공감 유지)
   - `NOT_LIKED` (미공감): 안전하게 공감 클릭 후 `LIKED` 전이 검증
   - `UNKNOWN` (불명확): **취소 방지를 위해 절대 클릭 금지**
3. **댓글 등록 성공 엄격 검증 (`CommentSubmitState`)**:
   - 단순 버튼 클릭이 아닌, 에디터 비워짐 및 DOM 렌더링을 실제 검증하여 `SUBMITTED`로 기록합니다.
4. **Clean Architecture 모듈 분리**:
   - `app/` (Models, State Machine, PostProcessor, FeedController)
   - `browser/` (단일 Persistent BrowserSession, Multi-Page, ProfileLockManager)
   - `naver/` (MobileDOMResolver, InteractionService, FeedSources)
   - `services/` (Config v2, DraftService, Structured History v2)
   - `ui/` (CustomTkinter Feed Assistant GUI)

---

## ⌨️ 단축키 안내

댓글 초안이 입력된 상태에서 에디터 포커스 중:
- **`Enter`**: 댓글 최종 등록 및 다음 게시글로 이동
- **`Shift + Enter`**: 줄바꿈 (여러 줄 댓글 작성)
- **`Escape (Esc)`**: 이번 게시글 건너뛰기

---

## 🚀 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt
playwright install chromium

# 프로그램 실행
python3 main.py
```

### 권장 사용 순서:
1. `🌐 로그인 창 열기` 버튼을 눌러 네이버 로그인 1회 완료 후 브라우저 닫기 (`data/user_profile`에 영구 저장).
2. 피드 대상(이웃 새글 / 추천 탐색 / 직접 URL) 선택 및 기본 문구 설정.
3. `▶ 피드 작업 시작` 클릭!
4. 브라우저에서 댓글 내용을 확인/수정하고 `Enter`를 누르며 쾌적하게 피드를 순회합니다.

---

## 🧪 테스트 실행

```bash
python3 -m unittest tests/test_units.py
```
