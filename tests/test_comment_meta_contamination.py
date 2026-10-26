import pytest
import json
from pathlib import Path

from services.comments.community_rhythm import ResponseContaminationGate, FinalQualityGate
from services.draft import DraftService


META_RESPONSES = [
    "다른 톤이나 다른 포인트(예: 부모님 모시고 가기 좋은 점, 거북목 케어 등)에 맞춘 댓글도 필요하시면 말씀해 주세요!",
    "짜장 소스 넉넉하고 만두 바삭해서 같이 먹으면 진짜 꿀조합이겠어요 😋\n다른 톤이나 군만두 양에 초점을 맞춘 댓글도 필요하신가요?",
    "다른 톤으로도 써드릴까요? 만두가 바삭해 보여요~",
    "만두가 바삭해 보여요~ 원하시면 댓글을 더 짧게 작성해 드릴게요",
    "댓글을 다른 말투로 수정해 드릴까요?",
    "다른 톤이나\n군만두 양에 초점을 맞춘 댓글도 필요하신가요?",
    "다른\u200b 톤으로도 써드릴까요?",
    "에스카르고 식감이 골뱅이랑 비슷하다니 저두 한번 도전해보고 싶네요 ㅎㅎ\n이 느낌이 괜찮으신가요, 아니면 와인이나 디저트(크림 브륄레) 쪽으로 포인트를 바꿔볼까요?",
    "히레가스에 레몬소금 조합이라니 바삭하고 담백하니 넘 맛있겠어요~\n다른 톤이나 메뉴(모리소바, 새우튀김우동 등)에 맞춘 다른 버전도 확인해보시겠어요?",
    "단풍은 살짝 아쉬워도 하늘 맑고 경치 좋을 때 다녀오셔서 힐링되셨겠어요\n이 느낌 그대로 괜찮으신가요, 아니면 다른 포인트(주차나 간식 식감 등)를 살려 다듬어드릴까요?",
    "이 댓글 마음에 드시나요?",
    "와인 쪽으로 초점을 바꿔 볼까요?",
    "글라돔을 가습기 받침대로 쓰시는 건 진짜 센스있으시네요 🤍\n와인랙이나 브러시 쪽에 더 초점을 맞춘 다른 버전도 확인해 보시겠어요?",
    "이대로 사용하셔도 괜찮을까요?",
    "원하시는 말투가 있으면 알려주세요",
    "댓글을 조금 더 짧게 다듬어 드릴까요?",
    "이 문구를 그대로 올리시면 될까요?",
    "추가 댓글도 만들어 드릴까요?",
    "이런 방향의 댓글이 마음에 드시나요?",
]


@pytest.mark.parametrize("text", META_RESPONSES)
def test_meta_response_is_never_accepted_as_a_comment(text):
    check = ResponseContaminationGate.validate(text)
    assert check.is_contaminated
    assert check.code == "response_meta_contamination"
    assert DraftService.clean_ai_response(text) is None
    for source in ("gemini", "clipboard", "local", "user_edit"):
        assert not FinalQualityGate.validate(text, source=source).valid


@pytest.mark.parametrize("text", [
    "짜장 소스 넉넉하고 만두 바삭해서 같이 먹으면 진짜 꿀조합이겠어요 😋",
    "부모님 모시고 가기에도 편하겠어요~",
    "거북목 때문에 고민이셨을 텐데 한결 편해지셨으면 좋겠어요",
    "다른 메뉴도 궁금해지네요 ㅎㅎ 군만두는 몇 개 나오는 건가요?",
    "필요하신 분들한테 도움이 되겠어요~",
    "다른 톤의 립도 필요하겠어요 ㅎㅎ",
    "다른 포인트도 알려주셔서 감사해요~",
    "와인은 어떤 걸로 골라보셨어요?",
    "포인트가 되는 조명이 예쁘네요~",
    "와인 대신 디저트로 바꿔 보셨나요?",
    "이런 느낌의 카페를 좋아해서 다음에 가보고 싶네요~",
    "크림 브륄레는 많이 달지 않나요?",
])
def test_normal_reactions_and_blog_questions_are_not_meta(text):
    assert ResponseContaminationGate.validate(text).ok
    assert DraftService.clean_ai_response(text) is not None


def test_contaminated_history_is_preserved_but_never_used_as_prompt_example(tmp_path):
    from services.history import HistoryStore
    from services.user_learning_service import UserLearningService
    store = HistoryStore(str(tmp_path / "history.json"))
    bad = META_RESPONSES[7]
    store.posts = {
        "bad": {"updated_at": "2026-10-18T15:12:31", "comment": {"status": "submitted", "submitted_text": bad}},
        "good": {"updated_at": "2026-10-18T14:00:00", "comment": {"status": "submitted", "submitted_text": "군만두가 바삭해 보여서 먹어보고 싶네요~"}},
    }
    assert store.get_recent_submitted_comments(limit=1) == ["군만두가 바삭해 보여서 먹어보고 싶네요~"]
    assert store.is_comment_submitted("bad")
    assert store.posts["bad"]["comment"]["submitted_text"] == bad
    assert not UserLearningService.is_valid_learning_text(bad)


@pytest.mark.parametrize("text", META_RESPONSES)
@pytest.mark.parametrize("transform", [
    lambda text: text.replace(" ", "  "),
    lambda text: text.replace(" ", "\n"),
    lambda text: "\u200e".join(text),
    lambda text: "🙂 " + text + " ~",
])
def test_meta_variants_are_blocked_even_after_formatting(text, transform):
    variant = transform(text)
    assert ResponseContaminationGate.validate(variant).code == "response_meta_contamination"
    assert DraftService.clean_ai_response(variant) is None
    assert not FinalQualityGate.validate(variant, source="gemini").valid


@pytest.mark.parametrize("key,text", json.loads(
    (Path(__file__).parent / "fixtures/comment_meta_registered_20261018.json").read_text(encoding="utf-8")
))
@pytest.mark.parametrize("form", ("original", "single_line", "bidi", "crlf"))
def test_every_registered_incident_is_blocked(key, text, form):
    variant = {"original": text, "single_line": text.replace("\n", " "),
               "bidi": "\u200e".join(text), "crlf": text.replace("\n", "\r\n")}[form]
    assert ResponseContaminationGate.validate(variant).code == "response_meta_contamination", key
    assert DraftService.clean_ai_response(variant) is None, key
    assert not FinalQualityGate.validate(variant, source="gemini").valid, key
