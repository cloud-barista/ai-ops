"""Exact offline batching of different known-model GPU benchmark grids.

This is a research-only computation path. It preserves the existing assessor's
support checks and SLO-specific results. It does not recommend VM resources or
authorize deployment and scaling actions.
"""
from __future__ import annotations

import copy
import json
from collections import OrderedDict

import assess_gpu_shape as shape
from train_performance import FEATURES, TARGETS


def _grid_key(candidates: list[dict]) -> str:
    return json.dumps(candidates, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _predict_grids(bundle: dict, grids: OrderedDict[str, list[dict]],
                   max_grids: int) -> dict[str, list[dict]]:
    """Predict a bounded number of distinct ordered grids per model call."""
    items = list(grids.items())
    result: dict[str, list[dict]] = {}
    for start in range(0, len(items), max_grids):
        chunk = items[start:start + max_grids]
        candidates = [row for _, grid in chunk for row in grid]
        try:
            raw = shape._predict_once(bundle, candidates)
        except Exception:
            # Combining grids can exceed a predictor's batch limit even when
            # each original eight-candidate call succeeds. Retry at the
            # original boundary; a per-grid failure still propagates.
            raw = None
        if not isinstance(raw, list) or len(raw) != 8 * len(chunk):
            # A malformed predictor result cannot be split safely. Preserve
            # the assessor's original per-grid count check in this case.
            for key, grid in chunk:
                result[key] = shape._predict_once(bundle, grid)
        else:
            for index, (key, _) in enumerate(chunk):
                result[key] = raw[8 * index:8 * (index + 1)]
    return result


def assess_gpu_shape_microbatch(bundle: dict, requests: list[dict],
                                max_grids: int = 8) -> list[dict]:
    """Return the assessor's exact results while coalescing distinct grids.

    Only valid requests for a verified, schema-compatible bundle are sent to
    prediction. The raw prediction cache is local to this call. Each request
    is still checked and scored by the original assessor, including invalid
    SLOs and unsupported candidate results.
    """
    if not isinstance(requests, list):
        raise TypeError('requests_must_be_list')
    if type(max_grids) is not int or max_grids < 1 or max_grids > shape.MAX_CACHED_GRIDS:
        raise ValueError('max_grids_must_be_integer_1_to_64')

    can_predict = (bundle.get(shape.VERIFIED_KEY)
                   == (shape.MODEL_SHA256, shape.SOURCE_SHA256)
                   and bundle.get('targets') == TARGETS
                   and bundle.get('features') == FEATURES)
    grids: OrderedDict[str, list[dict]] = OrderedDict()
    if can_predict:
        for request in requests:
            if shape._request_errors(request):
                continue
            candidates = request['candidates']
            grids.setdefault(_grid_key(candidates), candidates)
    raw_by_grid = _predict_grids(bundle, grids, max_grids) if grids else {}

    def predict_cached(_bundle: dict, candidates: list[dict]) -> list[dict]:
        # Unsupported reasons contain mutable lists. Do not let one request's
        # result mutate another request's result or this call's cache.
        return copy.deepcopy(raw_by_grid[_grid_key(candidates)])

    return [shape._assess_gpu_shape(bundle, request, predict_cached)
            for request in requests]
