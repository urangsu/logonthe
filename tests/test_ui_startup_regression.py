import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.main_window import MainWindow


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class StartupStateTests(unittest.TestCase):
    def make_window(self, mode="사용 안 함"):
        win = SimpleNamespace(
            comment_mode_var=Value(mode),
            comment_enabled_var=Value(mode != "사용 안 함"),
            auto_comment_submit_var=Value(mode == "자동 등록"),
            neighbor_mutual_only_var=Value(False),
            comment_mode_seg=MagicMock(),
            chk_neighbor_mutual=MagicMock(),
        )
        return win

    def test_inactive_sweep_preserves_configured_comment_mode(self):
        for mode in ("사용 안 함", "초안 검토", "자동 등록"):
            with self.subTest(mode=mode):
                win = self.make_window(mode)
                MainWindow._apply_neighbor_sweep_ui(win, False)
                self.assertEqual(win.comment_mode_var.get(), mode)
                self.assertFalse(win.neighbor_mutual_only_var.get())

    def test_each_sweep_cycle_restores_latest_user_choice(self):
        win = self.make_window()
        for mode in ("사용 안 함", "초안 검토", "자동 등록"):
            win.comment_mode_var.set(mode)
            MainWindow._apply_neighbor_sweep_ui(win, True)
            MainWindow._apply_neighbor_sweep_ui(win, True)
            self.assertEqual(win.comment_mode_var.get(), "사용 안 함")
            MainWindow._apply_neighbor_sweep_ui(win, False)
            self.assertEqual(win.comment_mode_var.get(), mode)
            self.assertEqual(win.comment_enabled_var.get(), mode != "사용 안 함")
            self.assertEqual(win.auto_comment_submit_var.get(), mode == "자동 등록")
            self.assertFalse(win.neighbor_mutual_only_var.get())
            self.assertFalse(hasattr(win, "_saved_comment_mode"))


class RecommendationFeedUITests(unittest.TestCase):
    def test_on_source_change_shows_recommendation_frame(self):
        from app.models import FeedSourceType

        win = SimpleNamespace(
            source_var=Value(FeedSourceType.RECOMMENDATION.value),
            _update_neighbor_options_state=MagicMock(),
            neighbor_options_frame=MagicMock(),
            discovery_frame=MagicMock(),
            direct_url_frame=MagicMock(),
            recommendation_frame=MagicMock(),
        )

        MainWindow._on_source_change(win)

        win.recommendation_frame.pack.assert_called_once()
        win.discovery_frame.pack_forget.assert_called_once()
        win.direct_url_frame.pack_forget.assert_called_once()

    def test_on_source_change_hides_recommendation_frame_on_other_sources(self):
        from app.models import FeedSourceType

        for other_source in (FeedSourceType.TARGETED_SEARCH, FeedSourceType.DIRECT, FeedSourceType.NEIGHBOR):
            with self.subTest(source=other_source.value):
                win = SimpleNamespace(
                    source_var=Value(other_source.value),
                    _update_neighbor_options_state=MagicMock(),
                    neighbor_options_frame=MagicMock(),
                    discovery_frame=MagicMock(),
                    direct_url_frame=MagicMock(),
                    recommendation_frame=MagicMock(),
                )

                MainWindow._on_source_change(win)

                win.recommendation_frame.pack_forget.assert_called_once()

