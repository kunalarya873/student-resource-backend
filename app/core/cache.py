import asyncio
import time
from typing import Any, TypeVar

T = TypeVar("T")


class InMemoryTTLCache:
    """
    High-performance, async-safe in-memory L1 cache with TTL expiration.
    Used for caching hot, read-heavy, low-churn metadata (departments, academic years, roles).
    """

    def __init__(self, default_ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl_seconds
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Any | None:
        """Retrieve item if present and not expired."""
        async with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            value, expires_at = self._cache[key]
            if time.time() > expires_at:
                del self._cache[key]
                self._misses += 1
                return None

            self._hits += 1
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store item with explicit or default TTL."""
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = time.time() + effective_ttl
        async with self._lock:
            self._cache[key] = (value, expires_at)

    async def invalidate(self, key: str) -> bool:
        """Manually invalidate an entry."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """Clear all entries."""
        async with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        """Observability telemetry for cache hit-ratio."""
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
        }


# Global L1 metadata cache instance
metadata_cache = InMemoryTTLCache(default_ttl_seconds=600)
