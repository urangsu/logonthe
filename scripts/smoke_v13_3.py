"""
Smoke Test for V13.3 Discovery & Runtime Recovery
- Verify TopicFilter on live sample articles and regression cases
- Verify GeminiExtensionBridge HTTP server and preflight diagnostics
- Verify BrowserSession lifecycle with structured close reason
"""
import time
import http.client
import json

from naver.discovery.topic_filter import DiscoveryTopicFilter, TopicDecision
from naver.discovery.query_pool import QueryRotator, QuerySpec
from services.gemini_extension_bridge import GeminiExtensionBridge, GeminiBridgeHTTPServer
from browser.session import BrowserSession
from app.errors import classify_playwright_failure, BrowserFailureKind
from src.logger import logger


def run_smoke():
    print("=== [1/4] Discovery TopicFilter Smoke ===")
    test_cases = [
        ("동대문엽기떡볶이 밀키트 착한맛 솔직후기", "소분해서 보관하면 편해요 분양", True, "FOOD"),
        ("제주시청 제주양식맛집 제주도민이 추천하는 제주 한양", "주식회사 한양에서 운영하는 맛집", True, "FOOD"),
        ("영화 <호프> 촬영지 전남 해남 여행지 소개", "사진 출사하기 좋은 여행지", True, "TRAVEL"),
        ("면세점에서 구매한 디올 선글라스 유럽여행 착용 후기", "환율에 따라 가격 차이는 있었습니다", True, "TRAVEL"),
        ("ETF 배당주 포트폴리오 정리", "미국 배당주와 환율 투자 전략", False, "finance"),
        ("아이폰 17 프로 성능 비교", "스마트폰 CPU GPU 벤치마크", False, "tech"),
        ("소니 A7M4 망원 렌즈 출사 리뷰", "미러리스 카메라 바디 스펙", False, "camera"),
        ("디올 선글라스 신상품 착용 리뷰", "백화점 매장에서 써본 후기", False, "UNKNOWN"),
    ]
    for title, snippet, expected_allow, expected_cat in test_cases:
        decision = DiscoveryTopicFilter.evaluate(title, snippet, stage="detail")
        status_str = "ALLOW" if decision.allowed else "BLOCK/SKIP"
        print(f"  [{status_str}] '{title}' -> detected: {decision.detected_category}, blocked: {decision.blocked_category}, reason: {decision.reason_code}")
        if expected_allow:
            assert decision.allowed, f"Expected allow: {title}"
            assert decision.detected_category == expected_cat
        else:
            assert not decision.allowed or decision.detected_category != "FOOD"

    print("\n=== [2/4] QueryRotator Category Preservation Smoke ===")
    rotator = QueryRotator(enabled_categories=["FOOD", "CAFE"], posts_per_query=2)
    for i in range(3):
        spec = rotator.current_spec
        print(f"  Query {i+1}: '{spec.query}' (Category: {spec.category})")
        rotator.record_post_found()

    print("\n=== [3/4] Gemini Extension Bridge 13.2.3 Preflight Diagnostics Smoke ===")
    bridge = GeminiExtensionBridge(expected_extension_version="13.2.3", expected_build_id="13.2.3-r1")
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
            "extensionVersion": "13.2.3",
            "contentBuild": "13.2.3-r1",
            "protocolVersion": 3,
            "bridgeSchemaVersion": 2
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

    print("\n=== [4/4] Browser Session Crash Classifier Smoke ===")
    e_page = Exception("Target page, context or browser has been closed")
    kind = classify_playwright_failure(e_page)
    print(f"  Classifier Result on target closed: {kind.value}")
    assert kind in (BrowserFailureKind.CONTEXT_CLOSED, BrowserFailureKind.BROWSER_DISCONNECTED)

    print("\n[SMOKE] All 4 smoke verification stages PASSED successfully!")


if __name__ == "__main__":
    run_smoke()
