"""
V13.3 Discovery & Runtime Recovery Verification Script
1. Discovery TopicFilter unit logic & weak positive gate
2. QueryRotator & TargetedSearchFeedSource integration
3. Local Gemini Extension Bridge HTTP Server & Preflight Diagnostics (Synthetic Bridge Smoke)
4. Real Playwright BrowserSession Lifecycle & Detail Page Recreation (Live Browser Smoke)
"""
import sys
import os
import time
import http.client
import json

from naver.discovery.topic_filter import DiscoveryTopicFilter, TopicDecision
from naver.discovery.query_pool import QueryRotator, QuerySpec
from naver.discovery.search_source import TargetedSearchFeedSource
from services.gemini_extension_bridge import GeminiExtensionBridge, GeminiBridgeHTTPServer
from services.runtime_contract import load_runtime_contract
from browser.session import BrowserSession
from app.errors import classify_playwright_failure, BrowserFailureKind
from src.logger import logger


def run_smoke():
    print("==================================================")
    print("🚀 [SMOKE 1/4] Discovery TopicFilter & Positive Gate Smoke")
    print("==================================================")
    test_cases = [
        ("동대문엽기떡볶이 밀키트 착한맛 솔직후기", "소분해서 보관하면 편해요 분양", True, "FOOD"),
        ("제주시청 제주양식맛집 제주도민이 추천하는 제주 한양", "주식회사 한양에서 운영하는 맛집", True, "FOOD"),
        ("영화 <호프> 촬영지 전남 해남 여행지 소개", "사진 출사하기 좋은 여행지", True, "TRAVEL"),
        ("면세점에서 구매한 디올 선글라스 유럽여행 착용 후기", "환율에 따라 가격 차이는 있었습니다", True, "TRAVEL"),
        ("ETF 배당주 포트폴리오 정리", "미국 배당주와 환율 투자 전략", False, "finance"),
        ("아이폰 17 프로 성능 비교", "스마트폰 CPU GPU 벤치마크", False, "tech"),
        ("소니 A7M4 망원 렌즈 출사 리뷰", "미러리스 카메라 바디 스펙", False, "camera"),
        ("디올 선글라스 신상품 착용 리뷰", "백화점 매장에서 써본 후기", False, "UNKNOWN"),
        ("명품 선글라스 신상품 착용 후기", "백화점 매장 착용 (FOOD 검색 삽입)", False, "UNKNOWN"), # expected_category without weak positive
    ]
    for title, snippet, expected_allow, expected_cat in test_cases:
        expected_cat_arg = "FOOD" if "FOOD 검색 삽입" in snippet else None
        decision = DiscoveryTopicFilter.evaluate(title, snippet, stage="card", expected_category=expected_cat_arg)
        status_str = "ALLOW" if decision.allowed else "BLOCK/SKIP"
        print(f"  [{status_str}] '{title}' -> detected: {decision.detected_category}, blocked: {decision.blocked_category or decision.reason_code}")
        if expected_allow:
            assert decision.allowed, f"Expected allow: {title}"
            assert decision.detected_category == expected_cat
        else:
            assert not decision.allowed or decision.detected_category != "FOOD"

    print("\n==================================================")
    print("🎯 [SMOKE 2/4] TargetedSearchFeedSource Integration Contract Smoke")
    print("==================================================")
    contract = load_runtime_contract()
    print(f"  Loaded Runtime Contract SoT: version={contract.extension_version}, build={contract.runtime_build}")
    assert contract.extension_version == "13.2.3"
    assert contract.runtime_build == "13.2.3-r5"

    rotator = QueryRotator(enabled_categories=["FOOD", "CAFE"], posts_per_query=2)
    spec = rotator.current_spec
    print(f"  QueryRotator initial spec: query='{spec.query}', category='{spec.category}'")
    assert spec.category in ["FOOD", "CAFE"]

    print("\n==================================================")
    print("🔌 [SMOKE 3/4] Gemini Bridge 13.2.3 Synthetic Protocol Smoke")
    print("==================================================")
    bridge = GeminiExtensionBridge()
    server = GeminiBridgeHTTPServer(bridge, port=0)
    server.start()
    try:
        # Preflight before heartbeat -> heartbeat_never_received
        pf1 = bridge.preflight()
        print(f"  Initial Preflight: ready={pf1.ready}, status={pf1.status}")
        assert not pf1.ready and pf1.status == "heartbeat_never_received"

        # Record heartbeat from extension 13.2.3
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
        payload = json.dumps({
            "status": "ready",
            "title": "Gemini",
            "url": "https://gemini.google.com/app",
            "extensionVersion": contract.extension_version,
            "contentBuild": contract.runtime_build,
            "buildId": contract.runtime_build,
            "protocolVersion": contract.protocol_version,
            "bridgeSchemaVersion": contract.bridge_schema_version
        }).encode("utf-8")
        conn.request("POST", "/v1/heartbeat", payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        conn.close()

        # Preflight after heartbeat -> ready
        pf2 = bridge.preflight()
        print(f"  After Heartbeat Preflight: ready={pf2.ready}, status={pf2.status}, version={pf2.extension_version}, build={pf2.content_build}")
        assert pf2.ready and pf2.status == "ready"
    finally:
        server.stop()

    print("\n==================================================")
    print("🌐 [SMOKE 4/4] Real Playwright Browser Session & Page Recovery Smoke")
    print("==================================================")
    import tempfile
    import shutil
    temp_profile = tempfile.mkdtemp(prefix="smoke_profile_")
    session = BrowserSession(headless=True, user_data_dir=temp_profile)
    try:
        session.start()
        print(f"  Browser context started: is_alive={session.is_context_alive()}")
        assert session.is_context_alive()

        detail_page1 = session.get_detail_page()
        print(f"  Detail page 1 created: is_closed={detail_page1.is_closed()}")
        assert not detail_page1.is_closed()

        # Simulate page closed by user / crash
        detail_page1.close()
        print(f"  Detail page 1 closed manually: is_closed={detail_page1.is_closed()}")
        assert detail_page1.is_closed()

        # Check recovery: get_detail_page() must create a new alive page
        detail_page2 = session.get_detail_page()
        print(f"  Detail page 2 recovered: is_closed={detail_page2.is_closed()}")
        assert not detail_page2.is_closed()
        assert detail_page2 != detail_page1

        # Test failure classifier on closed page
        e_page = Exception("Target page, context or browser has been closed")
        kind = classify_playwright_failure(e_page, page=detail_page1, context=session.context, session=session)
        print(f"  Classifier on closed page (context alive): {kind.value}")
        assert kind == BrowserFailureKind.PAGE_CLOSED

    finally:
        session.close(reason="smoke_test_completed")
        shutil.rmtree(temp_profile, ignore_errors=True)
        print("  Browser session closed successfully.")

    print("\n==================================================")
    print("🎉 [ALL SMOKE CHECKS PASSED SUCCESSFULLY]")
    print("==================================================")


if __name__ == "__main__":
    run_smoke()
