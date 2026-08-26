from typing import List, Dict, Optional, Set
import random


DISCOVERY_QUERIES: Dict[str, List[str]] = {
    "FOOD": [
        "내돈내산 맛집 후기",
        "동네 맛집 후기",
        "가족 외식 후기",
        "주말 맛집",
    ],
    "CAFE": [
        "동네 카페 후기",
        "카페 내돈내산",
        "디저트 카페",
        "브런치 카페 후기",
    ],
    "PARENTING": [
        "육아 일상",
        "아이랑 주말",
        "아이랑 나들이",
        "아기랑 카페",
        "아이랑 체험",
    ],
    "LIVING": [
        "살림 일상",
        "집밥 일상",
        "정리 수납",
        "생활용품 후기",
        "집꾸미기",
    ],
    "TRAVEL": [
        "주말 나들이",
        "가족여행 후기",
        "국내여행 후기",
    ],
    "LIFESTYLE": [
        "일상 기록",
        "주말 일상",
        "데이트 일상",
    ],
}


class QueryRotator:
    """
    생활형 주제 검색어 순환 풀 (v9-lite)
    - 선택된 카테고리별 검색어 및 사용자 지정 검색어를 순환
    - 검색어당 2~3개 처리 후 다음 검색어로 자연스럽게 로테이션
    """
    def __init__(
        self,
        enabled_categories: Optional[List[str]] = None,
        custom_queries: Optional[List[str]] = None,
        posts_per_query: int = 3
    ):
        self.posts_per_query = max(1, posts_per_query)
        self.enabled_categories = enabled_categories or list(DISCOVERY_QUERIES.keys())
        self.custom_queries = [q.strip() for q in (custom_queries or []) if q.strip()]

        # 검색어 리스트 조합
        self.queries: List[str] = []
        for cat in self.enabled_categories:
            if cat in DISCOVERY_QUERIES:
                self.queries.extend(DISCOVERY_QUERIES[cat])

        # 사용자 정의 검색어 추가
        for cq in self.custom_queries:
            if cq not in self.queries:
                self.queries.append(cq)

        if not self.queries:
            # 폴백: 기본 맛집/카페/일상
            self.queries = ["내돈내산 맛집 후기", "동네 카페 후기", "육아 일상", "살림 일상"]

        # 세션 시작 시 다양성을 위해 약간의 셔플 (순환 순서 섞기)
        random.shuffle(self.queries)

        self._current_index = 0
        self._current_query_post_count = 0

    @property
    def current_query(self) -> str:
        if not self.queries:
            return "내돈내산 맛집 후기"
        return self.queries[self._current_index % len(self.queries)]

    def record_post_found(self) -> bool:
        """
        포스트를 찾았을 때 카운트를 증가시키고,
        posts_per_query에 도달하면 다음 쿼리로 전환 (전환 시 True 반환)
        """
        self._current_query_post_count += 1
        if self._current_query_post_count >= self.posts_per_query:
            self.next_query()
            return True
        return False

    def next_query(self) -> str:
        """강제로 다음 쿼리로 전환"""
        self._current_index = (self._current_index + 1) % len(self.queries)
        self._current_query_post_count = 0
        return self.current_query
