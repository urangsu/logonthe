"""Local HTML fixtures only; no login, navigation, or external interaction."""
import unittest

from playwright.sync_api import sync_playwright

from services.blog_neighbor_count import _EXTRACT_JS, extract_neighbor_count
from services.post_eligibility import _VISITORS_JS, extract_daily_visitors, ProfileMetricsReader
from naver.resolver import MobileDOMResolver


class TestPostEligibilityDOM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_buddy_span_visibility_and_conflict(self):
        self.page.set_content('''<span class="buddy__UnXlf">2,851명의 이웃</span>
            <span class="buddy__hidden" style="display:none">900명의 이웃</span>
            <span>서로이웃 99명</span><button>이웃추가</button>''')
        data = self.page.evaluate(_EXTRACT_JS)
        self.assertEqual(extract_neighbor_count(data["candidates"])[0], 2851)
        self.page.set_content('''<span class="buddy__a">2,851명의 이웃</span>
            <span class="buddy__b">100명의 이웃</span>''')
        self.assertEqual(extract_neighbor_count(self.page.evaluate(_EXTRACT_JS)["candidates"])[2],
                         "conflicting_neighbor_counts")

    def test_delayed_profile_render_and_private_marker(self):
        self.page.set_content('''<div id="profile"></div><script>
            setTimeout(() => document.getElementById('profile').innerHTML =
              '<span class="ProfileBlock__text_new">이웃 613명</span>', 100);
            </script>''')
        self.assertIsNone(extract_neighbor_count(self.page.evaluate(_EXTRACT_JS)["candidates"])[0])
        self.page.locator("span[class*='ProfileBlock__text']").wait_for(state="visible")
        self.assertEqual(extract_neighbor_count(self.page.evaluate(_EXTRACT_JS)["candidates"])[0], 613)
        self.page.set_content('<div>이웃 비공개</div>')
        self.assertTrue(self.page.evaluate(_EXTRACT_JS)["notPublic"])

    def test_today_total_and_hidden_visitor_values(self):
        self.page.set_content('''<section class="visitor__stats"><span>오늘 250</span>
            <span>전체 900,000</span><span style="visibility:hidden">오늘 999</span></section>
            <article>오늘 800명을 만났어요</article>''')
        self.assertEqual(extract_daily_visitors(self.page.evaluate(_VISITORS_JS))[0], 250)

    def test_canonical_author_mismatch_is_rejected(self):
        self.page.set_content('''<link rel="canonical" href="https://m.blog.naver.com/another">
            <span class="buddy__a">100명의 이웃</span>''')
        from types import SimpleNamespace
        adapter = SimpleNamespace(url="https://m.blog.naver.com/a", evaluate=self.page.evaluate)
        result = ProfileMetricsReader()._read(adapter, "a", True, False)
        self.assertEqual(result["neighbors"].error, "profile_owner_mismatch")

    def test_unrecognized_counter_class_and_split_labels(self):
        self.page.set_content('''<div class="count__newHash"><dl>
            <dt>오늘</dt><dd>124</dd><dt>전체</dt><dd>90,000</dd>
            </dl></div><article>오늘 999명을 만났어요</article>''')
        self.assertEqual(extract_daily_visitors(self.page.evaluate(_VISITORS_JS))[0], 124)

    def test_unrecognized_counter_single_today_label(self):
        self.page.set_content('<span class="count__newHash">TODAY 2, 451</span>')
        self.assertEqual(extract_daily_visitors(self.page.evaluate(_VISITORS_JS))[0], 2451)

    def test_plain_prose_and_total_only_are_not_today_counts(self):
        self.page.set_content('<div>오늘 800명을 만났어요</div><span>전체 123,456</span>')
        self.assertIsNone(extract_daily_visitors(self.page.evaluate(_VISITORS_JS))[0])

    def test_missing_malformed_and_private_are_distinct(self):
        self.assertEqual(extract_daily_visitors([])[2], "daily_visitor_region_not_found")
        self.assertEqual(extract_daily_visitors(["오늘 -"])[2], "daily_visitor_number_parse_failed")
        self.assertEqual(extract_daily_visitors(["방문자 비공개"])[2], "not_public")

    def test_reaction_count_ignores_empty_hidden_and_comment_counts(self):
        self.page.set_content('''<span class="u_likeit_text _count num" style="display:none">999</span>
            <span class="u_likeit_text _count num"></span>
            <a class="u_likeit_button"><span class="u_likeit_icon __reaction__like" style="z-index:4"></span>
            <span class="u_likeit_text _count num">6</span></a>
            <button class="comment"><span class="_count num">4</span></button>''')
        self.assertEqual(MobileDOMResolver.get_like_count_text(self.page), "6")

    def test_reaction_count_inside_legacy_frame(self):
        self.page.set_content('''<iframe srcdoc='<span class="u_likeit_icon __reaction__like"></span>
            <span class="u_likeit_text _count num">6</span>'></iframe>''')
        self.page.frame_locator('iframe').locator('.u_likeit_text').wait_for(state='visible')
        self.assertEqual(MobileDOMResolver.get_like_count_text(self.page), "6")

    def test_reaction_zero_missing_and_conflict_remain_distinct(self):
        self.page.set_content('<span class="u_likeit_text _count num">0</span>')
        self.assertEqual(MobileDOMResolver.get_like_count_text(self.page), "0")
        self.page.set_content('<span class="u_likeit_text _count num"></span>')
        self.assertIsNone(MobileDOMResolver.get_like_count_text(self.page))
        self.page.set_content('''<span class="u_likeit_text _count num">6</span>
            <span class="u_likeit_text _count num">9</span>''')
        self.assertIsNone(MobileDOMResolver.get_like_count_text(self.page))
