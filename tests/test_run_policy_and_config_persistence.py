import unittest
from app.models import FeedSourceType
from app.policy import (
    RunMode,
    RunPolicy,
    resolve_run_policy,
    enter_sweep_mode,
    exit_sweep_mode,
    ensure_comment_mode_consistency,
)


class TestRunPolicyAndConfigPersistence(unittest.TestCase):
    def test_resolve_run_policy_normal(self):
        cfg = {
            "feed_source_type": "recommendation",
            "like_enabled": True,
            "comment_enabled": True,
            "neighbor_mutual_only": True,
            "neighbor_like_sweep_mode": False,
        }
        policy = resolve_run_policy(cfg)
        self.assertEqual(policy.mode, RunMode.NORMAL)
        self.assertEqual(policy.source_type, FeedSourceType.RECOMMENDATION)
        self.assertTrue(policy.effective_like_enabled)
        self.assertTrue(policy.effective_comment_enabled)
        self.assertFalse(policy.is_sweep_mode)
        # mutual_only is False for non-neighbor feeds
        self.assertFalse(policy.neighbor_mutual_only)

    def test_resolve_run_policy_neighbor_normal(self):
        cfg = {
            "feed_source_type": "neighbor",
            "like_enabled": True,
            "comment_enabled": True,
            "neighbor_mutual_only": True,
            "neighbor_like_sweep_mode": False,
        }
        policy = resolve_run_policy(cfg)
        self.assertEqual(policy.mode, RunMode.NEIGHBOR_NORMAL)
        self.assertEqual(policy.source_type, FeedSourceType.NEIGHBOR)
        self.assertTrue(policy.effective_like_enabled)
        self.assertTrue(policy.effective_comment_enabled)
        self.assertTrue(policy.neighbor_mutual_only)
        self.assertFalse(policy.is_sweep_mode)

    def test_resolve_run_policy_neighbor_sweep(self):
        cfg = {
            "feed_source_type": "neighbor",
            "like_enabled": False,  # Even if False in config, sweep enforces like=True
            "comment_enabled": True,  # Even if True in config, sweep enforces comment=False
            "neighbor_mutual_only": False,  # Sweep enforces mutual_only=True
            "neighbor_like_sweep_mode": True,
        }
        policy = resolve_run_policy(cfg)
        self.assertEqual(policy.mode, RunMode.NEIGHBOR_SWEEP)
        self.assertEqual(policy.source_type, FeedSourceType.NEIGHBOR)
        self.assertTrue(policy.effective_like_enabled)
        self.assertFalse(policy.effective_comment_enabled)
        self.assertTrue(policy.neighbor_mutual_only)
        self.assertTrue(policy.is_sweep_mode)

    def test_enter_sweep_mode_backs_up_and_exit_restores(self):
        cfg = {
            "feed_source_type": "neighbor",
            "comment_enabled": True,
            "auto_comment_submit_enabled": True,
            "neighbor_like_sweep_mode": False,
        }

        # 1. Enter sweep mode
        policy = enter_sweep_mode(cfg)
        self.assertTrue(policy.is_sweep_mode)
        self.assertFalse(policy.effective_comment_enabled)
        self.assertTrue(cfg["neighbor_like_sweep_mode"])
        self.assertFalse(cfg["comment_enabled"])
        self.assertIn("saved_general_comment_mode", cfg)
        self.assertEqual(cfg["saved_general_comment_mode"]["comment_enabled"], True)
        self.assertEqual(cfg["saved_general_comment_mode"]["auto_comment_submit_enabled"], True)

        # 2. Exit sweep mode restores original comment settings
        policy_restored = exit_sweep_mode(cfg)
        self.assertFalse(policy_restored.is_sweep_mode)
        self.assertTrue(policy_restored.effective_comment_enabled)
        self.assertFalse(cfg["neighbor_like_sweep_mode"])
        self.assertTrue(cfg["comment_enabled"])
        self.assertTrue(cfg["auto_comment_submit_enabled"])
        self.assertNotIn("saved_general_comment_mode", cfg)

    def test_app_restart_with_sweep_persisted(self):
        """
        Simulates an app restart while sweep was active:
        The saved comment configuration is preserved in config.
        When sweep is exited in the new session, the original settings are restored cleanly.
        """
        # Session 1: enter sweep mode
        persisted_config_file_data = {
            "feed_source_type": "neighbor",
            "comment_enabled": True,
            "auto_comment_submit_enabled": False,
            "neighbor_like_sweep_mode": False,
        }
        enter_sweep_mode(persisted_config_file_data)

        # Session 2: simulated restart (loading from persisted dict)
        restarted_config = dict(persisted_config_file_data)
        self.assertTrue(restarted_config["neighbor_like_sweep_mode"])
        self.assertIn("saved_general_comment_mode", restarted_config)

        # User toggles sweep mode off in Session 2
        exit_sweep_mode(restarted_config)
        self.assertFalse(restarted_config["neighbor_like_sweep_mode"])
        self.assertTrue(restarted_config["comment_enabled"])
        self.assertFalse(restarted_config["auto_comment_submit_enabled"])


if __name__ == "__main__":
    unittest.main()
