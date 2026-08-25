import unittest
from naver.resolver import MobileDOMResolver
from app.errors import (
    BotError, FatalSessionError, RecoverablePostError,
    PostNavigationMismatchError, BrowserDisconnectedError
)


class TestResolverAPIContract(unittest.TestCase):
    """MobileDOMResolver 인터페이스 계약 불변성 검증"""

    def test_canonical_resolver_methods_exist(self):
        canonical_methods = [
            "get_feed_cards",
            "get_card_post_link",
            "get_card_author",
            "get_card_title",
            "get_post_title",
            "get_post_content_locator",
            "get_like_button",
            "get_like_count_text",
            "get_comment_button",
            "get_comment_write_box",
            "get_comment_editor",
            "get_secret_comment_checkbox",
            "get_comment_submit_button"
        ]
        for name in canonical_methods:
            method = getattr(MobileDOMResolver, name, None)
            self.assertIsNotNone(method, f"Resolver missing canonical method: '{name}'")
            self.assertTrue(callable(method), f"Resolver attribute '{name}' is not callable")

    def test_error_taxonomy_hierarchy(self):
        self.assertTrue(issubclass(FatalSessionError, BotError))
        self.assertTrue(issubclass(RecoverablePostError, BotError))
        self.assertTrue(issubclass(PostNavigationMismatchError, RecoverablePostError))
        self.assertTrue(issubclass(BrowserDisconnectedError, FatalSessionError))


if __name__ == "__main__":
    unittest.main()
