# V1.2 실제 Naver smoke 기록

실행 시각: 2026-08-28 (Asia/Seoul)

## 수행한 확인

- `BrowserSession(headless=True)`이 새 V12 worktree의 `data/user_profile`을 사용해 정상 시작·종료됨
- `https://m.naver.com/` DOMContentLoaded 이동 성공
- 로그인 쿠키 확인 결과: `NID_AUT_missing`, `NID_SES_missing`

## 결과

브라우저 생명주기와 네이버 접속은 PASS. 그러나 이 worktree 프로필에는 인증 쿠키가 없어 아래 항목은 **BLOCKED / 미실행**이다.

- 실제 계정 로그인 유지 및 재실행 확인
- SmartEditor/legacy iframe 본문 추출 표본 대조
- 공감 전후 상태와 설정된 난수 대기 확인
- 댓글 초안 삽입·사용자 등록·등록 결과 관찰
- recoverable 오류 뒤 다음 글 계속 처리
- BuddyList 실제 pagination 및 표시 수 194 대조

계정 로그인 후 같은 브랜치에서 위 표본을 실행하고, 결과를 이 문서에 추가해야 Functional Parity 완료로 판정한다. 인증 쿠키를 코드나 저장소에 기록하지 않는다.
