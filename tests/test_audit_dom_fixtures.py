"""Browser fixtures exercise extractor JS; they do NOT certify Naver's live DOM."""
import unittest
from unittest.mock import patch
from pathlib import Path
from playwright.sync_api import sync_playwright
from services.reaction_participant_collector import REACTION_DOM
from services.comment_participant_collector import COMMENT_DOM
from services.buddy_list_collector import BUDDY_DOM, BUDDY_NEXT_DOM
from services.my_blog_recent_posts import MyBlogRecentPostService


class AuditDOMFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        if not Path(cls.playwright.chromium.executable_path).exists():
            cls.playwright.stop()
            raise unittest.SkipTest("Playwright Chromium fixture runtime not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()

    def open_fixture(self, html, url):
        page = self.browser.new_page()
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
        page.goto(url)
        self.addCleanup(page.close)
        return page

    def test_reaction_menu_links_outside_list_never_count(self):
        page = self.open_fixture('''<nav><a href="https://m.blog.naver.com/Cart.naver">Cart</a><a href="https://m.blog.naver.com/outside_person">Menu</a></nav>
            <section><span class="u_likeit_list_count">1</span><ul class="list_sympathy"><li><a href="https://blog.naver.com/real_user">Real</a></li></ul>
            <div class="list_end">마지막입니다</div></section>''', 'https://m.blog.naver.com/SympathyHistoryList.naver?blogId=owner&logNo=123')
        data = page.evaluate(REACTION_DOM, {"blogId": "owner", "logNo": "123"})
        self.assertEqual([row["blog_id"] for row in data["items"]], ["real_user"])
        self.assertTrue(data["terminal"])
        self.assertFalse(page.evaluate(REACTION_DOM, {"blogId": "other", "logNo": "123"})["scopeVerified"])

    def test_comment_body_links_are_not_authors(self):
        page = self.open_fixture('''<div id="cbox_module"><span class="u_cbox_count">2</span>
            <ul><li class="u_cbox_comment" data-comment-no="1"><div class="u_cbox_info"><a class="u_cbox_name" href="https://blog.naver.com/real_user"><span class="u_cbox_nick">Real</span></a></div>
            <div class="u_cbox_contents"><a href="https://blog.naver.com/other_person">body link</a></div></li>
            <li class="u_cbox_comment" data-comment-no="2"><div class="u_cbox_info"><a class="u_cbox_name" href="https://blog.naver.com/real_user">Real</a></div></li></ul>
            <div class="u_cbox_list_end">더 이상 댓글이 없습니다</div></div>''', 'https://m.blog.naver.com/owner/123')
        data = page.evaluate(COMMENT_DOM, {"blogId": "owner", "logNo": "123"})
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["comment_entry_count"], 2)
        self.assertEqual(data["totalLoadedEntries"], 2)
        self.assertTrue(data["terminal"])

    def test_buddy_header_scoped_and_checkbox_never_clicked(self):
        page = self.open_fixture('''<table><thead><tr><th>다른헤더</th><th>등록일</th></tr></thead></table>
            <section><span class="total">이웃 1명</span><table><thead><tr><th>선택</th><th>그룹명</th><th>이웃구분</th><th>이웃블로그</th><th>추가일</th><th>최근글작성일</th><th>새글소식</th></tr></thead>
            <tbody><tr><td><input name="buddySeq" value="1"></td><td>친구</td><td>서로이웃</td><td><a href="https://blog.naver.com/real_user">Real | Title</a></td><td>26.08.01.</td><td>26.08.20.</td><td><input id="toggle" type="checkbox" checked onclick="window.changed=true"></td></tr></tbody></table></section>''', 'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
        data = page.evaluate(BUDDY_DOM)
        self.assertEqual(data["items"][0]["added_date"], "26.08.01.")
        self.assertEqual(data["items"][0]["last_post_date"], "26.08.20.")
        self.assertEqual(data["items"][0]["new_posts_setting"], "unknown")
        self.assertIsNone(page.evaluate("window.changed"))
        self.assertTrue(page.locator('#toggle').is_checked())

    def test_post_windows_5_10_20_preserve_dates_and_do_not_invent_titles(self):
        cards = ''.join(f'<li><a href="https://m.blog.naver.com/owner/{100+i}"><span class="title">공지에 대한 일반글 {i}</span></a><span class="date">2026. 8. 20.</span></li>' for i in range(20))
        page = self.open_fixture('<ul class="list_post">' + cards + '</ul>', 'https://m.blog.naver.com/PostList.naver?blogId=owner')
        for count in (5, 10, 20):
            with patch('services.my_blog_recent_posts.interruptible_wait'):
                posts = MyBlogRecentPostService.fetch_recent_posts(page, 'owner', count)
            self.assertEqual(len(posts), count)
            self.assertEqual(posts[0]['published_at'], '2026-08-20')
            self.assertEqual(posts[0]['published_at_precision'], 'date')
            self.assertFalse(posts.capability_verified)

    def actual_buddy_fixture(self, pager, profile='https://blog.naver.com/real_user'):
        return '''<section><div><table><thead><tr><th></th>
            <th>그룹<select><option>전체</option><option>그룹A</option></select></th>
            <th>이웃<select><option>전체</option><option>이웃</option><option>서로이웃</option></select></th>
            <th>이웃</th><th>새글소식<select><option>전체</option><option>새글소식 ON</option><option>새글소식 OFF</option></select></th>
            <th>최근 글</th><th>이웃추가일</th></tr></thead><tbody><tr>
            <td><input type="checkbox" name="buddyBlogNo" value="123"></td><td>그룹A</td><td>서로이웃</td>
            <td><a href="''' + profile + '''">Real | Title</a></td><td><input id="notify" type="checkbox" checked onclick="window.changed=true"></td>
            <td>26.08.20.</td><td>26.08.01.</td></tr></tbody></table></div>''' + pager + '</section>'

    def test_actual_buddyblogno_headers_and_nested_pager(self):
        page = self.open_fixture(self.actual_buddy_fixture('<div class="paginate"><strong>1</strong><a href="#" onclick="window.pageNo=2">2</a><a href="#">3</a><a href="#">4</a><a href="#">5</a></div>'),
            'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
        data = page.evaluate(BUDDY_DOM)
        self.assertTrue(data['scopeVerified'])
        self.assertEqual(data['items'][0]['group_name'], '그룹A')
        self.assertEqual(data['items'][0]['buddy_type'], '서로이웃')
        self.assertEqual(data['items'][0]['added_date'], '26.08.01.')
        self.assertEqual(data['items'][0]['last_post_date'], '26.08.20.')
        self.assertEqual(data['items'][0]['new_posts_setting'], 'unknown')
        self.assertEqual(data['nextPage'], 2)
        self.assertFalse(data['terminal'])
        self.assertIsNone(page.evaluate('window.changed'))

    def test_terminal_pager_requires_no_hidden_next_or_ambiguous_control(self):
        for tail in ('<a href="#"><img alt="다음" src="data:,"></a>', '<a href="#" aria-label="알수없는 이동">...</a>'):
            pager = '<div class="paginate"><a href="#">1</a><a href="#">2</a><a href="#">3</a><a href="#">4</a><strong>5</strong>' + tail + '</div>'
            page = self.open_fixture(self.actual_buddy_fixture(pager), 'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
            self.assertFalse(page.evaluate(BUDDY_DOM)['terminal'])

    def test_unverified_identity_link_fails_closed_in_actual_table(self):
        page = self.open_fixture(self.actual_buddy_fixture('<div class="paginate"><strong>1</strong></div>', 'javascript:openBlog(123)'),
            'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
        data = page.evaluate(BUDDY_DOM)
        self.assertTrue(data['scopeVerified'])
        self.assertEqual(data['items'], [])
        self.assertEqual(data['unresolvedEntries'], 1)

    def test_pager_click_targets_only_next_observed_page(self):
        page = self.open_fixture(self.actual_buddy_fixture('<div class="paginate"><strong>1</strong><a href="#" onclick="window.pageNo=2">2</a></div>'),
            'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
        self.assertFalse(page.evaluate(BUDDY_NEXT_DOM, 5))
        self.assertIsNone(page.evaluate('window.pageNo'))
        self.assertTrue(page.evaluate(BUDDY_NEXT_DOM, 2))
        self.assertEqual(page.evaluate('window.pageNo'), 2)
        self.assertIsNone(page.evaluate('window.changed'))

    def test_last_page_in_initial_contiguous_range_has_terminal_evidence(self):
        pager = '<div class="paginate"><a href="#">1</a><a href="#">2</a><a href="#">3</a><a href="#">4</a><strong>5</strong></div>'
        page = self.open_fixture(self.actual_buddy_fixture(pager), 'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
        data = page.evaluate(BUDDY_DOM)
        self.assertTrue(data['terminal'])
        self.assertIsNone(data['nextPage'])

    def test_later_pagination_window_does_not_prove_terminal_by_itself(self):
        pager = '<div class="paginate"><a href="#">6</a><a href="#">7</a><a href="#">8</a><a href="#">9</a><strong>10</strong></div>'
        page = self.open_fixture(self.actual_buddy_fixture(pager), 'https://admin.blog.naver.com/BuddyListManage.naver?blogId=owner')
        self.assertFalse(page.evaluate(BUDDY_DOM)['terminal'])
