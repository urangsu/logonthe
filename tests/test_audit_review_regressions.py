import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from services.audit_repository import AuditRepository
from services.audit_models import ParticipantCollection, RecentPostCollection
from services.buddy_list_collector import BuddyCollectionResult, BuddyListCollector
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.reaction_participant_collector import collect_pages, ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector
from services.engagement_audit_service import EngagementAuditService


class AuditReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.repo = AuditRepository(self.path / 'audit.sqlite3')

    def test_missing_comment_ids_cannot_claim_exact_counts(self):
        page = MagicMock()
        page.evaluate.return_value = {'scopeVerified': True, 'items': [{'blog_id': 'buddy', 'comment_entry_count': 2}],
            'entries': [{'entry_id': '1', 'blog_id': 'buddy', 'mine': False}], 'totalLoadedEntries': 2,
            'displayedCount': 2, 'countUnit': 'entries', 'terminal': True}
        result = collect_pages(page, 'fixture', 'fixture', None, kind='comment')
        self.assertEqual(result.state, 'partial')
        self.assertIn('entry_identity_coverage_incomplete', result.quality_issues)
        self.assertEqual(result.observed_entry_count, 2)

    def test_partial_buddy_list_does_not_certify_nonbuddy(self):
        buddies = BuddyCollectionResult({}, 'partial', 2, 0, 1, ['x'], source_kind='fixture')
        posts = RecentPostCollection([{'log_no': '123', 'published_at': '2026-08-20', 'published_at_precision': 'date'}], 'complete', source_kind='fixture')
        likes = ParticipantCollection([{'blog_id': 'missing_buddy'}], 'complete', 1, terminal=True, source_kind='fixture')
        comments = ParticipantCollection([], 'complete', 0, terminal=True, source_kind='fixture')
        with patch.object(BuddyListCollector, 'collect_all_buddies', return_value=buddies), \
             patch.object(MyBlogRecentPostService, 'fetch_recent_posts', return_value=posts), \
             patch.object(ReactionParticipantCollector, 'collect', return_value=likes), \
             patch.object(CommentParticipantCollector, 'collect', return_value=comments):
            result = EngagementAuditService.run_audit(MagicMock(), 'owner', repository=self.repo)['report']
        self.assertEqual(result['non_buddy_reactors'], [])
        self.assertEqual(result['unknown_relationship_reactors'][0]['blog_id'], 'missing_buddy')
        self.assertEqual(result['unknown_relationship_reactors'][0]['relationship_state'], 'unknown')

    def test_legacy_menu_claims_preserved_only_as_excluded_evidence(self):
        path = self.path / 'my_blog_engagement_audit.json'
        original = {'blog_id': 'owner', 'master_buddies': [], 'non_buddy_reactors': [{'blog_id': 'Cart.naver', 'like_count': 5}, {'blog_id': 'actual', 'like_count': 2}], 'real_unresponsive_count': 45}
        path.write_text(json.dumps(original))
        before = path.read_bytes()
        ids = self.repo.import_legacy(self.path)
        result = self.repo.get_run(ids[0])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([r['blog_id'] for r in result['non_buddy_reactors']], ['actual'])
        self.assertEqual(result['non_buddy_reactors'][0]['reaction_status'], '과거자료 미검증')
        self.assertEqual(result['legacy_original'], original)
        self.assertEqual(len(result['excluded_evidence']), 1)
        self.assertIsNone(result['real_unresponsive_count'])
        self.assertFalse(result['comparison_eligible'])
