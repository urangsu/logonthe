class BotError(Exception):
    """네이버 피드 어시스턴트 기본 예외"""
    pass


class FatalSessionError(BotError):
    """전체 세션을 즉시 중단해야 하는 치명적 예외 (브라우저 비정상 종료, 설정 오류 등)"""
    pass


class BrowserDisconnectedError(FatalSessionError):
    """브라우저 연결 끊김 또는 세션 크래시"""
    pass


class PersistentProfileError(FatalSessionError):
    """프로필 락 충돌 또는 프로필 디렉터리 손상"""
    pass


class InvalidConfigError(FatalSessionError):
    """필수 설정값 누락 또는 유효하지 않은 설정"""
    pass


class UserStopRequestedError(FatalSessionError):
    """사용자가 작업을 중지함"""
    pass


class RecoverablePostError(BotError):
    """개별 게시글 단위에서 발생한 복구 가능한 예외 (해당 글만 건너뛰고 다음 글로 진행)"""
    def __init__(self, message: str, post_key: str = "", reason: str = "error"):
        super().__init__(message)
        self.post_key = post_key
        self.reason = reason


class PostNavigationMismatchError(RecoverablePostError):
    """네비게이션 실패 또는 예상 post.key와 실제 열린 페이지 URL 불일치 (Fail-Open 차단)"""
    pass


class PostDOMContractError(RecoverablePostError):
    """필수 DOM 요소 탐색 실패 또는 구조 변경 감지"""
    pass


class CommentUnavailableError(RecoverablePostError):
    """댓글이 비활성화되었거나 로그인이 필요한 게시글"""
    pass


class GenerationError(RecoverablePostError):
    """댓글 생성 중 발생한 예외"""
    pass


class PostTimeoutError(RecoverablePostError):
    """게시글 처리 중 타임아웃 발생"""
    pass
