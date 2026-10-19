import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from swissai_prefix_cache_core import (
    TTL_US, CachePolicySpec, CacheRequest, PrefixCache, default_policy_specs,
    replay_requests,
)


def request(t, prefix, label, probability=0.5):
    return CacheRequest(t, tuple(prefix), hashlib.sha256(label.encode()).digest(), probability)


class PrefixCacheCoreTests(unittest.TestCase):
    def test_same_timestamp_cannot_score_mutual_hits(self):
        rows = [
            request(1_000_000, (1,), "warm"),
            # Deliberately pass these in reverse ID-hash order. Replay sorts
            # the group for state updates but scores both on its prior state.
            request(TTL_US, (2,), "b"),
            request(TTL_US, (2,), "a"),
            request(TTL_US + 1, (2,), "later"),
        ]
        result = replay_requests(rows, [CachePolicySpec("lru", 2)],
                                 split_start_us=0, split_end_us=2 * TTL_US)
        total = result["lru"]["total"]
        self.assertEqual(total["requests"], 3)
        self.assertEqual(total["hit_blocks"], 1)
        self.assertEqual(total["eligible_blocks"], 3)
        self.assertEqual(total["hit_ratio"], 1 / 3)

    def test_ttl_expires_exactly_at_sixty_minutes_and_removes_descendants(self):
        cache = PrefixCache(CachePolicySpec("lru", 4))
        cache.advance_to(0)
        cache.apply(request(0, (1, 2), "first"))
        self.assertEqual(cache.size, 2)
        cache.advance_to(TTL_US - 1)
        self.assertEqual(cache.peek_hit_blocks((1, 2)), 2)
        cache.advance_to(TTL_US)
        self.assertEqual(cache.peek_hit_blocks((1, 2)), 0)
        self.assertEqual(cache.expirations, 2)
        self.assertEqual(cache.size, 0)

    def test_lru_and_lfu_choose_different_leaves(self):
        lru = PrefixCache(CachePolicySpec("lru", 2, "lru"))
        lfu = PrefixCache(CachePolicySpec("lfu", 2, "lfu"))
        for t, block in enumerate((1, 1, 1, 2, 3)):
            for cache in (lru, lfu):
                cache.advance_to(t)
                cache.apply(request(t, (block,), f"{t}"))
        self.assertEqual(lru.peek_hit_blocks((1,)), 0)
        self.assertEqual(lru.peek_hit_blocks((2,)), 1)
        self.assertEqual(lfu.peek_hit_blocks((1,)), 1)
        self.assertEqual(lfu.peek_hit_blocks((2,)), 0)
        self.assertEqual(lru.evictions, 1)
        self.assertEqual(lfu.evictions, 1)

    def test_eviction_keeps_trie_connected_and_caps_overlong_request(self):
        cache = PrefixCache(CachePolicySpec("lfu", 3, "lfu"))
        for t, prefix in enumerate(((1, 2), (1, 3), (4, 5, 6, 7))):
            cache.advance_to(t)
            cache.apply(request(t, prefix, str(t)))
            self.assertLessEqual(cache.size, 3)
        self.assertEqual(cache.peek_hit_blocks((4, 5, 6, 7)), 3)
        self.assertEqual(cache.size, 3)
        self.assertEqual(cache.evictions, 3)

    def test_threshold_zero_matches_lru_with_ties_expiry_and_long_paths(self):
        rows = [
            request(0, (1, 2, 3), "a", 0.0),
            request(1, (1, 2), "b", 0.9),
            request(TTL_US, (4, 5, 6, 7), "c", 0.1),
            request(TTL_US, (4, 5), "d", 0.7),
            request(TTL_US + 1, (1, 2), "e", 1.0),
            request(TTL_US + 2, (4, 5), "f", 0.0),
            request(2 * TTL_US + 3, (4,), "g", 0.0),
        ]
        specs = [CachePolicySpec("lru", 3),
                 CachePolicySpec("admit0", 3, "lru", 0.0)]
        result = replay_requests(rows, specs, split_start_us=0,
                                 split_end_us=3 * TTL_US)
        self.assertEqual(result["lru"]["total"], result["admit0"]["total"])
        self.assertEqual(result["lru"]["days"], result["admit0"]["days"])
        self.assertEqual(result["lru"]["total"]["over_capacity_requests"], 1)
        self.assertEqual(result["lru"]["total"]["over_capacity_blocks"], 1)

    def test_equal_time_input_order_does_not_change_state(self):
        a = request(TTL_US, (1,), "a")
        b = request(TTL_US, (2,), "b")
        probe = request(TTL_US + 1, (1,), "probe")
        spec = CachePolicySpec("lru", 1)
        first = replay_requests([a, b, probe], [spec], split_start_us=0,
                                split_end_us=2 * TTL_US)
        reversed_input = replay_requests([b, a, probe], [spec], split_start_us=0,
                                         split_end_us=2 * TTL_US)
        self.assertEqual(first, reversed_input)

    def test_high_threshold_still_updates_existing_hits(self):
        rows = [
            request(1_000_000, (1,), "warm", 0.9),
            request(TTL_US, (1,), "hit", 0.0),
            request(TTL_US + 1, (1, 2), "miss", 0.0),
            request(TTL_US + 2, (1,), "hitagain", 0.0),
        ]
        spec = CachePolicySpec("selective", 2, "lru", 0.8)
        result = replay_requests(rows, [spec], split_start_us=0,
                                 split_end_us=2 * TTL_US)["selective"]["total"]
        self.assertEqual(result["hit_blocks"], 3)
        self.assertEqual(result["eligible_blocks"], 4)
        self.assertEqual(result["expired_nodes"], 0)

    def test_heap_memory_remains_capacity_bounded_for_hot_prefix(self):
        cache = PrefixCache(CachePolicySpec("lru", 1))
        for t in range(10_000):
            cache.advance_to(t)
            cache.apply(request(t, (1,), f"{t}"))
        self.assertEqual(cache.size, 1)
        self.assertLessEqual(len(cache._expiry_heap), 4 * cache.spec.capacity + 1024)
        self.assertLessEqual(len(cache._eviction_heap), 4 * cache.spec.capacity + 1024)

    def test_rejects_bad_order_and_duplicate_same_time_hash(self):
        spec = CachePolicySpec("lru", 2)
        with self.assertRaisesRegex(ValueError, "time_sorted"):
            replay_requests([request(1, (1,), "a"), request(0, (1,), "b")],
                            [spec], split_start_us=0, split_end_us=TTL_US + 1)
        with self.assertRaisesRegex(ValueError, "duplicate_request_hash"):
            replay_requests([request(0, (1,), "a"), request(0, (2,), "a")],
                            [spec], split_start_us=0, split_end_us=TTL_US + 1)

    def test_default_specs_include_frozen_policy_grid(self):
        specs = default_policy_specs((1024, 4096, 16384))
        self.assertEqual(len(specs), 24)
        self.assertEqual(len({spec.name for spec in specs}), 24)
        self.assertEqual([spec.admission_threshold for spec in specs if spec.name.startswith("admit_")][:6],
                         [0.0, 0.1, 0.2, 0.4, 0.6, 0.8])


if __name__ == "__main__":
    unittest.main()
