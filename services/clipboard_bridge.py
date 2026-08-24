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

    def pop_command(self) -> Optional[WorkerCommand]:
        """Worker 스레드에서 대기 중인 명령을 논블로킹으로 꺼냄"""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def clear(self):
        """큐 비우기"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
