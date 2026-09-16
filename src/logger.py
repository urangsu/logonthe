import os
import sys
import logging
from datetime import datetime
from typing import Optional

DEFAULT_LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))


class FailSafeFileHandler(logging.FileHandler):
    def __init__(self, filename, mode='a', encoding=None, delay=False, errors=None, on_error=None):
        super().__init__(filename, mode=mode, encoding=encoding, delay=delay, errors=errors)
        self.on_error = on_error

    def handleError(self, record):
        exc_info = sys.exc_info()
        exc = exc_info[1] if exc_info else None
        if isinstance(exc, (OSError, IOError)):
            if callable(self.on_error):
                try:
                    self.on_error(exc)
                except Exception:
                    pass
        else:
            super().handleError(record)


class BotLogger:
    def __init__(self, log_dir: str = DEFAULT_LOG_DIR):
        self.log_file = os.path.join(log_dir, "bot.log")
        self.gui_callback = None
        self.file_handler: Optional[logging.FileHandler] = None
        self._file_logging_failed: bool = False

        self.logger = logging.getLogger("NaverBlogBot")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        try:
            os.makedirs(log_dir, exist_ok=True)
            self.file_handler = FailSafeFileHandler(self.log_file, encoding="utf-8", on_error=self.handle_file_error)
            formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            self.file_handler.setFormatter(formatter)
            self.logger.addHandler(self.file_handler)
        except (OSError, IOError) as exc:
            self.handle_file_error(exc)

    def handle_file_error(self, exc: BaseException):
        self._file_logging_failed = True
        errno_val = getattr(exc, "errno", "unknown")
        err_text = f"[LOGGER][FILE_IO_ERROR] errno={errno_val} fallback=console_gui err={exc}"
        print(f"[ERROR] {err_text}", file=sys.stderr)
        try:
            if self.file_handler and self.file_handler in self.logger.handlers:
                self.logger.removeHandler(self.file_handler)
            if self.file_handler:
                self.file_handler.close()
        except Exception:
            pass
        self.file_handler = None

        if self.gui_callback:
            try:
                self.gui_callback(f"[ERROR] {err_text}")
            except Exception:
                pass

    def register_gui_callback(self, callback_fn):
        """GUI 로그 창으로 메시지를 전송하는 콜백 함수 등록"""
        self.gui_callback = callback_fn

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] [{level}] {message}"

        if not self._file_logging_failed and self.file_handler is not None:
            try:
                if level == "INFO":
                    self.logger.info(message)
                elif level == "WARNING":
                    self.logger.warning(message)
                elif level == "ERROR":
                    self.logger.error(message)
            except (OSError, IOError) as exc:
                self.handle_file_error(exc)

                if self.gui_callback:
                    try:
                        self.gui_callback(f"[{timestamp}] [ERROR] {err_text}")
                    except Exception:
                        pass

        print(formatted)

        if self.gui_callback:
            try:
                self.gui_callback(formatted)
            except Exception:
                pass


logger = BotLogger()

