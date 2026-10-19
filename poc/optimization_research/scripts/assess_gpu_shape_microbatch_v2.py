"""Research-only GPU grid batching with one validation pass per request.

This preserves the pinned same-host benchmark assessor's outputs. It neither
estimates VM requirements nor authorizes deployment or scaling actions.
"""
from __future__ import annotations

from collections import OrderedDict

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import _grid_key, _predict_grids
from train_performance import FEATURES, TARGETS


def assess_gpu_shape_microbatch_v2(bundle: dict, requests: list[dict],
                                   max_grids: int = 8) -> list[dict]:
    """Batch distinct grids and reuse each request's validation and grid key.

    The caller must not mutate requests or the bundle while this call runs.
    Unsupported reason lists remain independent in the returned results.
    """
    if not isinstance(requests, list):
        raise TypeError('requests_must_be_list')
    if type(max_grids) is not int or max_grids < 1 or max_grids > shape.MAX_CACHED_GRIDS:
        raise ValueError('max_grids_must_be_integer_1_to_64')

    checked = [(request, shape._request_errors(request)) for request in requests]
    can_predict = (bundle.get(shape.VERIFIED_KEY)
                   == (shape.MODEL_SHA256, shape.SOURCE_SHA256)
                   and bundle.get('targets') == TARGETS
                   and bundle.get('features') == FEATURES)
    grids: OrderedDict[str, list[dict]] = OrderedDict()
    key_by_candidates_id: dict[int, str] = {}
    if can_predict:
        for request, errors in checked:
            if errors:
                continue
            candidates = request['candidates']
            key = _grid_key(candidates)
            key_by_candidates_id[id(candidates)] = key
            grids.setdefault(key, candidates)
    raw_by_grid = _predict_grids(bundle, grids, max_grids) if grids else {}

    def predict_cached(_bundle: dict, candidates: list[dict]) -> list[dict]:
        raw = raw_by_grid[key_by_candidates_id[id(candidates)]]
        # The assessor copies predicted numbers into new result dictionaries.
        # Only unsupported reason lists escape into returned results.
        return [item if item['status'] == 'PREDICTED'
                else {**item, 'reasons': list(item['reasons'])}
                for item in raw]

    return [shape._assess_gpu_shape(bundle, request, predict_cached,
                                    _prevalidated_errors=errors)
            for request, errors in checked]
