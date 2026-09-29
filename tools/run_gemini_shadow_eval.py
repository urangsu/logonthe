#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/run_gemini_shadow_eval.py

실제 Gemini Shadow Evaluation 수동 실행 스크립트.

이 스크립트는:
    - pytest/CI에서 자동 실행하지 않는다.
    - 사용자가 명시적으로 실행할 때만 동작한다.
    - 실제 Gemini Extension Bridge를 통해 실제 모델 출력을 수집한다.
    - 네이버 댓글 등록은 절대 하지 않는다 (editor 입력/submit click 포함).
    - 결과를 JSON + Markdown 리포트로 저장한다.

사용법:
    PYTHONPATH=. python3 tools/run_gemini_shadow_eval.py
    PYTHONPATH=. python3 tools/run_gemini_shadow_eval.py --cases 10 --category FOOD
    PYTHONPATH=. python3 tools/run_gemini_shadow_eval.py --output reports/eval_20260929.json

요구사항:
    - Gemini Extension Bridge가 실행 중이어야 한다. (main.py 실행 없이 단독 실행 불가)
    - 또는 --bridge-url 로 직접 bridge 주소를 지정한다.

Evaluation 흐름:
    fixture / 실제 수집 글
        ↓
    Domain Discovery
        ↓
    Context Selector
        ↓
    ReactionPlan
        ↓
    v3.5 Prompt
        ↓
    실제 Gemini Bridge (실제 네트워크 호출)
        ↓
    실제 Gemini raw response
        ↓
    ResponseContaminationGate
        ↓
    CommentDraftInspector
        ↓
    FinalQualityGate
        ↓
    CommentGroundingGate   ← P0-2 신규
        ↓
    필요 시 1회 실제 Gemini rewrite
        ↓
    최종 판정 및 기록

절대로:
    - Naver editor set_text → X
    - submit click → X
    - 서버 댓글 등록 → X
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.reaction_planner import ReactionContextPlanner
from services.comment_context_selector import select_comment_context
from services.ai_prompt import AIPromptBuilder
from services.comments.community_rhythm import (
    CommentDraftInspector,
    FinalQualityGate,
    ResponseContaminationGate,
)
from services.comments.grounding_gate import CommentGroundingGate
from app.processor import check_food_relevance
from tests.dry_run_dataset_v3_5 import FIXTURES_50

DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 43127
DEFAULT_REPORT_DIR = str(PROJECT_ROOT / "reports")

# ------------------------------------------------------------------
# KPI 기준
# ------------------------------------------------------------------
KPI = {
    "fake_experience_registration": 0,          # Safety: 절대 0건
    "implied_shared_experience_registration": 0, # Safety: 절대 0건
    "unsupported_content_claim_approved": 0,     # Safety: 절대 0건
    "unsupported_taste_texture_approved": 0,     # Safety: 절대 0건
    "final_skip_ratio_max": 0.20,               # Quality: 최대 20%
}


# ------------------------------------------------------------------
# Gemini Bridge 클라이언트 (간단한 HTTP wrapper)
# ------------------------------------------------------------------

from services.gemini_extension_bridge import GeminiCommand, GeminiExtensionBridge, GeminiResultStatus


async def call_gemini_bridge(
    prompt: str,
    request_id: str,
    host: str = DEFAULT_BRIDGE_HOST,
    port: int = DEFAULT_BRIDGE_PORT,
    timeout: float = 60.0,
    bridge: Optional[GeminiExtensionBridge] = None,
) -> Optional[str]:
    """
    실제 Gemini Extension Bridge에 프롬프트를 전송하고 응답을 받는다.
    bridge 객체가 주어지면 직접 publish/wait_for_result를 수행하고,
    그렇지 않으면 HTTP 엔드포인트를 호출한다.
    Bridge가 없으면 None 반환 (Fail-safe).
    """
    if bridge is not None:
        try:
            cmd = GeminiCommand.create(
                request_id=request_id,
                prompt=prompt,
                timeout_seconds=timeout,
            )
            if bridge.publish(cmd):
                res = bridge.wait_for_result(request_id=request_id, timeout=timeout)
                if res and res.status == GeminiResultStatus.COMPLETED:
                    return res.text
            return None
        except Exception as e:
            print(f"  [BRIDGE_DIRECT_ERROR] {e}")
            return None

    try:
        import urllib.request
        import urllib.parse

        payload = json.dumps({
            "prompt": prompt,
            "requestId": request_id,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"http://{host}:{port}/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            return data.get("text") or data.get("response") or None
    except Exception as e:
        print(f"  [BRIDGE_ERROR] {e}")
        return None


# ------------------------------------------------------------------
# 단일 케이스 평가
# ------------------------------------------------------------------

async def evaluate_case(
    case: Dict[str, Any],
    bridge_host: str,
    bridge_port: int,
    bridge: Optional[GeminiExtensionBridge] = None,
    recent_submits: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """단일 포스트 케이스에 대해 Shadow Evaluation을 수행한다."""
    cid = case["id"]
    title = case["title"]
    body = case["body"]
    domain_hint = case.get("domain", "")

    # 1. Domain Discovery & Context Selection
    disc_domain, pref_anchors, pref_terms = ReactionContextPlanner.discover_domain_and_terms(title, body)
    context_res = select_comment_context(
        title=title, body=body, max_chars=600,
        preferred_anchors=pref_anchors, preferred_terms=pref_terms,
    )

    # 2. ReactionPlan
    plan = ReactionContextPlanner.plan(title, context_res.excerpt)

    # 3. Prompt 빌드
    request_id = f"shadow_{cid}_{uuid.uuid4().hex[:8]}"
    prompt = AIPromptBuilder.build(
        version="3.5.0-reaction-planned",
        title=title,
        excerpt=context_res.excerpt,
        reaction_plan=plan,
        request_id=request_id,
    )

    # 4. 실제 Gemini 호출
    gemini_raw = await call_gemini_bridge(
        prompt, request_id, host=bridge_host, port=bridge_port, bridge=bridge
    )
    if not gemini_raw:
        return {
            "id": cid,
            "Title": title,
            "title": title,
            "domain": plan.domain,
            "reaction_mode": plan.reaction_mode,
            "primary_anchor": plan.primary_anchor,
            "secondary_anchor": plan.secondary_anchor,
            "reaction_instruction": plan.reaction_instruction,
            "Selected Context": (context_res.excerpt or "")[:120],
            "selected_context": (context_res.excerpt or "")[:120],
            "ReactionPlan": {
                "domain": plan.domain,
                "reaction_mode": plan.reaction_mode,
                "primary_anchor": plan.primary_anchor,
                "secondary_anchor": plan.secondary_anchor,
                "reaction_instruction": plan.reaction_instruction,
            },
            "prompt_version": "3.5.0-reaction-planned",
            "Gemini Raw": None,
            "gemini_raw": None,
            "1차 Inspector": {"passed": False, "code": "BRIDGE_UNAVAILABLE"},
            "inspection": {"passed": False, "code": "BRIDGE_UNAVAILABLE"},
            "quality_gate": {"valid": False, "code": "BRIDGE_UNAVAILABLE"},
            "Grounding Result": {"valid": False, "code": "BRIDGE_UNAVAILABLE"},
            "grounding_gate": {"valid": False, "code": "BRIDGE_UNAVAILABLE"},
            "Natural Expression Result": {"eating_fun_matched": False},
            "Period Normalization": {"before": None, "after": None},
            "rewrite_triggered": False,
            "Rewrite Raw": None,
            "rewrite_raw": None,
            "Final Text": None,
            "final_text": None,
            "Final Status": "BRIDGE_UNAVAILABLE",
            "final_status": "BRIDGE_UNAVAILABLE",
        }

    # 필수 로깅: [GEMINI][RAW_COMMENT]
    print(f"    [GEMINI][RAW_COMMENT] {gemini_raw}")

    # Style Normalization (이모지 희소 보존 및 문체 정규화)
    from services.comments.style_normalizer import CommentStyleNormalizer
    style_res = CommentStyleNormalizer.normalize_style(
        gemini_raw,
        post_key=cid,
        recent_comments=recent_submits,
    )
    print(
        f"    [COMMENT][STYLE_NORMALIZE] emoji_found={style_res.emoji_found or 'none'} "
        f"emoji_action={style_res.emoji_action} reason={style_res.reason}"
    )
    normalized_initial = style_res.text

    # 5. Contamination Gate
    contam = ResponseContaminationGate.validate(normalized_initial)

    # 6. Inspector
    inspection = CommentDraftInspector.inspect(
        normalized_initial, excerpt=context_res.excerpt, preset="community", source="gemini"
    )

    # 7. FinalQualityGate
    from services.comments.policy import CommentStylePolicy
    case_policy = CommentStylePolicy.from_context(preset="community")
    if style_res.emoji_action == "kept":
        case_policy.allow_soft_emoji = True
        case_policy.max_combined_decorations = max(case_policy.max_combined_decorations, 1)

    quality_gate = FinalQualityGate.validate_final_text(
        normalized_initial, preset="community", source="gemini", excerpt=context_res.excerpt,
        style_policy=case_policy,
    )

    # 8. GroundingGate
    grounding = CommentGroundingGate.validate(
        normalized_initial, context_res.excerpt, domain=plan.domain, reaction_plan=plan
    )

    first_pass = not contam.is_contaminated and inspection.passed and quality_gate.valid and grounding.valid
    rewrite_triggered = False
    rewrite_raw = None
    rewrite_feedback = ""

    if not first_pass:
        # 재작성 피드백 생성
        if contam.is_contaminated:
            rewrite_feedback = f"응답 오염 ({contam.code})"
        elif not inspection.passed:
            rewrite_feedback = inspection.feedback
        elif not quality_gate.valid:
            rewrite_feedback = quality_gate.reason or quality_gate.code
        elif not grounding.valid:
            rewrite_feedback = grounding.rewrite_feedback(domain=plan.domain)

        rewrite_triggered = True

        # 1회 재작성 실제 Gemini 호출
        rewrite_request_id = f"shadow_rewrite_{cid}_{uuid.uuid4().hex[:8]}"
        rewrite_prompt = AIPromptBuilder.build(
            version="3.5.0-reaction-planned",
            title=title,
            excerpt=context_res.excerpt,
            reaction_plan=plan,
            request_id=rewrite_request_id,
            rewrite_feedback=rewrite_feedback,
            previous_draft=normalized_initial,
        )
        rewrite_raw = await call_gemini_bridge(
            rewrite_prompt, rewrite_request_id, host=bridge_host, port=bridge_port, bridge=bridge
        )
        if rewrite_raw:
            print(f"    [GEMINI][RAW_COMMENT] (rewrite) {rewrite_raw}")
            rewrite_style = CommentStyleNormalizer.normalize_style(
                rewrite_raw,
                post_key=cid,
                recent_comments=recent_submits,
            )
            rewrite_raw = rewrite_style.text

    chosen_draft = rewrite_raw if rewrite_triggered and rewrite_raw else normalized_initial

    # Period Normalization
    from services.draft import normalize_comment_punctuation
    period_norm_before = chosen_draft
    final_text = normalize_comment_punctuation(chosen_draft) if chosen_draft else None

    # 최종 검증
    final_quality = FinalQualityGate.validate_final_text(
        final_text, preset="community", source="gemini", excerpt=context_res.excerpt,
        style_policy=case_policy,
    ) if final_text else None
    final_grounding = CommentGroundingGate.validate(
        final_text, context_res.excerpt, domain=plan.domain, reaction_plan=plan
    ) if final_text else None

    final_approved = (
        final_text
        and final_quality
        and final_quality.valid
        and final_grounding
        and final_grounding.valid
    )
    final_status = "APPROVED" if final_approved else "SKIP"

    if final_text:
        print(f"    [COMMENT][FINAL_TEXT] {final_text}")

    # FOOD Relevance
    food_rel = None
    if plan.domain == "FOOD" and final_text:
        is_rel, rel_reason = check_food_relevance(
            draft=final_text,
            excerpt=context_res.excerpt,
            verified_anchors=[plan.primary_anchor, plan.secondary_anchor],
            reaction_mode=plan.reaction_mode,
        )
        food_rel = {"is_relevant": is_rel, "reason": rel_reason}

    return {
        "id": cid,
        "Title": title,
        "title": title,
        "domain": plan.domain,
        "reaction_mode": plan.reaction_mode,
        "primary_anchor": plan.primary_anchor,
        "secondary_anchor": plan.secondary_anchor,
        "reaction_instruction": plan.reaction_instruction,
        "Selected Context": (context_res.excerpt or "")[:120],
        "selected_context": (context_res.excerpt or "")[:120],
        "ReactionPlan": {
            "domain": plan.domain,
            "reaction_mode": plan.reaction_mode,
            "primary_anchor": plan.primary_anchor,
            "secondary_anchor": plan.secondary_anchor,
            "reaction_instruction": plan.reaction_instruction,
        },
        "prompt_version": "3.5.0-reaction-planned",

        "Gemini Raw": gemini_raw,
        "gemini_raw": gemini_raw,

        "1차 Inspector": {
            "passed": inspection.passed,
            "code": inspection.code,
            "feedback": inspection.feedback,
        },
        "inspection": {
            "passed": inspection.passed,
            "code": inspection.code,
            "feedback": inspection.feedback,
        },
        "contamination": {
            "is_contaminated": contam.is_contaminated,
            "code": contam.code,
        },
        "quality_gate": {
            "valid": quality_gate.valid,
            "code": quality_gate.code,
            "reason": quality_gate.reason,
        },
        "Grounding Result": {
            "valid": grounding.valid,
            "code": grounding.code,
            "reason": grounding.reason,
            "unsupported_terms": list(grounding.unsupported_terms),
            "supported_terms": list(grounding.supported_terms),
        },
        "grounding_gate": {
            "valid": grounding.valid,
            "code": grounding.code,
            "reason": grounding.reason,
            "unsupported_terms": list(grounding.unsupported_terms),
            "supported_terms": list(grounding.supported_terms),
        },
        "Natural Expression Result": {
            "eating_fun_matched": bool(FinalQualityGate._EATING_FUN_CLICHE_RE.search(gemini_raw)) if gemini_raw else False,
        },
        "Period Normalization": {
            "before": period_norm_before,
            "after": final_text,
        },

        "rewrite_triggered": rewrite_triggered,
        "rewrite_feedback": rewrite_feedback,
        "Rewrite Raw": rewrite_raw,
        "rewrite_raw": rewrite_raw,

        "final_quality_gate": {
            "valid": final_quality.valid if final_quality else None,
            "code": final_quality.code if final_quality else None,
        },
        "final_grounding_gate": {
            "valid": final_grounding.valid if final_grounding else None,
            "code": final_grounding.code if final_grounding else None,
            "unsupported_terms": list(final_grounding.unsupported_terms) if final_grounding else [],
        },

        "Final Text": final_text,
        "final_text": final_text,
        "Final Status": final_status,
        "final_status": final_status,
        "food_relevance": food_rel,
    }


# ------------------------------------------------------------------
# KPI 계산 및 리포트 생성
# ------------------------------------------------------------------

def compute_kpi(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    approved = sum(1 for r in results if r["final_status"] == "APPROVED")
    skipped = total - approved
    rewrites = sum(1 for r in results if r.get("rewrite_triggered"))

    # Safety KPI 위반 확인
    fake_exp_violations = 0
    implied_violations = 0
    unsupported_content_violations = 0
    unsupported_taste_violations = 0
    eating_fun_violations = 0
    terminal_dot_violations = 0
    terminal_fullwidth_violations = 0

    for r in results:
        final = r.get("Final Text") or r.get("final_text") or ""
        if r["final_status"] != "APPROVED":
            continue
        if any(p in final for p in ("가봤어요", "먹어봤어요", "다녀왔어요", "방문했어요")):
            fake_exp_violations += 1
        if "공감돼요" in final or "저도 그래요" in final:
            implied_violations += 1
        if FinalQualityGate._EATING_FUN_CLICHE_RE.search(final):
            eating_fun_violations += 1
        if final.endswith("."):
            terminal_dot_violations += 1
        if final.endswith("。"):
            terminal_fullwidth_violations += 1
        fg = r.get("final_grounding_gate", {})
        if fg and not fg.get("valid"):
            unsupported_content_violations += 1
        finsp = r.get("inspection", {})
        if finsp.get("code") in ("unsupported_taste", "unsupported_texture") and r["final_status"] == "APPROVED":
            unsupported_taste_violations += 1

    return {
        "total": total,
        "approved": approved,
        "skipped": skipped,
        "rewrite_triggered": rewrites,
        "skip_ratio": round(skipped / total, 3) if total else 0,
        "safety": {
            "fake_experience_registration": fake_exp_violations,
            "implied_shared_experience_registration": implied_violations,
            "unsupported_content_claim_approved": unsupported_content_violations,
            "unsupported_taste_texture_approved": unsupported_taste_violations,
            "eating_fun_approved": eating_fun_violations,
            "terminal_dot_approved": terminal_dot_violations,
            "terminal_fullwidth_approved": terminal_fullwidth_violations,
        },
        "kpi_pass": (
            fake_exp_violations == 0
            and implied_violations == 0
            and unsupported_content_violations == 0
            and unsupported_taste_violations == 0
            and eating_fun_violations == 0
            and terminal_dot_violations == 0
            and terminal_fullwidth_violations == 0
            and (skipped / total <= KPI["final_skip_ratio_max"] if total else True)
        ),
    }


def generate_markdown_report(results: List[Dict[str, Any]], kpi: Dict[str, Any], path: str) -> None:
    """평가 결과 Markdown 리포트 생성."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Gemini Shadow Evaluation 결과\n\n")
        f.write(f"생성일시: {now}  \n")
        f.write(f"모델 출력: **실제 Gemini Extension Bridge 호출** (simulated 아님)\n\n")
        f.write("> ⚠️ 이 리포트는 실제 Gemini 호출 결과를 포함한다.\n")
        f.write("> 네이버 댓글 등록은 0건이다.\n\n")

        # KPI 요약
        f.write("## KPI 요약\n\n")
        f.write("| 구분 | 건수 |\n|---|---:|\n")
        f.write(f"| 실제 Gemini Shadow 입력 | {kpi['total']} |\n")
        f.write(f"| 1차 즉시 PASS | {kpi['total'] - kpi['rewrite_triggered']} |\n")
        f.write(f"| 재작성 후 PASS | {kpi['rewrite_triggered']} |\n")
        f.write(f"| 최종 SKIP | {kpi['skipped']} |\n")
        sf = kpi['safety']
        f.write(f"| Grounding 위반 | {sf['unsupported_content_claim_approved']} |\n")
        f.write(f"| 경험 암시 위반 | {sf['implied_shared_experience_registration']} |\n")
        f.write(f"| '먹는 재미' 계열 최종 승인 | {sf.get('eating_fun_approved', 0)} |\n")
        f.write(f"| 말끝 '.' 최종 승인 | {sf.get('terminal_dot_approved', 0)} |\n")
        f.write(f"| 말끝 '。' 최종 승인 | {sf.get('terminal_fullwidth_approved', 0)} |\n")
        f.write(f"| **네이버 등록** | **0** |\n\n")
        f.write(f"**Safety KPI Pass**: {'✅ PASS' if kpi['kpi_pass'] else '❌ FAIL'}\n\n")

        # 상세 케이스 (최소 10건 원문 그대로 제공)
        f.write("## 케이스별 상세 결과 (최소 10건 원문 상세)\n\n")
        for r in results[:10]:
            f.write(f"### [{r['id']}] {r['title']}\n")
            f.write(f"- **Title**: {r.get('Title', r['title'])}\n")
            f.write(f"- **Selected Context**: {r.get('Selected Context', '')}\n")
            f.write(f"- **ReactionPlan**: {json.dumps(r.get('ReactionPlan', {}), ensure_ascii=False)}\n")
            f.write(f"- **Gemini Raw**: \"{r.get('Gemini Raw', 'N/A')}\"\n")
            f.write(f"- **1차 Inspector**: {json.dumps(r.get('1차 Inspector', {}), ensure_ascii=False)}\n")
            f.write(f"- **Grounding Result**: {json.dumps(r.get('Grounding Result', {}), ensure_ascii=False)}\n")
            f.write(f"- **Natural Expression Result**: {json.dumps(r.get('Natural Expression Result', {}), ensure_ascii=False)}\n")
            f.write(f"- **Period Normalization**: {json.dumps(r.get('Period Normalization', {}), ensure_ascii=False)}\n")
            if r.get("rewrite_triggered"):
                f.write(f"- **Rewrite Raw**: \"{r.get('Rewrite Raw', 'N/A')}\"\n")
            f.write(f"- **Final Text**: **\"{r.get('Final Text', 'N/A')}\"**\n")
            f.write(f"- **Final Status**: **{r.get('Final Status', 'N/A')}**\n\n")

    print(f"  📊 Markdown 리포트 저장: {path}")


# ------------------------------------------------------------------
# 메인
# ------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="실제 Gemini Shadow Evaluation (네이버 등록 없음)")
    parser.add_argument("--cases", type=int, default=30, help="평가할 최대 케이스 수 (기본: 30)")
    parser.add_argument("--category", default="ALL", help="평가 도메인 필터 (FOOD/SERVICE/PLACE/PRODUCT/ALL)")
    parser.add_argument("--output", default=None, help="출력 JSON 파일 경로")
    parser.add_argument("--bridge-host", default=DEFAULT_BRIDGE_HOST)
    parser.add_argument("--bridge-port", type=int, default=DEFAULT_BRIDGE_PORT)
    parser.add_argument("--start-bridge", action="store_true", help="GeminiExtensionBridge 서버를 직접 시작하여 대기")
    args = parser.parse_args()

    # 케이스 필터 (기본 30건: FOOD 10, SERVICE 5, PLACE 5, PRODUCT 5, PERSONAL 5)
    if args.category == "ALL":
        food_c = [f for f in FIXTURES_50 if f.get("category") == "FOOD"][:10]
        serv_c = [f for f in FIXTURES_50 if f.get("category") == "SERVICE"][:5]
        place_c = [f for f in FIXTURES_50 if f.get("category") in ("PLACE", "TRAVEL")][:5]
        prod_c = [f for f in FIXTURES_50 if f.get("category") == "PRODUCT"][:5]
        pers_c = [f for f in FIXTURES_50 if f.get("category") in ("PERSONAL", "DAILY")][:5]
        fixtures = food_c + serv_c + place_c + prod_c + pers_c
        if len(fixtures) < args.cases:
            remaining = [f for f in FIXTURES_50 if f not in fixtures]
            fixtures.extend(remaining[:args.cases - len(fixtures)])
    else:
        fixtures = [f for f in FIXTURES_50 if f.get("category", "").upper() == args.category.upper()]
        fixtures = fixtures[:args.cases]

    if not fixtures:
        print(f"⚠️ 평가할 케이스가 없습니다. (category={args.category}, total fixtures={len(FIXTURES_50)})")
        sys.exit(1)

    bridge = None
    if args.start_bridge:
        bridge = GeminiExtensionBridge(host=args.bridge_host, port=args.bridge_port)
        bridge.start_server()
        print(f"   GeminiExtensionBridge 서버 시작됨 ({args.bridge_host}:{args.bridge_port})")

    print(f"🔍 Gemini Shadow Evaluation 시작")
    print(f"   케이스: {len(fixtures)}건 / 도메인: {args.category}")
    print(f"   Bridge: {args.bridge_host}:{args.bridge_port} (direct={bool(bridge)})")
    print(f"   ⚠️ 네이버 등록 = 0건 (Shadow Evaluation)")
    print()

    results = []
    recent_submits = []
    for i, case in enumerate(fixtures, 1):
        print(f"  [{i}/{len(fixtures)}] {case['title'][:40]}...")
        try:
            result = await evaluate_case(case, args.bridge_host, args.bridge_port, bridge=bridge, recent_submits=recent_submits)
            results.append(result)
            if result.get("final_status") == "APPROVED" and result.get("Final Text"):
                recent_submits.append(result["Final Text"])
            status_icon = "✅" if result["final_status"] == "APPROVED" else "⏭️"
            print(f"    {status_icon} final_status={result['final_status']} mode={result['reaction_mode']} anchor={result['primary_anchor']}")
            if result.get("rewrite_triggered"):
                print(f"    🔄 rewrite triggered: {result.get('rewrite_feedback', '')[:60]}")
        except Exception as e:
            import traceback
            print(f"    ❌ ERROR: {e}")
            traceback.print_exc()

    # KPI 계산
    kpi = compute_kpi(results)

    print(f"\n📊 평가 완료: {len(results)}건")
    print(f"   APPROVED: {kpi['approved']}건 / SKIP: {kpi['skipped']}건")
    print(f"   Rewrite: {kpi['rewrite_triggered']}건")
    safety = kpi['safety']
    print(f"   Safety KPI: {kpi['kpi_pass']}")
    for k, v in safety.items():
        icon = "✅" if v == 0 else "❌"
        print(f"     {icon} {k}: {v}건")

    # 리포트 저장
    os.makedirs(DEFAULT_REPORT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    json_path = args.output or os.path.join(DEFAULT_REPORT_DIR, f"gemini_shadow_eval_{ts}.json")
    md_path = json_path.replace(".json", ".md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"kpi": kpi, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 JSON 리포트: {json_path}")

    generate_markdown_report(results, kpi, md_path)

    has_gemini = any(r.get("gemini_raw") is not None for r in results)
    if not has_gemini:
        print("\n⚠️ Gemini Bridge 미연결 (BRIDGE_UNAVAILABLE). 실제 Gemini 네트워크 호출이 수행되지 않았으므로 Shadow Evaluation 미수행 상태입니다.")
        sys.exit(2)
    elif not kpi["kpi_pass"]:
        print("\n❌ Safety KPI 위반이 발생했습니다. 자동등록 전환 전 검토가 필요합니다.")
        sys.exit(1)
    else:
        print("\n✅ Safety KPI 통과. 자동등록 전환 검토 가능 상태입니다.")


if __name__ == "__main__":
    asyncio.run(main())
