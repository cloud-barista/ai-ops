"""Exact offline GPU-grid batching with copied candidate-grid reuse.

This extends v3's call-scoped identity reuse to separately deserialized plain
Python grids with equal values. Request and model bundle must remain read-only
for the duration of this call, as for v2/v3. No platform action is authorized.
"""
from __future__ import annotations

from collections import OrderedDict

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import _grid_key, _predict_grids
from assess_gpu_shape_microbatch_v3 import _request_head_errors
from train_performance import FEATURES, TARGETS


def _plain_grid(candidates: object) -> bool:
    """Limit value equality reuse to JSON-like grids without type aliases.

    ``True == 1`` and ``2.0 == 2`` would otherwise let a grid that fails the
    assessor's exact integer type checks match a previously valid grid. Custom
    equality on containers, keys or values must also take the original path.
    """
    if type(candidates) is not list or len(candidates) != 8:
        return False
    for row in candidates:
        if type(row) is not dict:
            return False
        if any(type(key) is not str or type(value) not in (str, int)
               for key, value in row.items()):
            return False
    return True


def assess_gpu_shape_microbatch_v4(bundle: dict, requests: list[dict],
                                   max_grids: int = 8) -> list[dict]:
    """Score SLO requests with call-local identity and plain-value grid reuse."""
    if not isinstance(requests, list):
        raise TypeError('requests_must_be_list')
    if type(max_grids) is not int or max_grids < 1 or max_grids > shape.MAX_CACHED_GRIDS:
        raise ValueError('max_grids_must_be_integer_1_to_64')

    can_predict = (bundle.get(shape.VERIFIED_KEY)
                   == (shape.MODEL_SHA256, shape.SOURCE_SHA256)
                   and bundle.get('targets') == TARGETS
                   and bundle.get('features') == FEATURES)
    checked: list[tuple[dict, list[str]]] = []
    grids: OrderedDict[str, list[dict]] = OrderedDict()
    prepared_by_id: dict[int, tuple[list[dict], str]] = {}
    # Narrow equality checks to grids with the same first workload ID. The
    # complete ordered list comparison, not this index, decides equivalence.
    prepared_by_workload: dict[str, list[tuple[list[dict], str]]] = {}
    key_by_candidates_id: dict[int, str] = {}
    for request in requests:
        if not can_predict:
            errors = shape._request_errors(request)
        else:
            head_errors, stop = _request_head_errors(request)
            if stop:
                errors = head_errors
            else:
                candidates = request['candidates']
                cached = prepared_by_id.get(id(candidates))
                if cached is not None and cached[0] is candidates:
                    errors = head_errors
                    key = cached[1]
                else:
                    plain = _plain_grid(candidates)
                    workload = candidates[0].get('workload_id') if plain else None
                    matched = None
                    if type(workload) is str:
                        for reference, reference_key in prepared_by_workload.get(workload, ()):
                            if candidates == reference:
                                matched = reference_key
                                break
                    if matched is not None:
                        errors = head_errors
                        key = matched
                        prepared_by_id[id(candidates)] = (candidates, key)
                    else:
                        errors = shape._request_errors(request)
                        if not errors:
                            key = _grid_key(candidates)
                            prepared_by_id[id(candidates)] = (candidates, key)
                            if type(workload) is str:
                                prepared_by_workload.setdefault(workload, []).append(
                                    (candidates, key))
                if not errors:
                    key_by_candidates_id[id(candidates)] = key
                    grids.setdefault(key, candidates)
        checked.append((request, errors))

    raw_by_grid = _predict_grids(bundle, grids, max_grids) if grids else {}

    def predict_cached(_bundle: dict, candidates: list[dict]) -> list[dict]:
        raw = raw_by_grid[key_by_candidates_id[id(candidates)]]
        # Returned assessments own their mutable unsupported-reason lists.
        return [item if item['status'] == 'PREDICTED'
                else {**item, 'reasons': list(item['reasons'])}
                for item in raw]

    return [shape._assess_gpu_shape(bundle, request, predict_cached,
                                    _prevalidated_errors=errors)
            for request, errors in checked]
