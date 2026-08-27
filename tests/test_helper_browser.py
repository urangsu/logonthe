"""Fully routed, offline Chromium fixtures. No live Naver or ChatGPT requests."""
import json
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from services.helper_policy import ASSETS, POLICY

URL = "https://m.blog.naver.com/owner/123"
TEXT = "본문의 구체적인 내용을 차분하게 정리해 주셔서 잘 읽었습니다."
BODY = "좋은 산책길에서 나무와 꽃을 관찰한 기록입니다. 길의 조용한 분위기와 나무의 색깔을 자세히 설명해 주었습니다."
HTML = '''<html><head></head><body><div class="se-main-container">BODY</div>
<textarea id="naverComment__write_textarea"></textarea><button class="u_cbox_btn_upload">등록</button>
<input id="unrelated"><ul id="comments"></ul>
<button class="u_likeit_list_button" data-type="like" aria-pressed="false">공감 선택</button>
<script>
window.submitClicks=0;window.likeClicks=0;window.autoObserve=true;
document.querySelector('.u_cbox_btn_upload').onclick=()=>{
 window.submitClicks++;
 if(window.autoObserve){let li=document.createElement('li');li.className='u_cbox_comment u_cbox_type_mine';
 li.dataset.commentNo=String(window.submitClicks);let text=document.createElement('div');text.className='u_cbox_contents';
 text.innerText=document.querySelector('textarea').value;li.append(text);document.querySelector('#comments').append(li);}
 document.querySelector('textarea').value='';};
document.addEventListener('keydown',event=>{if(event.target.id==='naverComment__write_textarea'&&event.key==='Enter')document.querySelector('.u_cbox_btn_upload').click();});
document.querySelector('.u_likeit_list_button').onclick=event=>{window.likeClicks++;event.target.setAttribute('aria-pressed','true');};
</script></body></html>'''.replace("BODY", BODY)


class HelperBrowserTests(unittest.TestCase):
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

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=HTML))
        self.page.add_init_script(path=str(ASSETS / "helper_keyboard.js"))
        self.page.goto(URL)
        self.commands = []
        self.page.expose_binding("__naverHelperCommand", lambda source, command: self.commands.append(command) or True)
        self.install()

    def install(self, draft=None, **extra):
        self.page.evaluate((ASSETS / "helper_policy.js").read_text())
        self.page.evaluate((ASSETS / "helper_panel.js").read_text(), {
            "postKey": "owner:123", "navigationVersion": 1, "url": URL,
            "title": "현재 글", "excerpt": BODY, "policy": POLICY,
            "tokens": json.loads((ASSETS.parent / "ui" / "tokens.json").read_text()),
            "draft": draft or {}, "likeState": "not_liked", **extra,
        })

    def panel(self, selector):
        return self.page.locator("#naver-assistant-helper").locator(selector)

    def insert(self, text=TEXT):
        self.panel("#answer").fill(text)
        self.panel("#insert").click()
        command = next(c for c in reversed(self.commands) if c["action"] == "insert")
        result = self.page.evaluate("data=>window.__naverHelper.insert(data)", {
            "postKey": "owner:123", "navigationVersion": 1,
            "text": text, "previousText": command["previousText"],
        })
        self.page.evaluate("()=>window.__naverHelper.response({postKey:'owner:123',navigationVersion:1})")
        return result

    def test_arrival_has_no_generation_insert_like_submit_or_next(self):
        self.assertEqual(self.page.locator("#naverComment__write_textarea").input_value(), "")
        self.assertEqual(self.page.evaluate("[window.submitClicks,window.likeClicks]"), [0, 0])
        self.assertEqual(self.commands, [])

    def test_chatgpt_button_routes_to_external_browser_command(self):
        self.panel("#chatgpt").click()
        command = next(c for c in self.commands if c["action"] == "open_chatgpt")
        self.assertEqual(command["postKey"], "owner:123")
        self.assertEqual(command["navigationVersion"], 1)

    def test_validation_disables_long_input_without_modifying_answer(self):
        self.panel("#answer").fill("가" * 101)
        self.assertTrue(self.panel("#insert").is_disabled())
        self.assertEqual(self.panel("#answer").input_value(), "가" * 101)

    def test_missing_body_blocks_prompt_until_manual_excerpt(self):
        self.install({"excerpt": "", "answer": ""})
        self.assertTrue(self.panel("#copy").is_disabled())
        self.panel("details").first.locator("summary").click()
        self.panel("#excerpt").fill(BODY)
        self.assertFalse(self.panel("#copy").is_disabled())

    def test_insert_does_not_submit_and_native_click_positive_observation(self):
        self.assertTrue(self.insert()["ok"])
        self.assertEqual(self.page.evaluate("window.submitClicks"), 0)
        self.assertEqual(self.page.locator("#naverComment__write_textarea").input_value(), TEXT)
        self.page.locator(".u_cbox_btn_upload").click()
        self.assertEqual(self.page.evaluate("window.__naverHelper.snapshot().status"), "unknown")
        self.assertTrue(self.page.evaluate("window.__naverHelper.observe()"))
        self.assertEqual(self.page.evaluate("window.__naverHelper.snapshot().status"), "submitted")
        self.assertTrue(self.panel("#insert").is_disabled())

    def test_native_clear_without_own_comment_is_unknown_never_retry(self):
        self.insert()
        self.page.evaluate("window.autoObserve=false")
        self.page.locator(".u_cbox_btn_upload").click()
        self.assertFalse(self.page.evaluate("window.__naverHelper.observe()"))
        self.assertEqual(self.page.evaluate("window.__naverHelper.snapshot().status"), "unknown")
        self.assertEqual(self.page.evaluate("window.submitClicks"), 1)
        self.assertTrue(self.panel("#insert").is_disabled())

    def test_public_response_cannot_promote_submission(self):
        self.page.evaluate("() => window.__naverHelper.response({postKey:'owner:123',navigationVersion:1,status:'submitted'})")
        self.assertNotEqual(self.page.evaluate("window.__naverHelper.snapshot().status"), "submitted")

    def test_existing_matching_own_comment_is_not_new_submission(self):
        self.insert()
        self.page.evaluate("text=>{document.querySelector('#comments').innerHTML='<li class=\"u_cbox_comment u_cbox_type_mine\" data-comment-no=\"1\"><div class=\"u_cbox_contents\"></div></li>';document.querySelector('.u_cbox_contents').innerText=text;window.autoObserve=false}", TEXT)
        self.page.locator(".u_cbox_btn_upload").click()
        self.assertFalse(self.page.evaluate("window.__naverHelper.observe()"))

    def test_stale_post_version_url_and_changed_draft_never_insert(self):
        self.panel("#answer").fill(TEXT)
        base = {"postKey": "owner:123", "navigationVersion": 1, "text": TEXT, "previousText": ""}
        for bad in ({"postKey": "other:456"}, {"navigationVersion": 0}, {"text": "가" * 12}, {"previousText": "stale"}):
            self.assertFalse(self.page.evaluate("data=>window.__naverHelper.insert(data)", {**base, **bad})["ok"])
        self.page.evaluate("history.pushState({},'', '/other/456')")
        self.assertFalse(self.page.evaluate("data=>window.__naverHelper.insert(data)", base)["ok"])
        self.assertEqual(self.page.locator("#naverComment__write_textarea").input_value(), "")

    def test_existing_editor_text_requires_confirmation(self):
        self.page.locator("#naverComment__write_textarea").fill("기존 작성 내용")
        self.panel("#answer").fill(TEXT)
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.panel("#insert").click()
        self.assertFalse(any(c["action"] == "insert" for c in self.commands))
        self.assertEqual(self.page.locator("#naverComment__write_textarea").input_value(), "기존 작성 내용")

    def test_enter_and_ime_commit_never_submit_unrelated_field_unaffected(self):
        self.page.locator("#naverComment__write_textarea").fill(TEXT)
        self.page.locator("#naverComment__write_textarea").press("Enter")
        prevented = self.page.evaluate("""()=>{const e=new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}); document.querySelector('textarea').dispatchEvent(e);return e.defaultPrevented;}""")
        self.assertFalse(prevented)
        self.assertEqual(self.page.evaluate("window.submitClicks"), 0)
        unrelated = self.page.evaluate("""()=>{const e=new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}); document.querySelector('#unrelated').dispatchEvent(e);return e.defaultPrevented;}""")
        self.assertFalse(unrelated)

    def test_like_atomic_off_only_never_toggles_or_opens_summary(self):
        identity = {"postKey": "owner:123", "navigationVersion": 1}
        self.assertEqual(self.page.evaluate("window.likeClicks"), 0)
        self.assertTrue(self.page.evaluate("data=>window.__naverHelper.like(data)", identity)["ok"])
        self.assertFalse(self.page.evaluate("data=>window.__naverHelper.like(data)", identity)["ok"])
        self.assertEqual(self.page.evaluate("window.likeClicks"), 1)
        self.page.locator(".u_likeit_list_button").evaluate("el=>{el.removeAttribute('aria-pressed')}")
        self.assertFalse(self.page.evaluate("data=>window.__naverHelper.like(data)", identity)["ok"])

    def test_next_is_explicit_and_edit_emits_scoped_draft_save(self):
        self.panel("#answer").fill(TEXT)
        self.assertEqual(self.commands[-1]["action"], "save")
        self.assertEqual(self.commands[-1]["snapshot"]["answer"], TEXT)
        self.assertEqual(self.commands[-1]["postKey"], "owner:123")
        self.assertEqual(self.commands[-1]["navigationVersion"], 1)
        self.assertTrue(self.commands[-1]["requestId"])
        self.assertFalse(any(c["action"] == "next" for c in self.commands))
        self.panel("#next").click()
        self.assertEqual(self.commands[-1]["action"], "next")


if __name__ == "__main__":
    unittest.main()
