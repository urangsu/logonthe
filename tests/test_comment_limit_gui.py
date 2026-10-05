import os
import subprocess
from unittest.mock import patch

import pytest

from services.config import ConfigService
from services.history import HistoryStore
from src.logger import logger


@pytest.mark.skipif(os.environ.get("NAVER_GUI_TEST") != "1", reason="Requires a local macOS GUI session")
def test_visible_comment_limit_saves_to_config_and_controller(tmp_path):
    from ui.main_window import MainWindow

    config = ConfigService(str(tmp_path / "config.json"))
    config.update_many({"max_feed_items": 52, "comment_blog_limit": 2, "comment_enabled": True})
    history = HistoryStore(str(tmp_path / "history.json"))
    with patch("ui.main_window.ConfigService", return_value=config), \
         patch("ui.main_window.HistoryStore", return_value=history), \
         patch("ui.main_window.GeminiBridgeHTTPServer"), \
         patch.object(logger, "register_gui_callback"):
        app = MainWindow()
        try:
            for width in (940, 860):
                app.geometry(f"{width}x640+20+20")
                app.update()
                entry = app.comment_blog_limit_entry
                assert entry.winfo_viewable()
                assert entry.get() == "2"
                assert entry.winfo_rootx() + entry.winfo_width() <= app.winfo_rootx() + app.winfo_width()
                assert entry.winfo_rooty() + entry.winfo_height() <= app.winfo_rooty() + app.winfo_height()

            app.comment_enabled_var.set(False)
            assert app.target_count_label.cget("text") == "목표 공감 수:"
            app.comment_enabled_var.set(True)
            assert app.target_count_label.cget("text") == "목표 댓글 수:"

            app.comment_blog_limit_entry.delete(0, "end")
            app.comment_blog_limit_entry.insert(0, "0")
            with patch("ui.main_window.messagebox.showwarning") as warning:
                app._start_task()
                warning.assert_called_once()
            assert config.get("comment_blog_limit") == 2

            app.comment_blog_limit_entry.delete(0, "end")
            app.comment_blog_limit_entry.insert(0, "1")
            with patch("ui.main_window.FeedController") as controller, \
                 patch("ui.main_window.threading.Thread"):
                app._start_task()
                assert controller.call_args.kwargs["config"].get("comment_blog_limit") == 1
            assert ConfigService(config.config_path).get("comment_blog_limit") == 1

            screenshot = os.environ.get("NAVER_GUI_SCREENSHOT")
            if screenshot:
                app.lift()
                app.update()
                bounds = f"{app.winfo_rootx()},{app.winfo_rooty()},{app.winfo_width()},{app.winfo_height()}"
                subprocess.run(["screencapture", "-x", "-R", bounds, screenshot], check=True)
        finally:
            app._on_close()
