"""Tests for temporal leakage, conditional labels and workload separation."""
import importlib.util
from pathlib import Path

import polars as pl
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/train_alibaba_gpu_sm_hour.py'
SPEC = importlib.util.spec_from_file_location('alibaba_sm_hour', SCRIPT)
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def workload(low=0, high=70):
    return next(f'synthetic-{i}' for i in range(10000)
                if low <= study.bucket(f'synthetic-{i}') < high)


def rows(start, *, pod='pod', work=None, count=4, gpu_count=1):
    work = workload() if work is None else work
    return [dict(pod_id=pod, workload_id=work, server_id='server',
                 gpu_request=1.0, server_gpu_count=gpu_count,
                 gpu_spec_public='GPU_A', priority_class='HP',
                 cpu_request_cores=4.0, avg_gpu_sm_util=float(i * 5),
                 day=(start + i) // 24, hour=(start + i) % 24)
            for i in range(count)]


def prepare(data):
    return study.make_examples(pl.DataFrame(data))


def check_midnight_history_and_target_are_connected_with_correct_availability():
    frame, _ = prepare(rows(109 * 24 + 22))
    assert len(frame) == 1
    r = frame.iloc[0]
    assert r.t == 110 * 24 and r.target_hour_index == 110 * 24 + 1
    assert r.sm_lag2 == 0 and r.sm_lag1 == 5 and r.sm_current == 10
    assert r.target_sm == 15
    assert r.measurement_available_hour_index == r.target_hour_index
    assert r.target_available_hour_index == r.target_hour_index + 1


def check_missing_or_invalid_future_is_excluded_never_zero_filled():
    frame, _ = prepare(rows(109 * 24, count=3))
    assert len(frame) == 0
    data = rows(109 * 24)
    data[-1]['avg_gpu_sm_util'] = float('nan')
    frame, _ = prepare(data)
    assert len(frame) == 0


def check_duplicate_key_excludes_all_rows_including_other_server():
    data = rows(109 * 24)
    copy = dict(data[2], server_id='other-server')
    frame, audit = prepare(data + [copy])
    assert len(frame) == 0
    assert audit['duplicate_pod_hour_keys_excluded'] == 1
    assert audit['duplicate_rows_excluded'] == 2


def check_history_or_future_server_movement_excluded(index):
    data = rows(109 * 24)
    data[index]['server_id'] = 'moved'
    frame, _ = prepare(data)
    assert len(frame) == 0


def check_missing_or_changing_workload_excludes_pod(value):
    data = rows(109 * 24)
    data[3]['workload_id'] = value
    frame, _ = prepare(data)
    assert len(frame) == 0


def check_target_never_crosses_train_end_and_validation_can_use_past_date():
    frame, _ = prepare(rows(112 * 24 + 20, count=5))
    assert frame.t.tolist() == [112 * 24 + 22]
    val, _ = prepare(rows(112 * 24 + 22, work=workload(70, 85)))
    assert val.split.tolist() == ['validation']
    assert val.t.tolist() == [113 * 24]


def check_workload_buckets_and_pods_are_disjoint_across_dates():
    data = (rows(109 * 24, pod='train', work=workload())
            + rows(113 * 24, pod='val', work=workload(70, 85))
            + rows(114 * 24, pod='test', work=workload(85, 100))
            + rows(114 * 24, pod='old-train-workload', work=workload()))
    frame, _ = prepare(data)
    assert set(frame.split) == {'train', 'validation', 'test'}
    assert 'old-train-workload' not in frame.pod_id.tolist()
    assert not set(study.FEATURES) & {'target_sm', 'pod_id', 'workload_id',
                                    'server_id', 'ready_status', 'next_gpu_count'}


def check_strict_cohort_requires_single_gpu_server_in_all_four_windows():
    data = rows(109 * 24)
    data[-1]['server_gpu_count'] = 2
    frame, _ = prepare(data)
    assert len(frame) == 1
    assert not frame.strict_single_gpu_server.any()


class TemporalDataTests(unittest.TestCase):
    def test_midnight(self):
        check_midnight_history_and_target_are_connected_with_correct_availability()

    def test_future(self):
        check_missing_or_invalid_future_is_excluded_never_zero_filled()

    def test_duplicates(self):
        check_duplicate_key_excludes_all_rows_including_other_server()

    def test_server(self):
        for index in [0, 1, 3]:
            with self.subTest(index=index):
                check_history_or_future_server_movement_excluded(index)

    def test_workload(self):
        for value in [None, 'different-workload']:
            with self.subTest(value=value):
                check_missing_or_changing_workload_excludes_pod(value)

    def test_boundary(self):
        check_target_never_crosses_train_end_and_validation_can_use_past_date()

    def test_split(self):
        check_workload_buckets_and_pods_are_disjoint_across_dates()

    def test_strict(self):
        check_strict_cohort_requires_single_gpu_server_in_all_four_windows()


if __name__ == '__main__':
    unittest.main()
