import unittest
from unittest.mock import MagicMock
from app.models import FeedPost, FeedSourceType
from app.errors import PostNavigationMismatchError
from naver.target_guard import TargetPostGuard


class TestTargetPostGuard(unittest.TestCase):
    def setUp(self):
        self.expected_post = FeedPost(
            key="travelmeow:224388639668",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/travelmeow/224388639668"
        )

    def test_guard_pass_when_url_matches(self):
        page_mock = MagicMock()
        page_mock.url = "https://m.blog.naver.com/travelmeow/224388639668?trackingCode=feed"
        # Mismatch가 발생하지 않고 통과해야 함
        TargetPostGuard.verify(page_mock, self.expected_post)

    def test_guard_raises_on_target_mismatch(self):
        page_mock = MagicMock()
        # 다른 글(A 글)이 열려 있는 상태에서 B 글 작업 시도 시 차단
        page_mock.url = "https://m.blog.naver.com/otheruser/111122223333"
        with self.assertRaises(PostNavigationMismatchError):
            TargetPostGuard.verify(page_mock, self.expected_post)

    def test_guard_raises_on_invalid_or_none_page(self):
        with self.assertRaises(PostNavigationMismatchError):
            TargetPostGuard.verify(None, self.expected_post)

        page_mock = MagicMock()
        page_mock.url = "https://www.naver.com"
        with self.assertRaises(PostNavigationMismatchError):
            TargetPostGuard.verify(page_mock, self.expected_post)


if __name__ == "__main__":
    unittest.main()
