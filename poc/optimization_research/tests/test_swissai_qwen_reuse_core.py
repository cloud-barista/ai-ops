import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from swissai_qwen_reuse_core import (
    HORIZON_US, MINUTE_US, Request, future_reuse_labels,
    historical_features,
)


def request(minute: int, buckets: tuple[int, ...], remainder: int = 0) -> Request:
    return Request(minute * MINUTE_US, buckets, len(buckets) * 16 + remainder)


class SwissaiQwenReuseCoreTests(unittest.TestCase):
    def test_later_longer_prefix_matches_and_same_time_does_not(self):
        records = [
            request(61, (7, 8)),
            request(61, (7, 8, 9)),
            request(62, (7, 8, 10)),
            request(63, (7, 9)),
        ]
        labels = future_reuse_labels(records, 0, 240 * MINUTE_US)
        self.assertEqual(labels, [1, 0, 0, 0])

    def test_exact_60_minute_boundary_expiry_and_split_censor(self):
        records = [
            request(59, (1,)),  # warmup: never scored
            request(61, (2,)),
            request(121, (2,)),  # exactly 60 minutes later
            request(122, (3,)),
            request(180, (8,)),  # its horizon ends at the excluded boundary
            request(183, (3,)),  # 61 minutes later
            request(201, (4,)),  # horizon crosses split end
        ]
        self.assertEqual(
            future_reuse_labels(records, 0, 240 * MINUTE_US),
            [-1, 1, 0, 0, -1, -1, -1],
        )

    def test_features_use_only_earlier_events_and_distinguish_exact_from_prefix(self):
        records = [
            request(61, (5,)),
            request(62, (5, 9)),
            request(63, (5,)),
            request(63, (5, 7)),
            request(64, (5,), remainder=3),
        ]
        features = historical_features(records, 0, 240 * MINUTE_US)
        self.assertEqual(features[0]["exact_60m"], 0)
        self.assertTrue(math.isnan(features[0]["seconds_since_exact"]))
        self.assertEqual(features[2]["arrivals_5m"], 2)
        self.assertEqual(features[2]["exact_5m"], 1)
        self.assertEqual(features[2]["exact_60m"], 1)
        self.assertEqual(features[2]["prefix_1_60m"], 2)
        self.assertEqual(features[2]["seconds_since_exact"], 120)
        self.assertEqual(features[3]["prefix_1_60m"], 2)
        self.assertEqual(features[4]["exact_60m"], 2)
        self.assertEqual(features[4]["prefix_1_60m"], 4)
        self.assertEqual(features[4]["token_remainder"], 3)

    def test_window_old_events_expire_and_split_state_resets(self):
        first = request(61, (4, 5, 6, 7))
        late = request(122, (4, 5, 6, 7))
        features = historical_features([first, late], 0, 240 * MINUTE_US)
        self.assertEqual(features[1]["exact_60m"], 0)
        self.assertEqual(features[1]["prefix_1_60m"], 0)
        self.assertEqual(features[1]["seconds_since_exact"], 61 * 60)
        next_split = historical_features(
            [request(301, (4, 5, 6, 7))],
            240 * MINUTE_US, 480 * MINUTE_US,
        )
        self.assertEqual(next_split[0]["exact_60m"], 0)

    def test_selected_output_still_uses_all_earlier_requests_as_history(self):
        records = [request(61, (2, 3)), request(62, (2, 3)), request(63, (2, 3))]
        selected = historical_features(
            records, 0, 240 * MINUTE_US, selected_indices={2},
        )
        self.assertEqual(set(selected), {2})
        self.assertEqual(selected[2]["exact_5m"], 2)
        self.assertEqual(selected[2]["arrivals_5m"], 2)

    def test_invalid_and_out_of_order_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            Request(0, (1, 2), 16)
        with self.assertRaises(ValueError):
            future_reuse_labels([request(62, (1,)), request(61, (1,))], 0, 240 * MINUTE_US)
        with self.assertRaises(ValueError):
            historical_features([request(241, (1,))], 0, 240 * MINUTE_US)

    def test_future_reuse_matches_pairwise_oracle_on_random_short_trace(self):
        rng = random.Random(42)
        records = []
        for _ in range(140):
            minute = rng.randrange(0, 240)
            head = rng.choice((1, 2, 3))
            tail = rng.choice((4, 5, 6))
            prefix = (head,) if rng.random() < 0.3 else (head, tail)
            records.append(request(minute, prefix))
        records.sort(key=lambda value: value.created_utc_us)
        predicted = future_reuse_labels(records, 0, 240 * MINUTE_US)
        expected = []
        for current in records:
            t = current.created_utc_us
            if not (HORIZON_US <= t and t + HORIZON_US < 240 * MINUTE_US):
                expected.append(-1)
                continue
            has_reuse = any(
                t < future.created_utc_us <= t + HORIZON_US
                and future.bucket_ids[:len(current.bucket_ids)] == current.bucket_ids
                for future in records
            )
            expected.append(int(has_reuse))
        self.assertEqual(predicted, expected)


if __name__ == "__main__":
    unittest.main()
