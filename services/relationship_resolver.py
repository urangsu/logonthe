"""
Relationship resolution and quality contract enforcement based on buddy list collection.
"""
from enum import Enum
from typing import Dict, NamedTuple, Optional, Set, Tuple

from services.buddy_list_collector import BuddyCollectionResult, BuddyInfo


class RelationshipType(str, Enum):
    MUTUAL = "mutual"
    NON_MUTUAL = "non_mutual"
    UNKNOWN = "unknown"


class RelationshipResolution(NamedTuple):
    blog_id: str
    rel_type: RelationshipType
    evidence: str
    buddy_info: Optional[BuddyInfo] = None


class RelationshipResolver:
    """
    Resolves relationship status for feed items against verified buddy collection.
    Enforces fail-closed semantics:
    - Never assumes a missing blog ID is non-mutual.
    - Preserves explicit evidence for all classifications.
    """

    def __init__(self, collection_result: Optional[BuddyCollectionResult] = None):
        self.result = collection_result
        self._map: Dict[str, BuddyInfo] = {}
        if collection_result and collection_result.buddies:
            for b_id, info in collection_result.buddies.items():
                if b_id:
                    self._map[b_id.strip().lower()] = info

    def resolve(self, blog_id: Optional[str]) -> RelationshipResolution:
        if not blog_id or not str(blog_id).strip():
            return RelationshipResolution("", RelationshipType.UNKNOWN, "missing_blog_id", None)

        canonical = str(blog_id).strip().lower()
        info = self._map.get(canonical)

        # "실행 도중 피드에서 목록에 없는 ID를 만났다면 UNKNOWN으로 분류한다."
        if not info:
            return RelationshipResolution(
                canonical,
                RelationshipType.UNKNOWN,
                "not_in_collected_buddy_list",
                None,
            )

        b_type = getattr(info, "buddy_type", "unknown")
        evidence = getattr(info, "buddy_type_evidence", "") or f"buddy_type:{b_type}"

        if b_type == "서로이웃":
            return RelationshipResolution(canonical, RelationshipType.MUTUAL, evidence, info)
        elif b_type == "이웃":
            return RelationshipResolution(canonical, RelationshipType.NON_MUTUAL, evidence, info)
        else:
            return RelationshipResolution(canonical, RelationshipType.UNKNOWN, evidence, info)

    def is_execution_blocked(self) -> Tuple[bool, str]:
        """
        Validates relationship collection quality contract before starting neighbor feed run.
        Returns (is_blocked, reason).
        """
        if self.result is None:
            return True, "relationship_collection_missing"

        if self.result.state in ("failed", "cancelled"):
            return True, self.result.error or f"collection_state_{self.result.state}"

        if "relationship_parse_incomplete" in getattr(self.result, "quality_issues", []):
            return True, "relationship_parse_incomplete"

        # Check actual unknown items in buddies
        unknown_count = getattr(self.result, "unknown_count", 0)
        if not unknown_count and self.result.buddies:
            unknown_count = sum(
                1 for b in self.result.buddies.values()
                if getattr(b, "buddy_type", "") not in ("서로이웃", "이웃")
            )

        if unknown_count > 0:
            return True, f"unresolved_relationships_present:{unknown_count}"

        return False, ""

    def get_mutual_blog_ids(self) -> Set[str]:
        return {
            b_id
            for b_id, info in self._map.items()
            if getattr(info, "buddy_type", "") == "서로이웃"
        }

    def get_summary(self) -> Dict[str, int]:
        mutual = sum(1 for b in self._map.values() if getattr(b, "buddy_type", "") == "서로이웃")
        non_mutual = sum(1 for b in self._map.values() if getattr(b, "buddy_type", "") == "이웃")
        unknown = sum(1 for b in self._map.values() if getattr(b, "buddy_type", "") not in ("서로이웃", "이웃"))
        return {
            "total": len(self._map),
            "mutual": mutual,
            "non_mutual": non_mutual,
            "unknown": unknown,
        }
