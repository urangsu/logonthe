"""Cross-process ownership of the Naver browser and local comment history."""
import fcntl
from pathlib import Path
from contextlib import contextmanager

DEFAULT_LOCK = Path(__file__).resolve().parent.parent / 'data' / 'assistant-worker.lock'


class BrowserWorkerBusy(RuntimeError):
    pass


@contextmanager
def browser_worker(lock_path=DEFAULT_LOCK):
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BrowserWorkerBusy('다른 작업이 네이버 브라우저를 사용 중입니다.') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
