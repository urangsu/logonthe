"""
프롬프트 버전 비교 평가 스크립트 (생성 코퍼스 방식 v2).

기존 버전과의 차이:
- 정적으로 하드코딩된 초안 대신 Gemini API를 실제 호출해 v3.0 / v3.2 각각의
  프롬프트로 초안을 생성하고, 동일 평가 파이프라인(CommentDraftInspector,
  FinalQualityGate)을 통과시켜 결과를 비교합니다.
- 생성된 초안과 평가 결과는 docs/PROMPT_EVALUATION_REPORT.json에 저장됩니다.

사용법:
    python scripts/compare_prompts.py [--model MODEL] [--cases N] [--output PATH]

옵션:
    --model   Gemini 모델 ID (실제 API 호출 시 명시 필요)
    --cases   실행할 케이스 개수 0=전체 (기본값: 0)
    --output  결과 JSON 경로 (기본값: docs/PROMPT_EVALUATION_REPORT.json)
    --dry-run API를 호출하지 않고 프롬프트 길이 비교만 실행
"""

import argparse
import json
import os
import sys
import textwrap
import time
import random
from typing import Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from services.ai_prompt import AIPromptBuilder, PROMPT_VERSION_V3_0, PROMPT_VERSION_V3_2
from services.comments.community_rhythm import CommentDraftInspector, FinalQualityGate
from services.comments.policy import CommentStylePolicy

EVALUATION_PRESET = "thoughtful"


# ---------------------------------------------------------------------------
# 평가 코퍼스 (4개 도메인)
# ---------------------------------------------------------------------------
TEST_CASES = [
    {
        "domain": "맛집/카페",
        "title": "[성수] 뚝섬역 에스프레소 바 카멜커피 7호점 - 앙버터와 시그니처 카멜커피 후기",
        "excerpt": (
            "성수동 골목에 위치한 카멜커피에 다녀왔습니다. 대표 메뉴인 카멜커피는 부드러운 플랫화이트 베이스에 "
            "달콤한 수제 크림이 올라가서 쌉싸름하면서도 달달한 밸런스가 정말 좋았습니다. 함께 시킨 앙버터는 겉바속촉 바게트에 "
            "두툼한 버터와 팥앙금이 듬뿍 들어있어 커피와 찰떡궁합이네요. 주말이라 웨이팅이 20분 정도 있었지만 "
            "매장 내부 빈티지 원목 가구 인테리어가 아늑해서 기다린 보람이 있었습니다."
        ),
        "content_focus": "CAFE_DESSERT",
        "verified_anchors": ["카멜커피", "앙버터"],
    },
    {
        "domain": "여행",
        "title": "1박 2일 강릉 뚜벅이 여행 코스 추천 | 안목해변 카페거리와 BTS 버스정류장",
        "excerpt": (
            "KTX 강릉역에서 내려서 버스를 타고 먼저 안목해변으로 향했습니다. 바다 색깔이 에메랄드빛으로 너무 맑고 "
            "파도 소리가 시원하더라고요. 해변 산책로를 따라 걷다가 카페 3층 통창 자리에서 바다를 보며 멍때렸습니다. "
            "오후에는 주문진 향호해변에 있는 BTS 앨범 자켓 촬영지 버스정류장에 들러 줄을 서서 기념사진을 남겼습니다. "
            "바닷바람이 꽤 불어서 겉옷을 챙겨가길 정말 잘했다는 생각이 들었습니다."
        ),
        "content_focus": "GENERAL",
        "verified_anchors": ["안목해변", "향호해변"],
    },
    {
        "domain": "제품/리뷰",
        "title": "로지텍 MX Master 3S 마우스 3개월 실사용 장단점 솔직 후기",
        "excerpt": (
            "기존에 쓰던 일반 마우스가 손목이 시큰거려서 로지텍 MX Master 3S로 바꾼 지 벌써 3개월이 지났습니다. "
            "가장 만족스러운 점은 무소음 클릭감입니다. 도서관이나 조용한 사무실에서도 눈치 안 보고 클릭할 수 있을 만큼 소음이 거의 없습니다. "
            "무한 휠 스크롤 기능 덕분에 긴 엑셀 문서나 PDF 코드를 볼 때 손가락 피로도가 확실히 줄어들었습니다. "
            "다만 무게가 141g으로 꽤 묵직해서 손이 작은 사람에게는 초반에 약간 적응 기간이 필요할 것 같습니다."
        ),
        "content_focus": "GENERAL",
        "verified_anchors": ["MX Master 3S", "무소음 클릭"],
    },
    {
        "domain": "일상/공부",
        "title": "주말 자격증 공부 일상과 도서관 집중 루틴",
        "excerpt": (
            "이번 주말에는 미뤄뒀던 정보처리기사 실기 시험 준비를 위해 아침 일찍 동네 시립도서관 열람실에 다녀왔습니다. "
            "아침 8시에 도착하니 창가 자리가 남아있어서 바로 자리 잡았네요. 텀블러에 얼음 가득 채워 담아온 콜드브루를 마시면서 "
            "기출문제 3회분을 풀었습니다. 알고리즘 파트에서 시간 복잡도 계산하는 문제가 헷갈려서 오답노트에 따로 정리해두었습니다. "
            "4시간 바짝 집중하고 나오니 날씨도 맑고 뿌듯한 하루였습니다."
        ),
        "content_focus": "GENERAL",
        "verified_anchors": ["정보처리기사", "오답노트"],
    },
]


# ---------------------------------------------------------------------------
# Gemini API 호출 (google-generativeai SDK)
# ---------------------------------------------------------------------------
def _call_gemini(prompt: str, model_id: str, retries: int = 2) -> Optional[str]:
    """Gemini API에 프롬프트를 전송하고 텍스트 응답을 반환합니다."""
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        print("  ⚠️  google-generativeai 패키지가 설치되어 있지 않습니다.", file=sys.stderr)
        print("     pip install google-generativeai 후 재실행하세요.", file=sys.stderr)
        return None

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("  ⚠️  GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.", file=sys.stderr)
        return None

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_id)

    for attempt in range(retries + 1):
        try:
            resp = model.generate_content(prompt)
            text = resp.text.strip() if resp.text else None
            return text
        except Exception as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            print(f"  ⚠️  Gemini API 오류: {exc}", file=sys.stderr)
            return None


def _evaluate_draft(draft_text: Optional[str], excerpt: str, *, generated: bool = True) -> dict:
    """생성된 초안을 CommentDraftInspector와 FinalQualityGate로 평가합니다."""
    if not draft_text:
        return {
            "text": None,
            "length": 0,
            "passed": False if generated else None,
            "stage": "n/a",
            "code": "generation_failed" if generated else "not_run",
            "feedback": "Gemini API에서 텍스트를 받지 못했습니다." if generated else "생성을 실행하지 않았습니다.",
            "quality_gate_valid": False if generated else None,
            "quality_gate_code": "generation_failed" if generated else "not_run",
        }

    policy = CommentStylePolicy.from_context(preset=EVALUATION_PRESET)
    context = dict(excerpt=excerpt, preset=EVALUATION_PRESET, source="gemini", style_policy=policy)
    inspect_res = CommentDraftInspector.inspect(draft_text, **context)
    gate_res = FinalQualityGate.validate_final_text(draft_text, **context)

    return {
        "text": draft_text,
        "length": len(draft_text),
        "passed": inspect_res.passed,
        "stage": inspect_res.stage,
        "code": inspect_res.code,
        "feedback": inspect_res.feedback,
        "quality_gate_valid": gate_res.valid,
        "quality_gate_code": gate_res.code,
    }


# ---------------------------------------------------------------------------
# 메인 비교 루프
# ---------------------------------------------------------------------------
def run_comparison(model_id: str, case_limit: int, dry_run: bool) -> list:
    results = []
    print("=" * 80)
    print("  생성 코퍼스 방식 프롬프트 비교 평가 (v2)")
    if dry_run:
        print("  [DRY-RUN] API 호출 없이 프롬프트 길이 비교만 실행합니다.")
    print("=" * 80)

    cases = TEST_CASES[:case_limit] if case_limit > 0 else TEST_CASES

    for idx, tc in enumerate(cases, start=1):
        domain = tc["domain"]
        title = tc["title"]
        excerpt = tc["excerpt"]
        focus = tc["content_focus"]
        anchors = tc["verified_anchors"]

        print(f"\n[케이스 {idx}/{len(cases)} - {domain}]")
        print(f"  제목: {textwrap.shorten(title, 60)}")

        prompt_v3_0 = AIPromptBuilder.build(
            title=title,
            excerpt=excerpt,
            content_focus=focus,
            verified_anchors=anchors,
            preset=EVALUATION_PRESET,
            version=PROMPT_VERSION_V3_0,
        )
        prompt_v3_2 = AIPromptBuilder.build(
            title=title,
            excerpt=excerpt,
            content_focus=focus,
            verified_anchors=anchors,
            preset=EVALUATION_PRESET,
            version=PROMPT_VERSION_V3_2,
        )

        reduction = round((1 - len(prompt_v3_2) / max(len(prompt_v3_0), 1)) * 100, 1)
        print(f"  📏 프롬프트: v3.0={len(prompt_v3_0)}자  v3.2={len(prompt_v3_2)}자  [{reduction}% 단축]")

        if dry_run:
            draft_v3_0 = None
            draft_v3_2 = None
        else:
            print(f"  🤖 v3.0 생성 중...", end="", flush=True)
            draft_v3_0 = _call_gemini(prompt_v3_0, model_id)
            print(f" 완료 ({len(draft_v3_0) if draft_v3_0 else 0}자)")
            time.sleep(1.5)

            print(f"  🤖 v3.2 생성 중...", end="", flush=True)
            draft_v3_2 = _call_gemini(prompt_v3_2, model_id)
            print(f" 완료 ({len(draft_v3_2) if draft_v3_2 else 0}자)")
            time.sleep(1.5)

        eval_v3_0 = _evaluate_draft(draft_v3_0, excerpt, generated=not dry_run)
        eval_v3_2 = _evaluate_draft(draft_v3_2, excerpt, generated=not dry_run)

        _print_eval("v3.0", eval_v3_0)
        _print_eval("v3.2", eval_v3_2)

        entry = {
            "case_id": idx,
            "domain": domain,
            "title": title,
            "excerpt": excerpt,
            "model": model_id if not dry_run else "dry-run",
            "prompt_comparison": {
                "v3_0_len": len(prompt_v3_0),
                "v3_2_len": len(prompt_v3_2),
                "reduction_percent": f"{reduction}%",
            },
            "v3_0": eval_v3_0,
            "v3_2": eval_v3_2,
            "winner": _winner(eval_v3_0, eval_v3_2),
        }
        results.append(entry)

    return results


def _print_eval(version: str, ev: dict) -> None:
    if ev["code"] == "not_run":
        print(f"  [{version}] 생성/품질 평가 미실행")
        return
    icon = "✅" if ev["passed"] and ev["quality_gate_valid"] else "❌"
    text_preview = textwrap.shorten(ev["text"] or "(생성 실패)", 70)
    print(f"  {icon} [{version}] \"{text_preview}\"")
    if not ev["passed"]:
        print(f"       -> 반려: {ev['code']}  {ev['feedback']}")
    if not ev["quality_gate_valid"]:
        print(f"       -> 품질게이트 실패: {ev['quality_gate_code']}")


def _winner(ev0: dict, ev2: dict) -> str:
    # Mechanical validation and length are not evidence of user preference.
    if not ev0.get("text") or not ev2.get("text"):
        return "not_evaluated"
    return "human_review_required"


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="프롬프트 버전 비교 평가 스크립트 (생성 코퍼스 방식)")
    parser.add_argument("--model", help="실제 API 호출에 사용할 모델 ID (웹 모델과 별도)")
    parser.add_argument("--cases", type=int, default=0, help="실행할 케이스 개수, 0=전체 (기본값: 0)")
    parser.add_argument("--output", default=None, help="결과 JSON 저장 경로 (기본값: docs/PROMPT_EVALUATION_REPORT.json)")
    parser.add_argument("--dry-run", action="store_true", help="API를 호출하지 않고 프롬프트 길이 비교만 실행")
    parser.add_argument("--review-output", help="모델/검사 점수를 숨긴 사용자 비교용 JSON 경로")
    args = parser.parse_args()
    if not args.dry_run and not args.model:
        parser.error("실제 API 비교에는 --model을 명시해야 합니다. 웹 실행 모델 검증을 대신하지 않습니다.")
    if args.cases < 0:
        parser.error("--cases는 0 이상이어야 합니다.")
    return args


def main() -> None:
    args = _parse_args()
    results = run_comparison(model_id=args.model, case_limit=args.cases, dry_run=args.dry_run)

    winner_counts: dict = {"human_review_required": 0, "not_evaluated": 0}
    pass_counts: dict = {"v3_0": 0, "v3_2": 0}
    for r in results:
        winner_counts[r["winner"]] = winner_counts.get(r["winner"], 0) + 1
        if r["v3_0"].get("passed") and r["v3_0"].get("quality_gate_valid"):
            pass_counts["v3_0"] += 1
        if r["v3_2"].get("passed") and r["v3_2"].get("quality_gate_valid"):
            pass_counts["v3_2"] += 1

    print("\n" + "=" * 80)
    print(f"  📊 종합 결과 (총 {len(results)}개 케이스, model={args.model if not args.dry_run else 'not_run'})")
    if args.dry_run:
        pass_counts = {"v3_0": None, "v3_2": None}
        print("     검사 통과율: 미측정 (생성 미실행)")
    else:
        print(f"     검사 통과: v3.0={pass_counts['v3_0']}/{len(results)}, v3.2={pass_counts['v3_2']}/{len(results)}")
    print(f"     사용자 비교 필요: {winner_counts.get('human_review_required', 0)}건 / 평가 불가: {winner_counts.get('not_evaluated', 0)}건")
    print("=" * 80)

    if args.output:
        out_path = args.output
    else:
        out_dir = os.path.join(ROOT_DIR, "docs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "PROMPT_EVALUATION_REPORT.json")

    payload = {
        "meta": {
            "model": args.model if not args.dry_run else "dry-run",
            "dry_run": args.dry_run,
            "provider": "gemini_api" if not args.dry_run else None,
            "production_web_runtime_verified": False,
            "quality_preference_evaluated": False,
            "total_cases": len(results),
            "pass_counts": pass_counts,
            "winner_counts": winner_counts,
        },
        "results": results,
    }
    if args.review_output:
        if os.path.abspath(args.review_output) == os.path.abspath(out_path):
            raise ValueError("비교용 파일과 원본 결과 파일 경로는 달라야 합니다.")
        review_rows, answer_key = [], {}
        for row in results:
            if not row["v3_0"].get("text") or not row["v3_2"].get("text"):
                continue
            versions = ["v3_0", "v3_2"]
            random.SystemRandom().shuffle(versions)
            answer_key[str(row["case_id"])] = dict(zip(("A", "B"), versions))
            review_rows.append({
                "case_id": row["case_id"], "title": row["title"], "excerpt": row["excerpt"],
                "A": row[versions[0]]["text"], "B": row[versions[1]]["text"],
                "preferred": None, "reason": "", "edited_text": "",
            })
        payload["blind_review_key"] = answer_key
        with open(args.review_output, "w", encoding="utf-8") as f:
            json.dump({"status": "awaiting_human_review", "cases": review_rows}, f, ensure_ascii=False, indent=2)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 평가 결과 저장 완료: {out_path}")


if __name__ == "__main__":
    main()
