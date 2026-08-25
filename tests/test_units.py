import unittest
import os
import shutil
import tempfile
from app.models import (
    FeedSourceType, LikeState, CommentSubmitState, FeedPost, PostProcessResult,
    LikeProcessResult, CommentProcessResult, WorkerCommand, WorkerCommandType
)
from app.state import StateManager, FeedState
from naver.resolver import MobileDOMResolver
from naver.url_utils import parse_blog_post_url, canonicalize_post_url, build_post_key, extract_canonical_post
from naver.count_parser import parse_compact_count
from naver.content_extractor import ContentContextExtractor
from services.draft import DraftService
from services.contextual_draft import ContextualDraftEngine
from services.comments.intents import ReactionIntent, FirstPersonIntent, CommentCandidate
from services.comments.composer import HumanLikeComposerV31
from services.comments.validators import PositiveSafetyValidator
from services.blog_popularity import BlogPopularityService, DailyVisitorResult
from services.like_eligibility import LikeEligibilityService, LikeEligibility
from services.config import ConfigService, migrate_config_v1_to_v2
from services.history import HistoryStore
from services.pacing import PacingService, PacingKind
from services.ai_prompt import AIPromptBuilder
from services.clipboard_bridge import ClipboardCommandBridge
from services.gemini_web import GeminiWebBridge
from services.gemini_existing_chrome import ExistingChromeGeminiBridge


class TestResolverAliases(unittest.TestCase):
    def test_resolver_method_aliases(self):
        self.assertTrue(hasattr(MobileDOMResolver, "get_comment_button"))
        self.assertTrue(hasattr(MobileDOMResolver, "get_comment_open_button"))
        self.assertTrue(hasattr(MobileDOMResolver, "get_comment_write_box"))
        self.assertTrue(hasattr(MobileDOMResolver, "get_comment_editor"))
        self.assertTrue(hasattr(MobileDOMResolver, "get_secret_checkbox"))
        self.assertTrue(hasattr(MobileDOMResolver, "get_secret_comment_checkbox"))


class TestCountParser(unittest.TestCase):
    def test_parse_compact_counts(self):
        self.assertEqual(parse_compact_count("0"), 0)
        self.assertEqual(parse_compact_count("12"), 12)
        self.assertEqual(parse_compact_count("999"), 999)
        self.assertEqual(parse_compact_count("999+"), 999)
        self.assertEqual(parse_compact_count("1,234"), 1234)
        self.assertEqual(parse_compact_count("1천"), 1000)
        self.assertEqual(parse_compact_count("1.2천"), 1200)
        self.assertEqual(parse_compact_count("1만"), 10000)
        self.assertEqual(parse_compact_count("1.2만"), 12000)
        self.assertEqual(parse_compact_count("10K"), 10000)
        self.assertEqual(parse_compact_count("공감 999"), 999)
        self.assertEqual(parse_compact_count("오늘 3,241"), 3241)


class TestPositiveSafetyValidator(unittest.TestCase):
    def test_rejection_rules(self):
        # 1. Negative word
        c1 = CommentCandidate(body="생각보다 양이 푸짐하고 맛있어 보이네요 :)", category="FOOD", reaction_intent=ReactionIntent.PRAISE, first_person_intent=FirstPersonIntent.NONE, subject="파스타", template_id="t1")
        valid, r = PositiveSafetyValidator.validate_candidate(c1)
        self.assertFalse(valid)
        self.assertIn("banned_negative", r)

        # 2. Judgment / Comparison
        c2 = CommentCandidate(body="저라면 이 메뉴 대신 다른 걸 시켰을 것 같아요", category="FOOD", reaction_intent=ReactionIntent.PRAISE, first_person_intent=FirstPersonIntent.NONE, subject="피자", template_id="t2")
        valid, r = PositiveSafetyValidator.validate_candidate(c2)
        self.assertFalse(valid)
        self.assertIn("banned_judgment", r)

        # 3. Macro phrase
        c3 = CommentCandidate(body="유익한 정보 감사합니다. 잘 보고 갑니다 :)", category="GENERAL", reaction_intent=ReactionIntent.PRAISE, first_person_intent=FirstPersonIntent.NONE, subject="", template_id="t3")
        valid, r = PositiveSafetyValidator.validate_candidate(c3)
        self.assertFalse(valid)
        self.assertIn("banned_macro", r)

        # 4. Fake past experience
        c4 = CommentCandidate(body="저도 예전에 가봤는데 분위기가 정말 좋더라고요", category="CAFE", reaction_intent=ReactionIntent.PRAISE, first_person_intent=FirstPersonIntent.NONE, subject="카페", template_id="t4")
        valid, r = PositiveSafetyValidator.validate_candidate(c4)
        self.assertFalse(valid)
        self.assertIn("banned_fake_experience", r)

        # 5. Valid positive candidate
        c5 = CommentCandidate(body="딸기라떼 색감이 너무 예쁘네요. 저도 한번 마셔보고 싶어요 :)", category="CAFE", reaction_intent=ReactionIntent.TRY_INTENT, first_person_intent=FirstPersonIntent.WANT_TO_DRINK, subject="딸기라떼", template_id="t5")
        valid, r = PositiveSafetyValidator.validate_candidate(c5)
        self.assertTrue(valid)


class TestHumanLikeComposerV31(unittest.TestCase):
    def test_composer_food_and_cafe(self):
        res = ContextualDraftEngine.generate(
            title="광양 중마동 장어덮밥 히츠마부시 맛집 후기",
            excerpt="부모님 모시고 다녀왔는데 장어가 정말 부드럽고 오차즈케로 마무리하니 깔끔했습니다."
        )
        self.assertEqual(res.category, "FOOD")
        self.assertTrue(len(res.body) > 10)
        self.assertNotIn("생각보다", res.body)
        self.assertNotIn("저라면", res.body)
        self.assertNotIn("잘 보고 갑니다", res.body)

    def test_hobby_goods_spongebob_keyring(self):
        # GS25 스폰지밥 랜덤키링 근육빵빵 - 빵 때문에 FOOD로 오분류되지 않고 HOBBY_GOODS로 정확히 분류되는지 검증
        res = ContextualDraftEngine.generate(
            title="GS25 스폰지밥 랜덤키링 근육빵빵 내돈내산 후기",
            excerpt="집 앞 편의점 갔다가 스폰지밥 랜덤키링이 있길래 사봤는데 근육빵빵 스폰지밥이 나왔습니다. 너무 귀엽네요."
        )
        self.assertEqual(res.category, "HOBBY_GOODS")
        self.assertTrue(len(res.body) > 10)
        self.assertNotIn("음식", res.body)
        self.assertNotIn("맛있어", res.body)
        self.assertTrue("귀엽" in res.body or "뽑아" in res.body or "굿즈" in res.body or "키링" in res.body or "스폰지밥" in res.body)

    def test_composer_praise_boost(self):
        res = ContextualDraftEngine.generate(
            title="성수동 신상 디저트 카페 딸기라떼 맛집",
            excerpt="분위기가 너무 아늑하고 딸기라떼와 크로플 조합이 최고였습니다.",
            praise_boost=True
        )
        self.assertEqual(res.category, "CAFE")
        self.assertTrue(len(res.body) > 10)


class TestUrlUtils(unittest.TestCase):
    def test_parse_blog_post_url(self):
        res = parse_blog_post_url("https://m.blog.naver.com/travelmeow/224388639668")
        self.assertEqual(res, ("travelmeow", "224388639668"))

        res = parse_blog_post_url("https://blog.naver.com/travelmeow/224388639668")
        self.assertEqual(res, ("travelmeow", "224388639668"))

        res = parse_blog_post_url("https://blog.naver.com/PostView.naver?blogId=travelmeow&logNo=224388639668&redirect=Dlog")
        self.assertEqual(res, ("travelmeow", "224388639668"))

    def test_canonicalize_post_url(self):
        url = canonicalize_post_url("travelmeow", "224388639668")
        self.assertEqual(url, "https://m.blog.naver.com/travelmeow/224388639668")

    def test_extract_canonical_post(self):
        post = extract_canonical_post(
            "https://blog.naver.com/travelmeow/224388639668?extra=1",
            FeedSourceType.NEIGHBOR,
            title="테스트 글",
            author="여행냥이"
        )
        self.assertIsNotNone(post)
        self.assertEqual(post.key, "travelmeow:224388639668")
        self.assertEqual(post.url, "https://m.blog.naver.com/travelmeow/224388639668")
        self.assertEqual(post.title, "테스트 글")
        self.assertEqual(post.author, "여행냥이")


class TestLikeEligibility(unittest.TestCase):
    def test_like_guard_cached_visitors(self):
        BlogPopularityService.clear_cache()
        BlogPopularityService._cache["popular_blog"] = DailyVisitorResult(value=15000, raw_text="오늘 15,000", confidence="high")
        BlogPopularityService._cache["normal_blog"] = DailyVisitorResult(value=1500, raw_text="오늘 1,500", confidence="high")

        cfg = {
            "like_popularity_guard_enabled": True,
            "like_count_skip_threshold": 999,
            "daily_visitor_guard_enabled": True,
            "daily_visitor_skip_threshold": 10000,
            "daily_visitor_unknown_policy": "skip_like"
        }

        post_pop = FeedPost(key="popular_blog:100", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/popular_blog/100")
        post_norm = FeedPost(key="normal_blog:200", source=FeedSourceType.NEIGHBOR, url="https://m.blog.naver.com/normal_blog/200")

        res_pop = LikeEligibilityService.evaluate(None, None, post_pop, cfg)
        self.assertFalse(res_pop.eligible)
        self.assertEqual(res_pop.status, LikeEligibility.SKIP_DAILY_VISITORS)

        res_norm = LikeEligibilityService.evaluate(None, None, post_norm, cfg)
        self.assertTrue(res_norm.eligible)
        self.assertEqual(res_norm.status, LikeEligibility.ELIGIBLE)


class TestContentExtractorAndAIPrompt(unittest.TestCase):
    def test_clean_text_boilerplate(self):
        raw = "이웃추가 공감 12 댓글 3 이번 주말 강릉 여행 다녀왔습니다. 공유하기 NAVER 블로그"
        cleaned = ContentContextExtractor.clean_text(raw, max_chars=100)
        self.assertNotIn("이웃추가", cleaned)
        self.assertNotIn("공유하기", cleaned)
        self.assertIn("강릉 여행", cleaned)

    def test_ai_prompt_builder_warm(self):
        prompt_with_excerpt = AIPromptBuilder.build(
            title="강릉 순두부 맛집 탐방",
            excerpt="초당순두부 마을에서 짬뽕순두부를 먹었는데 국물이 정말 얼큰했습니다."
        )
        self.assertIn("강릉 순두부 맛집 탐방", prompt_with_excerpt)
        self.assertIn("짬뽕순두부", prompt_with_excerpt)
        self.assertIn("절대 금지 규칙", prompt_with_excerpt)


class TestDraftServiceSuffix(unittest.TestCase):
    def test_resolve_suffix(self):
        cfg = {
            "general_suffix": "오늘도 좋은 하루 보내세요 :)",
            "recommendation_suffix_enabled": True,
            "recommendation_suffix": "시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)"
        }
        s_neigh = DraftService.resolve_suffix(FeedSourceType.NEIGHBOR, cfg)
        self.assertEqual(s_neigh, "오늘도 좋은 하루 보내세요 :)")

        s_recom = DraftService.resolve_suffix(FeedSourceType.RECOMMENDATION, cfg)
        self.assertEqual(s_recom, "시간 되실 때 제 블로그에도 편하게 한 번 놀러 와주세요 :)")

    def test_compose_body_and_suffix(self):
        res = DraftService.compose_body_and_suffix("사진이 너무 예쁘네요!", "오늘도 좋은 하루 보내세요 :)")
        self.assertEqual(res, "사진이 너무 예쁘네요!\n\n오늘도 좋은 하루 보내세요 :)")


class TestPacingService(unittest.TestCase):
    def test_pacing_disabled(self):
        cfg = {"pacing_enabled": False}
        pacing = PacingService(cfg)
        res_act = pacing.wait_action()
        self.assertEqual(res_act.seconds, 0.0)
        self.assertFalse(res_act.interrupted)


class TestClipboardCommandBridge(unittest.TestCase):
    def test_bridge_queue(self):
        bridge = ClipboardCommandBridge()
        bridge.send_apply_clipboard_comment("Gemini가 작성한 정성스러운 댓글입니다.")
        cmd = bridge.pop_command()
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.kind, WorkerCommandType.APPLY_CLIPBOARD_COMMENT)
        self.assertEqual(cmd.text, "Gemini가 작성한 정성스러운 댓글입니다.")

        self.assertIsNone(bridge.pop_command())


class TestConfigAndHistory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cfg_file = os.path.join(self.test_dir, "config.json")
        self.hist_file = os.path.join(self.test_dir, "history.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_config_migration(self):
        old_v1 = {
            "max_pages": 3,
            "comment_template": "테스트 템플릿",
            "fixed_suffix": "기존 꼬리말"
        }
        migrated = migrate_config_v1_to_v2(old_v1)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["max_feed_items"], 30)
        self.assertEqual(migrated["general_suffix"], "기존 꼬리말")
        self.assertTrue(migrated["recommendation_suffix_enabled"])
        self.assertEqual(migrated["gemini_browser_mode"], "existing_chrome_mac")

    def test_history_store(self):
        store = HistoryStore(self.hist_file)
        post = FeedPost(
            key="user1:12345",
            source=FeedSourceType.NEIGHBOR,
            url="https://m.blog.naver.com/user1/12345"
        )
        res = PostProcessResult(
            post=post,
            like_result=LikeProcessResult(state_before=LikeState.NOT_LIKED, action_taken=True, state_after=LikeState.LIKED),
            comment_result=CommentProcessResult(status=CommentSubmitState.SUBMITTED, submitted_text="댓글 작성됨")
        )
        store.record_result(res)

        self.assertTrue(store.is_processed("user1:12345"))
        self.assertTrue(store.is_comment_submitted("user1:12345"))
        self.assertFalse(store.is_comment_submitted("user2:99999"))


class TestStateManager(unittest.TestCase):
    def test_state_updates(self):
        sm = StateManager()
        sm.reset(total_targets=10)
        self.assertEqual(sm.state.total_target_count, 10)

        sm.update(
            new_state=FeedState.LIKING,
            inc_like=True,
            inc_processed=True,
            current_post_title="테스트 제목",
            current_ai_prompt="프롬프트 텍스트",
            ai_clipboard_ready=True
        )
        self.assertEqual(sm.state.current_state, FeedState.LIKING)
        self.assertEqual(sm.state.likes_count, 1)
        self.assertEqual(sm.state.processed_count, 1)
        self.assertEqual(sm.state.current_post_title, "테스트 제목")
        self.assertEqual(sm.state.current_ai_prompt, "프롬프트 텍스트")
        self.assertTrue(sm.state.ai_clipboard_ready)


if __name__ == "__main__":
    unittest.main()
