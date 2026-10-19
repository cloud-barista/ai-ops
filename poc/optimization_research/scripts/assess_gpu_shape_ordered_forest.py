"""v6 validation/reuse with deterministic, once-validated tree traversal.

Original v6 and saved model stay unchanged. No VM action is authorized.
"""
from __future__ import annotations

from collections import OrderedDict

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import _grid_key
from ordered_forest_predict import OrderedForestPredictor, predict_grids_ordered
from assess_gpu_shape_microbatch_v3 import _request_head_errors
from assess_gpu_shape_microbatch_v4 import _plain_grid
from train_performance import FEATURES, TARGETS


MAX_BATCH_GRIDS = 256


def _assess_ordered(bundle: dict, requests: list[dict], engine,
                    max_grids: int = MAX_BATCH_GRIDS) -> list[dict]:
    """Score SLO requests with at most 256 distinct grids per model call."""
    if not isinstance(requests, list):
        raise TypeError('requests_must_be_list')
    if type(max_grids) is not int or max_grids < 1 or max_grids > MAX_BATCH_GRIDS:
        raise ValueError('max_grids_must_be_integer_1_to_256')

    can_predict = (bundle.get(shape.VERIFIED_KEY)
                   == (shape.MODEL_SHA256, shape.SOURCE_SHA256)
                   and bundle.get('targets') == TARGETS
                   and bundle.get('features') == FEATURES)
    checked: list[tuple[dict, list[str]]] = []
    grids: OrderedDict[str, list[dict]] = OrderedDict()
    prepared_by_id: dict[int, tuple[list[dict], str]] = {}
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

    raw_by_grid = predict_grids_ordered(bundle, grids, max_grids, engine) if grids else {}

    def predict_cached(_bundle: dict, candidates: list[dict]) -> list[dict]:
        raw = raw_by_grid[key_by_candidates_id[id(candidates)]]
        return [item if item['status'] == 'PREDICTED'
                else {**item, 'reasons': list(item['reasons'])}
                for item in raw]

    return [shape._assess_gpu_shape(bundle, request, predict_cached,
                                    _prevalidated_errors=errors)
            for request, errors in checked]


def assess_gpu_shape_ordered_forest(bundle: dict, requests: list[dict],
                                    workers: int = 1, max_grids: int = 256) -> list[dict]:
    """Experimental exact-output path; pool lifetime is one complete call."""
    with OrderedForestPredictor(workers) as engine:
        return _assess_ordered(bundle, requests, engine, max_grids)
