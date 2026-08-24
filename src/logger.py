import os
import logging
from datetime import datetime

DEFAULT_LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

class BotLogger:
    def __init__(self, log_dir: str = DEFAULT_LOG_DIR):
        os.makedirs(log_dir, exist_ok=True)
        self.log_file = os.path.join(log_dir, "bot.log")
        self.gui_callback = None


        logging.basicConfig(
            filename=self.log_file,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8"
        )
        self.logger = logging.getLogger("NaverBlogBot")

    def register_gui_callback(self, callback_fn):
        """GUI 로그 창으로 메시지를 전송하는 콜백 함수 등록"""
        self.gui_callback = callback_fn

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] [{level}] {message}"
        
        if level == "INFO":
            self.logger.info(message)
        elif level == "WARNING":
            self.logger.warning(message)
        elif level == "ERROR":
            self.logger.error(message)

        print(formatted)

        if self.gui_callback:
            try:
                self.gui_callback(formatted)
            except Exception:
                pass

logger = BotLogger()
