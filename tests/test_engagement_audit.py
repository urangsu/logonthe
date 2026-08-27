"""Audit contract tests. Every write is isolated from the operator's data directory."""
import copy
import csv
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from services.buddy_list_collector import BuddyListCollector, BuddyInfo, BuddyCollectionResult
from services.engagement_audit_service import EngagementAuditService
from services.engagement_audit_store import EngagementAuditStore
from services.reaction_participant_collector import ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 12, tzinfo=KST)


class IsolatedAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def repository(self):
        from services import engagement_audit_service as module
        cls = getattr(module, "AuditRepository", None)
        self.assertIsNotNone(cls, "AuditRepository must isolate and preserve immutable runs")
        return cls(self.root / "audit.sqlite3")


class CollectorContracts(unittest.TestCase):
    def collect(self, collector, data, stop_event=None):
        page = MagicMock()
        page.evaluate.return_value = data
        page.locator.return_value.first.count.return_value = 0
        with patch("services.reaction_participant_collector.interruptible_wait"), patch("services.comment_participant_collector.interruptible_wait"):
            return collector.collect(page, "owner", "123", stop_event)

    def test_menu_names_are_never_people(self):
        data = {"scopeVerified": True, "terminal": True, "countUnit": "people", "displayedCount": 6,
                "items": [{"blog_id": value, "nickname": value} for value in
                          ["real_person", "Cart.naver", "MarketPlace.naver", "ReadHistoryList.naver", "BlogTagView.naver", "CheckIn.naver"]]}
        people, state, _ = self.collect(ReactionParticipantCollector, data)
        self.assertEqual([p["blog_id"] for p in people], ["real_person"])
        self.assertEqual(state, "partial")

    def test_unscoped_results_fail_closed(self):
        people, state, _ = self.collect(ReactionParticipantCollector,
            {"items": [{"blog_id": "menu_user"}], "displayedCount": 1, "terminal": True})
        self.assertEqual(people, [])
        self.assertEqual(state, "failed")

    def test_missing_total_without_terminal_is_partial(self):
        _, state, _ = self.collect(ReactionParticipantCollector,
            {"scopeVerified": True, "items": [{"blog_id": "real_user"}], "displayedCount": None})
        self.assertEqual(state, "partial")

    def test_missing_total_with_positive_terminal_is_complete(self):
        _, state, _ = self.collect(ReactionParticipantCollector,
            {"scopeVerified": True, "items": [{"blog_id": "real_user"}], "terminal": True})
        self.assertEqual(state, "complete")

    def test_overshoot_is_partial(self):
        _, state, _ = self.collect(ReactionParticipantCollector,
            {"scopeVerified": True, "items": [{"blog_id": "one"}, {"blog_id": "two"}],
             "displayedCount": 1, "countUnit": "people", "terminal": True})
        self.assertEqual(state, "partial")

    def test_cancel_never_complete(self):
        stop = threading.Event(); stop.set()
        _, state, _ = self.collect(ReactionParticipantCollector,
            {"scopeVerified": True, "items": [], "displayedCount": 0, "terminal": True}, stop)
        self.assertEqual(state, "cancelled")

    def test_comment_entry_count_not_unique_people(self):
        _, state, total = self.collect(CommentParticipantCollector,
            {"scopeVerified": True, "items": [{"blog_id": "one", "comment_entry_count": 2}],
             "entries": [{'entry_id': '1', 'blog_id': 'one'}, {'entry_id': '2', 'blog_id': 'one'}],
             "totalLoadedEntries": 2, "unresolvedEntries": 0, "displayedCount": 2,
             "countUnit": "entries", "terminal": True})
        self.assertEqual(state, "complete")
        self.assertEqual(total, 2)

    def test_comment_unresolved_author_does_not_prove_absence(self):
        _, state, _ = self.collect(CommentParticipantCollector,
            {"scopeVerified": True, "items": [], "totalLoadedEntries": 1,
             "unresolvedEntries": 1, "displayedCount": 1, "countUnit": "entries", "terminal": True})
        self.assertEqual(state, "partial")

    def test_buddy_duplicate_page_cannot_complete(self):
        page = MagicMock(); frame = page.frame.return_value
        record = {"scopeVerified": True, "expectedTotal": 2, "items": [
            {"blog_id": "one", "nickname": "One", "added_date": "26.01.01."}],
            "nextPage": 2, "terminal": False}
        frame.evaluate.side_effect = [record, True, record]
        with patch("services.buddy_list_collector.interruptible_wait"):
            result = BuddyListCollector.collect_all_buddies(page, "owner")
        self.assertEqual(result.state, "partial")
        self.assertIn("duplicate_page", result.quality_issues)
        self.assertEqual(result.buddies["one"].new_posts_setting, "unknown")


class AuditPolicyContracts(IsolatedAuditTest):
    def run_case(self, *, added="26.01.01.", state="complete", dates=True, count=5, stopped=False):
        from services.audit_models import ParticipantCollection, RecentPostCollection
        buddies = {"buddy": BuddyInfo("buddy", "Buddy", "", "", "이웃", None, added)}
        b_result = BuddyCollectionResult(buddies, "complete", 1, 1, 1, ["one"], source_kind="fixture")
        posts = [{"log_no": str(100+i), "url": f"https://m.blog.naver.com/owner/{100+i}", "title": f"Post {i}",
                  "published_at": "2026-08-20T12:00:00+09:00" if dates else None,
                  "published_at_precision": "second" if dates else "unknown"} for i in range(count)]
        empty = ParticipantCollection([], state, 0 if state == "complete" else None,
                                      source_kind="fixture", terminal=state == "complete")
        repo = self.repository()
        stop = threading.Event()
        if stopped: stop.set()
        with patch.object(BuddyListCollector, "collect_all_buddies", return_value=b_result), \
             patch("services.my_blog_recent_posts.MyBlogRecentPostService.fetch_recent_posts", return_value=RecentPostCollection(posts, "complete", source_kind="fixture")), \
             patch.object(ReactionParticipantCollector, "collect", return_value=empty), \
             patch.object(CommentParticipantCollector, "collect", return_value=empty):
            return EngagementAuditService.run_audit(MagicMock(), "owner", 5, stop, repository=repo, now=NOW)

    def test_missing_dates_means_unknown_not_no_reaction(self):
        self.repository()
        report = self.run_case(dates=False)["report"]
        row = report["master_buddies"][0]
        self.assertIsNone(row["no_reaction"])
        self.assertEqual(row["reaction_status"], "확인불가")
        self.assertEqual(report["unresponsive_buddies"], [])
        self.assertEqual(report["source_kind"], "fixture")

    def test_partial_missing_observations_are_lower_bounds(self):
        self.repository()
        result = self.run_case(state="partial")
        row = result["report"]["master_buddies"][0]
        self.assertIsNone(row["like_count"])
        self.assertEqual(row["observed_like_count"], 0)
        self.assertTrue(row["counts_are_lower_bounds"])
        self.assertIsNone(row["no_reaction"])
        self.assertEqual(result["audit_state"], "partial")

    def test_three_mature_post_addition_posts_allow_review(self):
        self.repository()
        row = self.run_case()["report"]["master_buddies"][0]
        self.assertTrue(row["no_reaction"])
        self.assertEqual(row["reaction_status"], "무반응 검토")
        self.assertEqual(row["eligible_post_count"], 5)

    def test_posts_before_added_do_not_count(self):
        self.repository()
        row = self.run_case(added="26.08.23.")["report"]["master_buddies"][0]
        self.assertIsNone(row["no_reaction"])
        self.assertEqual(row["eligible_post_count"], 0)

    def test_grace_uses_kst_calendar_days(self):
        method = EngagementAuditService.is_grace_period
        self.assertIn("now", __import__("inspect").signature(method).parameters)
        self.assertTrue(method("26.08.25.", now=NOW))
        self.assertFalse(method("26.08.24.", now=NOW))
        self.assertIsNone(method("", now=NOW))

    def test_cancel_saved_and_not_successful(self):
        self.repository()
        result = self.run_case(stopped=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["audit_state"], "cancelled")
        self.assertEqual(self.repository().get_run(result["run_id"])["audit_state"], "cancelled")


class AuditPersistenceContracts(IsolatedAuditTest):
    def report(self):
        return {"generated_at": NOW.isoformat(), "blog_id": "owner", "source_kind": "fixture",
                "audit_state": "partial", "policy_version": "v1.1", "posts": [],
                "master_buddies": [{"blog_id": "buddy", "like_count": None, "comment_count": None}],
                "non_buddy_reactors": [], "unresponsive_buddies": []}

    def test_immutable_runs_are_distinct_and_atomic(self):
        repo = self.repository(); report = self.report()
        first = repo.save_run(report); second = repo.save_run(report)
        self.assertNotEqual(first, second)
        saved = repo.get_run(first)
        self.assertEqual(saved["run_id"], first)
        with self.assertRaises(ValueError): repo.save_run(saved)
        invalid = self.report(); invalid["master_buddies"] *= 2
        with self.assertRaises(Exception): repo.save_run(invalid)
        self.assertEqual(len(repo.list_runs()), 2)
        self.assertEqual(repo.latest_run()["run_id"], second)

    def test_legacy_import_read_only_and_excluded(self):
        repo = self.repository(); old = self.root / "my_blog_engagement_audit.json"
        old.write_text(json.dumps(self.report()), encoding="utf-8"); before = old.read_bytes()
        ids = repo.import_legacy(self.root)
        self.assertEqual(len(ids), 1)
        self.assertEqual(old.read_bytes(), before)
        report = repo.get_run(ids[0])
        self.assertEqual(report["source_kind"], "legacy_unverified")
        self.assertFalse(report["comparison_eligible"])
        self.assertEqual(repo.import_legacy(self.root), [])

    def test_per_run_exports_never_overwrite_and_null_stays_empty(self):
        self.assertIn("directory", __import__("inspect").signature(EngagementAuditStore.save_v8).parameters)
        paths = EngagementAuditStore.save_v8(self.report(), directory=self.root)
        again = EngagementAuditStore.save_v8(self.report(), directory=self.root)
        self.assertNotEqual(paths, again)
        with open(paths[1], encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(rows[0]["공감한글수"], "")
        self.assertEqual(rows[0]["참여여부"], "확인불가")

    def test_comparison_requires_same_post_policy_cohort_and_live(self):
        repo = self.repository()
        method = getattr(repo, "compare_runs", None)
        self.assertIsNotNone(method)
        first = repo.save_run(self.report()); second = repo.save_run(self.report())
        result = method(first, second)
        self.assertFalse(result["comparable"])
        self.assertIsNone(result["delta"])


if __name__ == "__main__": unittest.main()

class AdditionalAuditEvidenceContracts(IsolatedAuditTest):
    def test_invalid_profile_hosts_and_routes(self):
        from services.audit_models import canonical_blog_id
        for value in ("https://blog.naver.com.evil.example/user", "https://m.blog.naver.com/Cart.naver", "Cart.naver", "https://blog.naver.com/user/123", "https://blog.naver.com:443/user"):
            self.assertIsNone(canonical_blog_id(value))
        self.assertEqual(canonical_blog_id("https://blog.naver.com/PostList.naver?blogId=real_user"), "real_user")

    def test_date_only_maturity_does_not_assume_midnight(self):
        from services.audit_models import published_bounds
        early, late = published_bounds("2026-08-25", "date")
        self.assertLess(NOW - late, dt.timedelta(hours=48))
        self.assertGreater(NOW - early, dt.timedelta(hours=48))

    def test_post_metadata_supports_dates_without_fabricated_relative_time(self):
        from services.my_blog_recent_posts import normalize_publication
        self.assertEqual(normalize_publication("2026. 8. 25."), ("2026-08-25", "date"))
        self.assertEqual(normalize_publication("3일 전"), (None, "unknown"))

    def test_legacy_csv_formula_text_is_not_executed_on_export(self):
        report = {"master_buddies": [{"blog_id": "buddy", "nickname": '=HYPERLINK("malicious")'}]}
        paths = EngagementAuditStore.save_v8(report, directory=self.root)
        with open(paths[1], encoding="utf-8-sig") as file:
            row = next(csv.DictReader(file))
        self.assertTrue(row["닉네임"].startswith("'="))

    def test_comment_replacement_pages_without_entry_ids_are_partial(self):
        from services.reaction_participant_collector import collect_pages
        page = MagicMock()
        page.locator.return_value.first.count.return_value = 1
        page.locator.return_value.first.is_visible.return_value = True
        page.evaluate.side_effect = [
            {"scopeVerified": True, "items": [{"blog_id": "one", "comment_entry_count": 1}], "totalLoadedEntries": 1, "hasMore": True},
            {"scopeVerified": True, "items": [{"blog_id": "two", "comment_entry_count": 1}], "totalLoadedEntries": 1, "terminal": True},
        ]
        with patch("services.reaction_participant_collector.interruptible_wait"):
            result = collect_pages(page, "fixture", "fixture", None, kind="comment")
        self.assertEqual(result.state, "partial")
        self.assertIn("entry_identity_unverified", result.quality_issues)

class PositiveAndComparisonContracts(IsolatedAuditTest):
    def test_positive_reactions_keep_people_posts_and_entries_separate(self):
        from services.audit_models import ParticipantCollection, RecentPostCollection
        buddy = BuddyInfo('buddy', 'Buddy', '', '', '이웃', None, '26.01.01.')
        result = BuddyCollectionResult({'buddy': buddy}, 'complete', 1, 1, 1, ['fixture'], source_kind='fixture')
        posts = RecentPostCollection([{'log_no': str(100+i), 'url': f'https://m.blog.naver.com/owner/{100+i}', 'title': 'Post',
            'published_at': '2026-08-20', 'published_at_precision': 'date'} for i in range(5)], 'complete', source_kind='fixture')
        like = ParticipantCollection([{'blog_id': 'buddy'}], 'complete', 1, source_kind='fixture')
        comment = ParticipantCollection([{'blog_id': 'buddy', 'comment_entry_count': 2}, {'blog_id': 'stranger', 'comment_entry_count': 1}],
            'complete', 3, count_unit='entries', source_kind='fixture')
        with patch.object(BuddyListCollector, 'collect_all_buddies', return_value=result), \
             patch('services.my_blog_recent_posts.MyBlogRecentPostService.fetch_recent_posts', return_value=posts), \
             patch.object(ReactionParticipantCollector, 'collect', return_value=like), \
             patch.object(CommentParticipantCollector, 'collect', return_value=comment):
            report = EngagementAuditService.run_audit(MagicMock(), 'owner', repository=self.repository(), now=NOW)['report']
        row = report['master_buddies'][0]
        self.assertEqual((row['like_count'], row['comment_count'], row['comment_entry_count'], row['engaged_post_count']), (5, 5, 10, 5))
        self.assertFalse(row['no_reaction'])
        self.assertEqual(report['posts'][0]['commenter_count'], 2)
        self.assertEqual(report['posts'][0]['comment_entry_count'], 3)
        self.assertEqual(report['non_buddy_reactors'][0]['blog_id'], 'stranger')
        self.assertEqual(len(report['reaction_observations']), 10)

    def test_comparable_live_runs_delta_and_cohort_guard(self):
        repo = self.repository()
        report = {'blog_id': 'owner', 'source_kind': 'live', 'audit_state': 'complete', 'capability_verified': True,
                  'policy_version': 'v1.1', 'posts': [{'log_no': '1', 'published_at': '2026-08-20', 'published_at_precision': 'date'}],
                  'master_buddies': [{'blog_id': 'buddy', 'like_count': 1, 'comment_count': 0, 'comment_entry_count': 0, 'engaged_post_count': 1}]}
        first = repo.save_run(report)
        report['master_buddies'][0]['comment_count'] = 1
        report['master_buddies'][0]['comment_entry_count'] = 2
        second = repo.save_run(report)
        delta = repo.compare_runs(first, second)
        self.assertTrue(delta['comparable'])
        self.assertEqual(delta['delta'][0]['comment_entry_count'], 2)
        report['master_buddies'][0]['blog_id'] = 'other'
        third = repo.save_run(report)
        self.assertEqual(repo.compare_runs(second, third)['reasons'], ['cohort_mismatch'])

class RepositoryConnectionContracts(IsolatedAuditTest):
    def test_transaction_context_closes_connection(self):
        import sqlite3
        repo = self.repository()
        with repo._connect() as db:
            db.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
