"""Deterministic, streaming prefix-cache replay for the frozen SwissAI Qwen protocol.

This module has no dataset loader or label access.  A caller supplies eligible
requests in nondecreasing creation-time order, with complete 16-token blocks
only.  Each call starts with an empty cache, including empty frequency and TTL
state.  Cache capacity is counted in trie nodes, not bytes of GPU memory.

At equal creation times all hits and occupancies are measured against the same
pre-group state.  Requests are then applied in SHA256(request ID) order, so
same-time requests cannot score hits from one another.  State updates inside
the group still follow that deterministic order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import heapq
import math
from typing import Iterable, Iterator, Sequence


TTL_US = 60 * 60 * 1_000_000
DAY_US = 24 * 60 * 60 * 1_000_000
EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
MAX_BUCKET_ID = 2**64 - 1


@dataclass(frozen=True, slots=True)
class CacheRequest:
    """One already-audited request; ``prefix`` excludes the padded last block."""

    created_us: int
    prefix: Sequence[int]
    tie_breaker: bytes  # SHA256(request ID).digest(); never the raw ID.
    admission_probability: float | None = None


@dataclass(frozen=True, slots=True)
class CachePolicySpec:
    name: str
    capacity: int
    eviction: str = "lru"  # "lru" or "lfu"
    admission_threshold: float | None = None  # None admits every new node.

    def __post_init__(self) -> None:
        if not self.name or self.capacity < 1 or self.eviction not in ("lru", "lfu"):
            raise ValueError("invalid_cache_policy_spec")
        if self.admission_threshold is not None and not 0 <= self.admission_threshold <= 1:
            raise ValueError("invalid_admission_threshold")


class _Node:
    __slots__ = ("parent", "bucket_id", "children", "last_access_us", "frequency", "path_hash",
                 "ordinal", "version", "alive")

    def __init__(self, parent: _Node | None, bucket_id: int | None,
                 created_us: int, path_hash: bytes,
                 ordinal: int) -> None:
        self.parent = parent
        self.bucket_id = bucket_id
        self.children: dict[int, _Node] = {}
        self.last_access_us = created_us
        # The admission request is the node's first access for LFU.
        self.frequency = 1
        self.path_hash = path_hash
        self.ordinal = ordinal
        self.version = 0
        self.alive = True


class PrefixCache:
    """One capacity-bounded prefix trie with TTL and leaf-only eviction.

    Expiry and eviction heaps are rebuilt when stale entries grow beyond four
    entries per resident node; memory therefore scales with capacity rather
    than the number of replayed requests.  An admitted request path is protected
    while its new nodes are inserted, which makes all of its first ``C`` blocks
    resident after the request even under LFU tie-breaking.
    """

    def __init__(self, spec: CachePolicySpec) -> None:
        self.spec = spec
        self.root = _Node(None, None, -1, hashlib.sha256(b"").digest(), 0)
        self.size = 0
        self.expirations = 0
        self.evictions = 0
        self._next_ordinal = 1
        self._expiry_heap: list[tuple[int, int, int, _Node]] = []
        self._eviction_heap: list[tuple[object, ...]] = []
        self._now_us: int | None = None

    def _leaf_key(self, node: _Node) -> tuple[object, ...]:
        if self.spec.eviction == "lru":
            return (node.last_access_us, node.path_hash, node.ordinal, node.version, node)
        return (node.frequency, node.last_access_us, node.path_hash,
                node.ordinal, node.version, node)

    def _track(self, node: _Node) -> None:
        heapq.heappush(self._expiry_heap, (node.last_access_us, node.ordinal,
                                          node.version, node))
        if not node.children:
            heapq.heappush(self._eviction_heap, self._leaf_key(node))

    def _all_nodes(self) -> Iterator[_Node]:
        stack = list(self.root.children.values())
        while stack:
            node = stack.pop()
            yield node
            stack.extend(node.children.values())

    def _compact_heaps(self) -> None:
        # A hot node can otherwise add one stale heap entry per request.
        limit = 4 * self.spec.capacity + 1024
        if len(self._expiry_heap) > limit:
            self._expiry_heap = [(n.last_access_us, n.ordinal, n.version, n)
                                 for n in self._all_nodes()]
            heapq.heapify(self._expiry_heap)
        if len(self._eviction_heap) > limit:
            self._eviction_heap = [self._leaf_key(n) for n in self._all_nodes()
                                   if not n.children]
            heapq.heapify(self._eviction_heap)

    def _remove_subtree(self, node: _Node, *, reason: str) -> None:
        parent = node.parent
        assert parent is not None and node.alive
        assert node.bucket_id is not None
        del parent.children[node.bucket_id]
        stack = [node]
        removed = 0
        while stack:
            current = stack.pop()
            stack.extend(current.children.values())
            current.children.clear()
            current.alive = False
            removed += 1
        self.size -= removed
        if reason == "expiry":
            self.expirations += removed
        else:
            self.evictions += removed
        if parent is not self.root and not parent.children:
            heapq.heappush(self._eviction_heap, self._leaf_key(parent))

    def advance_to(self, created_us: int) -> None:
        """Expire before a timestamp's pre-group hit snapshot."""
        if self._now_us is not None and created_us < self._now_us:
            raise ValueError("cache_time_moved_backwards")
        self._now_us = created_us
        cutoff = created_us - TTL_US
        heap = self._expiry_heap
        while heap and heap[0][0] <= cutoff:
            _, _, version, node = heapq.heappop(heap)
            if node.alive and node.version == version and node.last_access_us <= cutoff:
                self._remove_subtree(node, reason="expiry")
        self._compact_heaps()

    def peek_hit_blocks(self, prefix: Sequence[int]) -> int:
        """Read the current consecutive cache hit depth without updating state."""
        node = self.root
        depth = 0
        for bucket_id in prefix[:self.spec.capacity]:
            child = node.children.get(bucket_id)
            if child is None:
                break
            depth += 1
            node = child
        return depth

    def _touch(self, node: _Node, created_us: int) -> None:
        node.last_access_us = created_us
        node.frequency += 1
        node.version += 1
        self._track(node)

    def _evict_one_leaf(self, protected: set[_Node]) -> None:
        skipped: list[tuple[object, ...]] = []
        while self._eviction_heap:
            entry = heapq.heappop(self._eviction_heap)
            node = entry[-1]
            version = entry[-2]
            if not node.alive or node.version != version or node.children:
                continue
            if node in protected:
                skipped.append(entry)
                continue
            self._remove_subtree(node, reason="eviction")
            for candidate in skipped:
                heapq.heappush(self._eviction_heap, candidate)
            return
        for candidate in skipped:
            heapq.heappush(self._eviction_heap, candidate)
        raise RuntimeError("no_evictable_leaf_outside_admitted_path")

    def apply(self, request: CacheRequest) -> None:
        """Update state after all same-time requests have taken hit snapshots."""
        if self._now_us != request.created_us:
            raise ValueError("advance_to_required_before_apply")
        threshold = self.spec.admission_threshold
        probability = request.admission_probability
        if probability is not None and (not math.isfinite(probability) or not 0 <= probability <= 1):
            raise ValueError("invalid_admission_probability")
        if threshold is None or threshold == 0:
            admit = True
        else:
            if probability is None:
                raise ValueError("missing_admission_probability")
            admit = probability >= threshold
        node = self.root
        protected: set[_Node] = set()
        for bucket_id in request.prefix[:self.spec.capacity]:
            child = node.children.get(bucket_id)
            if child is not None:
                self._touch(child, request.created_us)
                node = child
                protected.add(node)
                continue
            if not admit:
                break
            if not isinstance(bucket_id, int) or not 0 <= bucket_id <= MAX_BUCKET_ID:
                raise ValueError("invalid_bucket_id")
            if self.size == self.spec.capacity:
                self._evict_one_leaf(protected)
            # Encoding is fixed-width unsigned big-endian, and hashing is
            # chained from the parent prefix hash. This defines a stable
            # total order for eviction ties without retaining full paths.
            digest = hashlib.sha256(node.path_hash + bucket_id.to_bytes(8, "big")).digest()
            child = _Node(node, bucket_id, request.created_us, digest, self._next_ordinal)
            self._next_ordinal += 1
            node.children[bucket_id] = child
            self.size += 1
            # The former leaf is no longer eligible; lazy heap validation
            # excludes it because it now has a child.
            self._track(child)
            node = child
            protected.add(node)
        self._compact_heaps()


@dataclass(slots=True)
class _Stats:
    requests: int = 0
    eligible_blocks: int = 0
    hit_blocks: int = 0
    over_capacity_requests: int = 0
    over_capacity_blocks: int = 0
    occupancy_block_samples: int = 0
    expired_nodes: int = 0
    evicted_nodes: int = 0

    def add_request(self, blocks: int, hits: int, occupancy: int, capacity: int) -> None:
        self.requests += 1
        self.eligible_blocks += blocks
        self.hit_blocks += hits
        self.occupancy_block_samples += occupancy
        if blocks > capacity:
            self.over_capacity_requests += 1
            self.over_capacity_blocks += blocks - capacity

    def as_dict(self, capacity: int) -> dict[str, int | float]:
        return {
            "requests": self.requests,
            "eligible_blocks": self.eligible_blocks,
            "hit_blocks": self.hit_blocks,
            "hit_ratio": self.hit_blocks / self.eligible_blocks if self.eligible_blocks else float("nan"),
            "over_capacity_requests": self.over_capacity_requests,
            "over_capacity_blocks": self.over_capacity_blocks,
            "mean_occupancy_blocks_at_arrival": (self.occupancy_block_samples / self.requests
                                                  if self.requests else float("nan")),
            "mean_occupancy_fraction_at_arrival": (self.occupancy_block_samples / (self.requests * capacity)
                                                    if self.requests else float("nan")),
            "expired_nodes": self.expired_nodes,
            "evicted_nodes": self.evicted_nodes,
        }


def _groups(requests: Iterable[CacheRequest]) -> Iterator[tuple[int, list[CacheRequest]]]:
    group: list[CacheRequest] = []
    current_us: int | None = None
    for request in requests:
        if not isinstance(request.created_us, int) or request.created_us < 0:
            raise ValueError("invalid_created_us")
        if not isinstance(request.tie_breaker, bytes) or len(request.tie_breaker) != 32:
            raise ValueError("tie_breaker_must_be_sha256_digest")
        if not request.prefix:
            raise ValueError("empty_complete_block_prefix")
        if current_us is None:
            current_us = request.created_us
        if request.created_us < current_us:
            raise ValueError("requests_must_be_time_sorted")
        if request.created_us != current_us:
            group.sort(key=lambda row: row.tie_breaker)
            if len({row.tie_breaker for row in group}) != len(group):
                raise ValueError("duplicate_request_hash_at_one_timestamp")
            yield current_us, group
            group = []
            current_us = request.created_us
        group.append(request)
    if group:
        assert current_us is not None
        group.sort(key=lambda row: row.tie_breaker)
        if len({row.tie_breaker for row in group}) != len(group):
            raise ValueError("duplicate_request_hash_at_one_timestamp")
        yield current_us, group


def replay_requests(requests: Iterable[CacheRequest], specs: Sequence[CachePolicySpec],
                    *, split_start_us: int, split_end_us: int) -> dict[str, dict]:
    """Replay one split from cold caches, excluding its first 60 minutes from scores.

    The caller owns data provenance, eligibility, chronological split selection,
    training and frozen probability generation. No future label is consumed here.
    ``requests`` must contain only this split's eligible, time-sorted arrivals.
    """
    if split_end_us <= split_start_us:
        raise ValueError("invalid_split_interval")
    if not specs or len({spec.name for spec in specs}) != len(specs):
        raise ValueError("empty_or_duplicate_policy_specs")
    caches = {spec.name: PrefixCache(spec) for spec in specs}
    totals = {spec.name: _Stats() for spec in specs}
    days: dict[str, dict[str, _Stats]] = {spec.name: {} for spec in specs}
    score_start = split_start_us + TTL_US
    for created_us, group in _groups(requests):
        if not split_start_us <= created_us < split_end_us:
            raise ValueError("request_outside_split")
        scored = created_us >= score_start
        day = date.fromordinal(EPOCH_ORDINAL + created_us // DAY_US).isoformat() if scored else ""
        for spec in specs:
            cache = caches[spec.name]
            before_expirations = cache.expirations
            before_evictions = cache.evictions
            cache.advance_to(created_us)
            occupancy = cache.size
            # All hits are computed before this timestamp changes the trie.
            hits = [cache.peek_hit_blocks(row.prefix) for row in group]
            if scored:
                day_stats = days[spec.name].setdefault(day, _Stats())
                for row, hit in zip(group, hits):
                    blocks = len(row.prefix)
                    totals[spec.name].add_request(blocks, hit, occupancy, spec.capacity)
                    day_stats.add_request(blocks, hit, occupancy, spec.capacity)
            for row in group:
                cache.apply(row)
            if scored:
                expired = cache.expirations - before_expirations
                evicted = cache.evictions - before_evictions
                totals[spec.name].expired_nodes += expired
                totals[spec.name].evicted_nodes += evicted
                day_stats.expired_nodes += expired
                day_stats.evicted_nodes += evicted
    return {
        spec.name: {
            "policy": {"capacity_blocks": spec.capacity, "eviction": spec.eviction,
                       "admission_threshold": spec.admission_threshold,
                       "ttl_seconds": TTL_US // 1_000_000},
            "total": totals[spec.name].as_dict(spec.capacity),
            "days": {day: stat.as_dict(spec.capacity) for day, stat in sorted(days[spec.name].items())},
        }
        for spec in specs
    }


def default_policy_specs(capacities: Sequence[int] = (4096,),
                         thresholds: Sequence[float] = (0, 0.1, 0.2, 0.4, 0.6, 0.8)
                         ) -> list[CachePolicySpec]:
    """Construct the frozen LRU/LFU and admission policies."""
    specs: list[CachePolicySpec] = []
    for capacity in capacities:
        specs.append(CachePolicySpec(f"lru_c{capacity}", capacity, "lru"))
        specs.append(CachePolicySpec(f"lfu_c{capacity}", capacity, "lfu"))
        for threshold in thresholds:
            label = str(threshold).replace(".", "p")
            specs.append(CachePolicySpec(f"admit_t{label}_c{capacity}", capacity,
                                         "lru", float(threshold)))
    return specs
