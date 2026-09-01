import queue
from typing import Optional
from app.models import WorkerCommand, WorkerCommandType


class ClipboardCommandBridge:
    """
    Tkinter UI 스레드와 Playwright Worker 스레드 간의
    Thread-safe 비동기 명령 전달 큐 브릿지
    """
    def __init__(self):
        self._queue: queue.Queue[WorkerCommand] = queue.Queue()

    def send_apply_clipboard_comment(self, text: str):
        """UI 스레드에서 복사된 댓글 텍스트를 Worker 스레드로 전달"""
        cmd = WorkerCommand(kind=WorkerCommandType.APPLY_CLIPBOARD_COMMENT, text=text)
        self._queue.put(cmd)

    def send_gemini_retry(self):
        self._queue.put(WorkerCommand(kind=WorkerCommandType.GEMINI_RETRY))

    def send_gemini_skip_post(self, post_key: str = ""):
        self._queue.put(WorkerCommand(kind=WorkerCommandType.GEMINI_SKIP_POST, post_key=post_key))

    def send_skip_post(self, post_key: str = ""):
        """현재 글 처리를 중단하고 다음 글로 건너뛰도록 명령 전달"""
        self._queue.put(WorkerCommand(kind=WorkerCommandType.SKIP_POST, post_key=post_key))

    def send_gemini_use_local_once(self):
        self._queue.put(WorkerCommand(kind=WorkerCommandType.GEMINI_USE_LOCAL_ONCE))

    def pop_command(self) -> Optional[WorkerCommand]:
        """Worker 스레드에서 대기 중인 명령을 논블로킹으로 꺼냄"""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def clear_skips(self):
        """남아있는 stale SKIP 명령 정리"""
        remaining = []
        while not self._queue.empty():
            try:
                cmd = self._queue.get_nowait()
                if cmd.kind not in (WorkerCommandType.SKIP_POST, WorkerCommandType.GEMINI_SKIP_POST):
                    remaining.append(cmd)
            except queue.Empty:
                break
        for item in remaining:
            self._queue.put(item)

    def clear(self):
        """큐 비우기"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
