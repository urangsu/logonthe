import unittest

from services.config import migrate_engagement_audit_recent_posts
from services.my_blog_recent_posts import MyBlogRecentPostService


class TestRecentPostSelection(unittest.TestCase):
    def test_rank_candidates_ignores_dom_order_and_uses_published_time(self):
        candidates = [
            {
                "log_no": "224370000001",
                "title": "예전 대표글",
                "url": "https://m.blog.naver.com/me/224370000001",
                "published_at_ms": 1000,
                "published_text": "2026. 8. 20.",
                "dom_index": 0,
            },
            {
                "log_no": "224399000003",
                "title": "최신글3",
                "url": "https://m.blog.naver.com/me/224399000003",
                "published_at_ms": 4000,
                "published_text": "2026. 9. 1.",
                "dom_index": 8,
            },
            {
                "log_no": "224399000002",
                "title": "최신글2",
                "url": "https://m.blog.naver.com/me/224399000002",
                "published_at_ms": 3000,
                "published_text": "2026. 8. 31.",
                "dom_index": 7,
            },
            {
                "log_no": "224399000001",
                "title": "최신글1",
                "url": "https://m.blog.naver.com/me/224399000001",
                "published_at_ms": 2000,
                "published_text": "2026. 8. 30.",
                "dom_index": 6,
            },
        ]

        posts, ordering = MyBlogRecentPostService._rank_candidates(candidates, max_count=3)

        self.assertEqual(ordering, "published_at")
        self.assertEqual(
            [p["log_no"] for p in posts],
            ["224399000003", "224399000002", "224399000001"],
        )

    def test_rank_candidates_falls_back_to_log_no_when_dates_are_missing(self):
        candidates = [
            {"log_no": "224370000001", "title": "old", "url": "old"},
            {"log_no": "224399000003", "title": "new3", "url": "new3"},
            {"log_no": "224399000001", "title": "new1", "url": "new1"},
            {"log_no": "224399000002", "title": "new2", "url": "new2"},
        ]

        posts, ordering = MyBlogRecentPostService._rank_candidates(candidates, max_count=3)

        self.assertEqual(ordering, "log_no_fallback")
        self.assertEqual(
            [p["log_no"] for p in posts],
            ["224399000003", "224399000002", "224399000001"],
        )

    def test_duplicate_links_keep_richer_candidate(self):
        candidates = [
            {"log_no": "224399000003", "title": "포스트 224399000003", "url": "u"},
            {
                "log_no": "224399000003",
                "title": "실제 최신글 제목",
                "url": "u",
                "published_at_ms": 4000,
                "published_text": "2026. 9. 1.",
            },
        ]

        posts, _ = MyBlogRecentPostService._rank_candidates(candidates, max_count=10)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "실제 최신글 제목")
        self.assertEqual(posts[0]["published_at_ms"], 4000)

    def test_existing_default_five_is_migrated_to_ten(self):
        migrated = migrate_engagement_audit_recent_posts(
            {"engagement_audit_recent_posts": 5}
        )
        self.assertEqual(migrated["engagement_audit_recent_posts"], 10)

    def test_explicit_nonlegacy_count_is_preserved(self):
        migrated = migrate_engagement_audit_recent_posts(
            {"engagement_audit_recent_posts": 12}
        )
        self.assertEqual(migrated["engagement_audit_recent_posts"], 12)


if __name__ == "__main__":
    unittest.main()
