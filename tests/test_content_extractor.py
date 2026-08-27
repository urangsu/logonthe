import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.models import FeedPost, FeedSourceType
from naver.content_extractor import ContentContextExtractor


class ContentExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        if not Path(cls.playwright.chromium.executable_path).exists():
            cls.playwright.stop()
            raise unittest.SkipTest("Chromium fixture runtime not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_extracts_old_naver_iframe_content(self):
        page = self.browser.new_page()
        body = '<div id="postViewArea"><p>오래된 네이버 레이아웃에서도 본문이 iframe 안에 표시됩니다.</p><p>장소와 메뉴에 대한 충분한 설명이 있어 댓글 작성에 사용할 수 있습니다.</p></div>'
        srcdoc = body.replace('&', '&amp;').replace('"', '&quot;')
        page.set_content(f'<iframe id="mainFrame" srcdoc="{srcdoc}"></iframe>')
        post = FeedPost("owner:1", FeedSourceType.DIRECT, "https://m.blog.naver.com/owner/1", title="제목")
        context = ContentContextExtractor.extract(page, post, max_chars=700)
        page.close()
        self.assertIn("오래된 네이버 레이아웃", context.excerpt)

    def test_falls_back_to_visible_body_when_container_is_missing(self):
        page = self.browser.new_page()
        page.set_content('<main>본문 후보가 별도 컨테이너 없이 표시됩니다. 장소와 메뉴에 대한 설명이 충분히 길게 들어 있습니다.</main>')
        post = FeedPost("owner:2", FeedSourceType.DIRECT, "https://m.blog.naver.com/owner/2", title="제목")
        context = ContentContextExtractor.extract(page, post, max_chars=700)
        page.close()
        self.assertIn("본문 후보", context.excerpt)


if __name__ == "__main__":
    unittest.main()
