"""One policy for browser preview and worker validation; never silently clean text."""
import json
import re
import unicodedata
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
POLICY = json.loads((ASSETS / "helper_policy.json").read_text(encoding="utf-8"))


def normalize(text):
    return unicodedata.normalize("NFC", str(text or ""))


def validate_comment(text, suffix=""):
    body, tail = normalize(text), normalize(suffix)
    final = body + ((" " + tail) if tail else "")
    reasons, codes = [], []
    length = len(final)
    if not body.strip():
        codes.append("empty")
        reasons.append("댓글 본문을 입력해 주세요.")
    if length < POLICY["minLength"] or length > POLICY["maxLength"]:
        codes.append("length")
        reasons.append(f"접미문구 포함 {POLICY['minLength']}–{POLICY['maxLength']}자여야 합니다. 현재 {length}자입니다.")
    for rule in POLICY["patterns"]:
        if re.search(rule["pattern"], final, re.I | re.M):
            codes.append(rule["code"])
            reasons.append(rule["reason"])
    return {"valid": not reasons, "text": final, "length": length,
            "reasons": reasons, "codes": codes}


def body_sufficient(body):
    return POLICY["minBodyLength"] <= len(normalize(body).strip()) <= POLICY["maxBodyLength"]


def build_prompt(title, body, suffix=""):
    if not body_sufficient(body):
        raise ValueError("본문이 부족하거나 너무 깁니다. 40–6000자의 본문 발췌를 직접 입력해 주세요.")
    tail_len = len(normalize(suffix)) + (1 if suffix else 0)
    maximum = POLICY["maxLength"] - tail_len
    minimum = max(1, POLICY["minLength"] - tail_len)
    if maximum < minimum or maximum < 12:
        raise ValueError("접미문구가 너무 깁니다. 접미문구를 줄여 주세요.")
    return (POLICY["promptInstruction"] + f" 댓글 자체는 {minimum}–{maximum}자(NFC 기준)로 작성하세요.\n"
            + json.dumps({"title": normalize(title), "quotedBody": normalize(body)}, ensure_ascii=False))
