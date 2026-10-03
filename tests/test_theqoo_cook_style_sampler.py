from scripts.sample_theqoo_cook_style import comment_texts, discover_posts, summarize
import pytest


def test_discovery_skips_notices_foreign_boards_and_duplicate_comment_links():
    html = '''<table class="bd_lst">
      <tr class="notice"><td class="title"><a href="/cook/1">notice</a></td></tr>
      <tr><td class="title"><a href="/event/2">event</a></td></tr>
      <tr><td class="title"><a href="https://elsewhere.example/cook/9">other</a></td></tr>
      <tr><td class="title"><a href="/cook/3?filter_mode=hot">food</a>
          <a href="/cook/3#comment">10</a></td></tr>
      <tr><td class="title"><a href="/cook/4">food two</a></td></tr>
    </table>'''
    assert discover_posts(html, 1) == ["https://theqoo.net/cook/3"]
    assert discover_posts(html, 6) == ["https://theqoo.net/cook/3", "https://theqoo.net/cook/4"]


def test_comment_extraction_uses_actual_ct_schema_and_excludes_author_replies():
    response = {"comment_list": [
        {"ct": "<p>오 좋아요 ㅎㅎ</p>", "srl": "private-id"},
        {"ct": "삭제된 댓글입니다"},
        {"ct": "작성자 답글", "is_writer": True},
    ]}
    texts = comment_texts(response)
    assert texts == ["오 좋아요 ㅎㅎ"]
    stats = summarize(texts)
    assert stats["laughter_or_tears"] == 1
    assert stats["reaction_opening"] == 1
    assert "private-id" not in str(stats)
    assert texts[0] not in str(stats)


def test_access_rejection_and_unknown_schema_are_not_empty_success():
    with pytest.raises(RuntimeError):
        comment_texts({"error": -1, "message": "denied"})
    with pytest.raises(RuntimeError):
        comment_texts({"unexpected": []})
    assert summarize([])["median_characters"] is None
