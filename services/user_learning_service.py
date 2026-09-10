from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
import re
import tempfile
import time
from typing import Optional, Dict, Any, List, Tuple

from app.models import FeedPost
from src.logger import logger

USER_LEARNING_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "user_learning_corpus.json"))


class CorpusSource(str, Enum):
    USER_EDIT = "user_edit"
    USER_ADOPTED = "user_adopted"
    AUTO_SUBMIT = "auto_submit"
    EXTERNAL_COLLECTED = "external_collected"
    UNKNOWN = "unknown"


@dataclass
class PersonalizedStyleProfile:
    version: str = "v1.0"
    total_samples: int = 0
    user_edit_count: int = 0
    avg_length: float = 40.0
    tilde_ratio: float = 0.3
    exclamation_ratio: float = 0.2
    question_ratio: float = 0.05
    top_endings: List[str] = field(default_factory=lambda: ["~네요", "~요"])
    user_edit_tendency: List[str] = field(default_factory=list)


class UserLearningService:
    """
    사용자가 직접 수정한 최종 댓글 및 등록 이력을 수집/기록하는 학습 서비스:
    - 초안 대비 사용자가 수정한 내역을 JSON 데이터셋으로 영구 축적
    - 출처 구분(user_edit, user_adopted, auto_submit, external_collected)
    - auto_submit(비검토 자동 등록)은 학습 예시에서 엄격히 배제
    - 데이터 정제(광고, 테스트 텍스트, 찌꺼기 제거)
    - Few-shot 개인화 동적 예시 및 버전화된 문체 프로필 제공
    """

    BANNED_TEST_KEYWORDS = ("테스트", "test", "asdf", "임시", "mock", "sample", "샘플")
    BANNED_SPAM_KEYWORDS = (
        "원고료", "체험단", "협찬", "광고", "수익", "쿠폰", "할인코드",
        "카톡", "오픈채팅", "http://", "https://", "www.", ".com", ".kr", "문의", "상담"
    )
    BANNED_BOT_KEYWORDS = (
        "서이추", "이웃추가", "소통해요", "잘보고 갑니다", "잘 보고 갑니다",
        "좋은 정보", "좋은 포스팅", "포스팅 잘 봤어요", "유익한 정보",
        "하트 꾹", "공감 꾹", "답방", "이웃 신청"
    )

    @classmethod
    def record_submission(
        cls,
        post: FeedPost,
        initial_draft: str,
        final_submitted: str,
        category: str = "UNKNOWN",
        anchor: str = "",
        evidence_span: str = "",
        source: str = "unknown",
        rejection_reason: str = "",
        decision_origin: str = "user",
    ):
        if not final_submitted or not final_submitted.strip():
            return
        cls.record_decision(
            post=post,
            initial_draft=initial_draft,
            final_submitted=final_submitted,
            category=category,
            anchor=anchor,
            evidence_span=evidence_span,
            source=source,
            decision="edited" if (initial_draft or "").strip() != final_submitted.strip() else "adopted",
            rejection_reason=rejection_reason,
            decision_origin=decision_origin,
        )

    @classmethod
    def infer_anchor(cls, post: FeedPost, comment: str) -> tuple[str, str]:
        """Infer only a comment token that is visibly grounded in title/body."""
        from services.comments.entities import extract_entity_tokens

        context = f"{post.title or ''} {post.excerpt or ''}"
        for token in sorted(set(extract_entity_tokens(comment or "")), key=len, reverse=True):
            index = context.find(token)
            if index >= 0:
                start = max(0, index - 30)
                end = min(len(context), index + len(token) + 30)
                return token, context[start:end].strip()
        return "", ""

    @classmethod
    def normalize_source_type(cls, entry: dict) -> str:
        explicit = entry.get("source_type")
        if explicit:
            return explicit
        decision_origin = entry.get("decision_origin")
        source = entry.get("source")
        if decision_origin == "auto_submit" or source == "auto_submit":
            return CorpusSource.AUTO_SUBMIT.value
        if entry.get("is_user_edited") is True or entry.get("decision") == "edited":
            return CorpusSource.USER_EDIT.value
        if entry.get("decision") == "adopted":
            return CorpusSource.USER_ADOPTED.value
        if source == "external" or entry.get("decision_origin") == "external":
            return CorpusSource.EXTERNAL_COLLECTED.value
        return CorpusSource.UNKNOWN.value

    @classmethod
    def is_valid_learning_text(cls, text: str) -> bool:
        if not text or not isinstance(text, str):
            return False
        s = text.strip()
        if len(s) < 8 or len(s) > 100:
            return False
        s_lower = s.lower()
        for kw in cls.BANNED_TEST_KEYWORDS:
            if kw in s_lower:
                return False
        for kw in cls.BANNED_SPAM_KEYWORDS:
            if kw in s_lower:
                return False
        for kw in cls.BANNED_BOT_KEYWORDS:
            if kw in s_lower:
                return False
        if re.fullmatch(r"^[ㄱ-ㅎㅏ-ㅣ\s~.!?]+$", s):
            return False
        return True

    @classmethod
    def record_decision(
        cls,
        post: FeedPost,
        initial_draft: str,
        final_submitted: str = "",
        category: str = "UNKNOWN",
        anchor: str = "",
        evidence_span: str = "",
        source: str = "unknown",
        decision: str = "skipped",
        rejection_reason: str = "",
        decision_origin: str = "user",
    ):
        if decision not in {"adopted", "edited", "skipped", "rejected"}:
            raise ValueError(f"unsupported learning decision: {decision}")

        initial_s = (initial_draft or "").strip()
        final_s = (final_submitted or "").strip()
        is_edited = (initial_s != final_s)

        if not anchor:
            anchor, evidence_span = cls.infer_anchor(post, final_s or initial_s)

        source_type = source
        if decision_origin == "auto_submit":
            source_type = CorpusSource.AUTO_SUBMIT.value
        elif is_edited:
            source_type = CorpusSource.USER_EDIT.value
        elif decision == "adopted":
            source_type = CorpusSource.USER_ADOPTED.value
        elif source not in (s.value for s in CorpusSource):
            source_type = CorpusSource.EXTERNAL_COLLECTED.value if source == "external" else source

        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "post_key_hash": hashlib.sha256((post.key or post.url).encode("utf-8")).hexdigest(),
            "category": category,
            "anchor": anchor,
            "evidence_span": evidence_span[:160],
            "source": source,
            "source_type": source_type,
            "decision": decision,
            "decision_origin": decision_origin,
            "initial_draft": initial_s,
            "final_submitted": final_s,
            "is_user_edited": is_edited,
            "length": len(final_s),
            "rejection_reason": rejection_reason,
        }

        try:
            os.makedirs(os.path.dirname(USER_LEARNING_FILE), exist_ok=True)
            existing = []
            if os.path.exists(USER_LEARNING_FILE):
                try:
                    with open(USER_LEARNING_FILE, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = []

            existing.append(entry)

            fd, tmp = tempfile.mkstemp(prefix=".learning-", suffix=".json", dir=os.path.dirname(USER_LEARNING_FILE))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, USER_LEARNING_FILE)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            if decision == "skipped":
                logger.log("  📝 [LEARNING] 사용자가 건너뛴 초안과 사유를 기록했습니다.")
            elif is_edited:
                logger.log("  📝 [LEARNING] 사용자가 수정한 댓글을 참고용 코퍼스(user_learning_corpus.json)에 기록했습니다.")
            elif decision_origin == "auto_submit":
                logger.log("  📝 [LEARNING] 자동 등록 댓글을 참고용 코퍼스(user_learning_corpus.json)에 기록했습니다 (출처: auto_submit).")
            else:
                logger.log("  📝 [LEARNING] 등록된 댓글을 참고용 코퍼스(user_learning_corpus.json)에 기록했습니다.")
        except Exception as e:
            logger.log(f"  ⚠️ [LEARNING] 학습 데이터 저장 중 예외: {e}", "WARNING")

    @classmethod
    def load_corpus(cls) -> list[dict]:
        if not os.path.exists(USER_LEARNING_FILE):
            return []
        try:
            with open(USER_LEARNING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    @classmethod
    def get_cleaned_corpus(cls, raw_entries: Optional[List[dict]] = None) -> List[dict]:
        """
        학습에 적합한 데이터만 엄격히 정제:
        - auto_submit 출처 배제 (규칙: 사용자 검토 없는 자동 등록 댓글은 학습 예시로 절대 사용 안 함)
        - skipped, rejected 배제
        - 테스트 텍스트, 광고, 매크로 클리셰 배제
        - 정규화 텍스트 중복 배제
        """
        if raw_entries is None:
            raw_entries = cls.load_corpus()

        cleaned = []
        seen_texts = set()
        for item in raw_entries:
            source_type = cls.normalize_source_type(item)
            # Rule: Never train on auto_submit!
            if source_type == CorpusSource.AUTO_SUBMIT.value:
                continue

            decision = item.get("decision")
            if decision not in ("edited", "adopted"):
                continue

            submitted = (item.get("final_submitted") or "").strip()
            if not cls.is_valid_learning_text(submitted):
                continue

            norm_text = re.sub(r"\s+", "", submitted)
            if norm_text in seen_texts:
                continue
            seen_texts.add(norm_text)

            item_copy = dict(item)
            item_copy["source_type"] = source_type
            cleaned.append(item_copy)
        return cleaned

    @classmethod
    def compute_style_profile(cls, cleaned_entries: Optional[List[dict]] = None) -> PersonalizedStyleProfile:
        """정제된 코퍼스로부터 버전화된 문체 기준과 산출 근거를 계산"""
        if cleaned_entries is None:
            cleaned_entries = cls.get_cleaned_corpus()

        if not cleaned_entries:
            return PersonalizedStyleProfile(version="v1.0-default", total_samples=0)

        total = len(cleaned_entries)
        lengths = [len(it["final_submitted"]) for it in cleaned_entries]
        avg_len = sum(lengths) / total if total > 0 else 40.0

        tilde_count = sum(1 for it in cleaned_entries if "~" in it["final_submitted"])
        excl_count = sum(1 for it in cleaned_entries if "!" in it["final_submitted"])
        q_count = sum(1 for it in cleaned_entries if "?" in it["final_submitted"])

        endings_freq: Dict[str, int] = {}
        for it in cleaned_entries:
            text = it["final_submitted"].strip().rstrip("~!?.")
            for ending in ("네요", "어요", "아요", "구요", "고요", "죠", "나봐요", "인가봐요", "봐요", "듯해요", "좋겠어요"):
                if text.endswith(ending):
                    endings_freq[f"~{ending}"] = endings_freq.get(f"~{ending}", 0) + 1
                    break
            else:
                if text.endswith("요"):
                    endings_freq["~요"] = endings_freq.get("~요", 0) + 1

        sorted_endings = sorted(endings_freq.items(), key=lambda x: x[1], reverse=True)
        top_endings = [k for k, _ in sorted_endings[:4]] or ["~네요", "~요"]

        user_edits = [it for it in cleaned_entries if it.get("source_type") == CorpusSource.USER_EDIT.value]
        edit_tendencies: List[str] = []
        if user_edits:
            shorter_count = 0
            removed_hyperbole = 0
            for it in user_edits:
                init_len = len(it.get("initial_draft") or "")
                final_len = len(it.get("final_submitted") or "")
                if init_len > 0 and final_len < init_len:
                    shorter_count += 1
                init_text = it.get("initial_draft") or ""
                final_text = it.get("final_submitted") or ""
                for hyp in ("정말", "진짜", "너무", "완전", "대박"):
                    if hyp in init_text and hyp not in final_text:
                        removed_hyperbole += 1
                        break
            if shorter_count / len(user_edits) >= 0.5:
                edit_tendencies.append("길이 단축 (간결한 1문장 선호)")
            if removed_hyperbole / len(user_edits) >= 0.3:
                edit_tendencies.append("과도한 수식어/감탄 표현 절제")

        corpus_hash = hashlib.md5(f"{total}-{avg_len:.1f}-{tilde_count}".encode()).hexdigest()[:6]
        version = f"v1.0-{corpus_hash}"

        return PersonalizedStyleProfile(
            version=version,
            total_samples=total,
            user_edit_count=len(user_edits),
            avg_length=round(avg_len, 1),
            tilde_ratio=round(tilde_count / total, 2),
            exclamation_ratio=round(excl_count / total, 2),
            question_ratio=round(q_count / total, 2),
            top_endings=top_endings,
            user_edit_tendency=edit_tendencies,
        )

    @classmethod
    def select_dynamic_examples(
        cls,
        category: str = "GENERAL",
        anchors: Optional[List[str]] = None,
        limit: int = 3,
        raw_entries: Optional[List[dict]] = None,
    ) -> Tuple[List[str], Dict[str, Any]]:
        """
        현재 글의 맥락(카테고리, 앵커)에 맞는 예시 2~3개를 동적으로 선별.
        - 사용자 수정본(user_edit) 우선 배치
        - 구조 복제/동일 어미 반복 방지 다양성 필터
        - 부족 시 기본 예시 fallback 및 통계 제공
        """
        if raw_entries is None:
            raw_entries = cls.load_corpus()
        total_raw = len(raw_entries)

        user_edits_total = sum(
            1 for it in raw_entries
            if cls.normalize_source_type(it) == CorpusSource.USER_EDIT.value
        )

        cleaned = cls.get_cleaned_corpus(raw_entries)
        cleaned_count = len(cleaned)

        cat_upper = (category or "GENERAL").upper()
        anchors_set = set(a.lower() for a in (anchors or []) if a)

        scored = []
        for idx, item in enumerate(cleaned):
            score = 0.0
            st = item.get("source_type")
            if st == CorpusSource.USER_EDIT.value:
                score += 3.0
            elif st == CorpusSource.USER_ADOPTED.value:
                score += 1.0

            item_cat = (item.get("category") or "UNKNOWN").upper()
            if item_cat != "UNKNOWN" and item_cat != "COMMON":
                if item_cat == cat_upper or (cat_upper.startswith("FOOD") and "FOOD" in item_cat):
                    score += 2.0

            item_anchor = (item.get("anchor") or "").lower()
            if item_anchor and item_anchor in anchors_set:
                score += 2.0

            score += idx * 0.001  # slight tiebreaker
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        selected_texts: List[str] = []
        used_endings = set()
        for _, item in scored:
            text = item.get("final_submitted", "").strip()
            if not text:
                continue
            norm_ending = text[-4:] if len(text) >= 4 else text
            if norm_ending in used_endings:
                continue
            used_endings.add(norm_ending)
            selected_texts.append(text)
            if len(selected_texts) >= limit:
                break

        is_fallback = False
        if len(selected_texts) < 2:
            from services.ai_prompt import AIPromptBuilder
            selected_texts = [ex.strip().lstrip("- ").strip('"') for ex in AIPromptBuilder.REPRESENTATIVE_EXAMPLES[:limit]]
            is_fallback = True

        stats = {
            "total_raw": total_raw,
            "cleaned": cleaned_count,
            "user_edits": user_edits_total,
            "referenced": len(selected_texts),
            "is_fallback": is_fallback,
        }
        return selected_texts, stats

    @classmethod
    def get_learning_context(
        cls,
        category: str = "GENERAL",
        anchors: Optional[List[str]] = None,
        limit: int = 3,
    ) -> Tuple[List[str], Dict[str, Any], PersonalizedStyleProfile]:
        raw_entries = cls.load_corpus()
        examples, stats = cls.select_dynamic_examples(
            category=category, anchors=anchors, limit=limit, raw_entries=raw_entries
        )
        cleaned = cls.get_cleaned_corpus(raw_entries)
        profile = cls.compute_style_profile(cleaned)
        return examples, stats, profile
