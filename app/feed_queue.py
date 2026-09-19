"""
Sequential, thread-safe Feed Queue module.
Enforces FIFO ordering, forbids background prefetch, tracks active post key,
and permits scrolling only when the queue is completely empty and no post is active.
"""
from collections import deque
import threading
from typing import Deque, List, Optional
from app.models import FeedPost


class FeedQueue:
    """
    Sequential FeedQueue with strict FIFO ordering and scroll safety.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._items: Deque[FeedPost] = deque()
        self.active_post_key: Optional[str] = None
        self._seen_keys: set[str] = set()

    def push(self, posts: List[FeedPost]) -> int:
        """
        Pushes newly discovered posts into the FIFO queue.
        Duplicates already present or seen in the queue are ignored.
        Returns count of successfully queued posts.
        """
        added = 0
        with self._lock:
            for p in posts:
                key = p.key or p.url
                if key not in self._seen_keys:
                    self._seen_keys.add(key)
                    self._items.append(p)
                    added += 1
        return added

    def pop(self) -> Optional[FeedPost]:
        """
        Pops the next post from the head of the FIFO queue.
        """
        with self._lock:
            if not self._items:
                return None
            return self._items.popleft()

    def peek(self) -> Optional[FeedPost]:
        """
        Inspects the next post without removing it.
        """
        with self._lock:
            if not self._items:
                return None
            return self._items[0]

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._items) == 0

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def set_active(self, post_key: Optional[str]) -> None:
        with self._lock:
            self.active_post_key = post_key

    def has_active(self) -> bool:
        with self._lock:
            return self.active_post_key is not None

    def can_scroll(self) -> bool:
        """
        Strict constraint: scrolling the feed to load more posts is ONLY allowed
        when the queue is completely drained AND no post is currently being processed.
        """
        with self._lock:
            return len(self._items) == 0 and self.active_post_key is None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.active_post_key = None
