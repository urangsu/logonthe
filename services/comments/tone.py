"""Conservative cues for restrained comments, not a sentiment classifier."""


RESTRAINED_CUES = (
    "부고", "별세", "추모", "애도", "장례", "돌아가셨", "떠나보낸",
    "투병", "항암", "참사", "우울증", "슬픈 이야기", "진지한 이야기",
    "담담한 사색", "상실의 아픔",
)


def needs_restrained_tone(title: str = "", excerpt: str = "") -> bool:
    text = f"{title or ''}\n{excerpt or ''}"
    return any(cue in text for cue in RESTRAINED_CUES)
