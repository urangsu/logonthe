import pytest
from playwright.sync_api import sync_playwright

from app.processor import check_food_relevance
from naver.content_extractor import ContentContextExtractor
from services.comments.community_rhythm import CommentDraftInspector
from services.comments.style_normalizer import CommentStyleNormalizer
from services.reaction_planner import ReactionContextPlanner


@pytest.mark.parametrize("preview_class", ["se-oglink", "se-module-oglink", "se_oglink"])
def test_link_preview_is_not_current_post_context(preview_class):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(f'''<article class="se-main-container">
              <p>게국지를 주문했는데 기다린 보람이 있었어요</p>
              <div class="{preview_class}"><a>탕수육 볶음밥 맛집 자금성</a></div>
              <p>간장게장도 <a href="/menu">같이 주문했어요</a></p>
              <p hidden>피자 파스타 리조또 맛집</p>
              <p>웨이팅이 길어서 조금 지쳤어요</p>
            </article>''')
            candidates = ContentContextExtractor._collect_candidates(page, 1000)
            assert candidates
            for _, text in candidates:
                assert "볶음밥" not in text
                assert "피자" not in text
                assert "같이 주문했어요" in text
                assert "지쳤어요" in text
        finally:
            browser.close()


def test_food_reaction_not_forced_to_say_combination():
    relevant, _ = check_food_relevance(
        "트러플 소스 피자 완전 땡기네요", "트러플 소스 피자를 주문했어요",
        ["스테이크", "파스타", "리조또"], "combination_curiosity")
    assert relevant


def test_mode_keyword_alone_is_not_food_evidence():
    relevant, _ = check_food_relevance(
        "이 조합 궁금하네요", "주차장이 넓고 매장이 조용해요",
        [], "combination_curiosity")
    assert not relevant


@pytest.mark.parametrize("draft,context", [
    ("모르타델라에 피스타치오라니 궁금해요", "피자에 모르타델라와 피스타치오가 올라가요"),
    ("크리스피 슈림프 창펀 먹어보고 싶네요", "셰프 추천 메뉴는 크리스피 슈림프 창펀이에요"),
    ("망고시루 먹어보고 싶네요", "생망고와 우유크림이 들어간 망고시루예요"),
    ("껍데기 구워 먹으면 맛있겠어요", "야외 테이블에서 껍데기를 구워 먹었어요"),
])
def test_logged_menu_reactions_are_not_rejected(draft, context):
    assert check_food_relevance(draft, context, [], "food_desire")[0]


def test_menu_dictionary_never_substitutes_for_article_evidence():
    assert not check_food_relevance(
        "망고시루 먹어보고 싶네요", "피자를 주문했어요", ["피자"], "food_desire")[0]


def test_adverb_is_not_a_menu_attribute_target():
    result = CommentDraftInspector.inspect(
        "바로 무쳐주는 매콤한 보쌈김치 너무 땡기네요",
        excerpt="겉절이는 매콤하고 바로 무쳐주는 보쌈김치예요. 고기는 담백해요")
    assert result.passed, (result.code, result.feedback)


def test_independent_main_menu_attribute_is_not_transferred():
    result = CommentDraftInspector.inspect(
        "매콤한 닭갈비가 땡기네요",
        excerpt="겉절이는 매콤해요. 닭갈비도 매콤하게 볶았어요")
    assert result.passed, (result.code, result.feedback)


def test_distinct_emoji_survives_history():
    result = CommentStyleNormalizer.normalize_style(
        "트러플 피자가 눈에 들어오네요 👀", recent_comments=["맛있겠네요 😋"],
        allow_emoji_sampling=False)
    assert result.emoji_action == "kept"


def test_zero_history_window_does_not_mean_entire_history():
    result = CommentStyleNormalizer.normalize_style(
        "피자가 눈에 들어오네요 👀", recent_comments=["궁금하네요 👀"],
        emoji_history_window=0, allow_emoji_sampling=False)
    assert result.emoji_action == "kept"


def test_food_disappointment_takes_priority_over_appetite():
    plan = ReactionContextPlanner.plan(
        "트러플 피자 후기", "트러플 피자를 주문했는데 식어서 아쉬웠어요")
    assert plan.reaction_mode == "writer_feeling"
    assert check_food_relevance(
        "기다렸는데 식어서 아쉬우셨겠어요", "피자가 식어서 아쉬웠어요",
        ["피자"], "writer_feeling")[0]


def test_no_disappointment_is_not_negative_feeling():
    plan = ReactionContextPlanner.plan("트러플 피자 후기", "트러플 피자는 아쉬움 없이 맛있게 먹었어요")
    assert plan.reaction_mode != "writer_feeling"


def test_mixed_review_keeps_both_feelings():
    plan = ReactionContextPlanner.plan("트러플 피자 후기", "트러플 피자가 식어서 아쉬웠지만 맛있었어요")
    assert "함께 존중" in plan.reaction_instruction


def test_real_attribute_transfer_in_one_sentence_still_fails():
    result = CommentDraftInspector.inspect(
        "갈비탕이 아삭해서 맛있겠네요",
        excerpt="깍두기는 아삭하고 갈비탕은 담백해요")
    assert result.code == "mismatched_attribute_target"


def test_adverb_does_not_hide_real_attribute_transfer():
    result = CommentDraftInspector.inspect(
        "바로 매콤한 갈비탕이 땡기네요",
        excerpt="겉절이가 매콤하고 갈비탕은 담백해요")
    assert result.code == "mismatched_attribute_target"


def test_iframe_body_beats_outer_page_navigation():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<nav>' + '다른 블로그 추천 파스타 메뉴입니다 ' * 40 + '</nav><iframe></iframe>')
            page.frames[1].set_content('<div id="postViewArea"><p>게국지는 오래 기다린 보람이 있었고 간장게장도 주문했어요</p></div>')
            candidates = ContentContextExtractor._collect_candidates(page, 1000)
            assert candidates
            assert all("파스타" not in text for _, text in candidates)
        finally:
            browser.close()
