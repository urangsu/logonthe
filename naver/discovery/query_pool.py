from dataclasses import dataclass
from typing import List, Dict, Optional, Set
import random


@dataclass(frozen=True)
class QuerySpec:
    category: str
    query: str


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
    생활형 주제 검색어 순환 풀 (v9-lite / V13.3)
    - 선택된 카테고리별 검색어 및 사용자 지정 검색어와 QuerySpec(category, query) 보존
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

        # QuerySpec 리스트 조합
        self.specs: List[QuerySpec] = []
        for cat in self.enabled_categories:
            if cat in DISCOVERY_QUERIES:
                for q in DISCOVERY_QUERIES[cat]:
                    self.specs.append(QuerySpec(cat, q))

        # 사용자 정의 검색어 추가 (CUSTOM 카테고리)
        for cq in self.custom_queries:
            if not any(s.query == cq for s in self.specs):
                self.specs.append(QuerySpec("CUSTOM", cq))

        if not self.specs:
            self.specs = [
                QuerySpec("FOOD", "내돈내산 맛집 후기"),
                QuerySpec("CAFE", "동네 카페 후기"),
                QuerySpec("PARENTING", "육아 일상"),
                QuerySpec("LIVING", "살림 일상"),
            ]

        # 세션 시작 시 다양성을 위해 셔플
        random.shuffle(self.specs)

        # 하위 호환성 문자열 리스트
        self.queries: List[str] = [s.query for s in self.specs]

        self._current_index = 0
        self._current_query_post_count = 0

    @property
    def current_spec(self) -> QuerySpec:
        if not self.specs:
            return QuerySpec("FOOD", "내돈내산 맛집 후기")
        return self.specs[self._current_index % len(self.specs)]

    @property
    def current_query(self) -> str:
        return self.current_spec.query

    @property
    def current_category(self) -> str:
        return self.current_spec.category

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
        self._current_index += 1
        self._current_query_post_count = 0
        return self.current_query
