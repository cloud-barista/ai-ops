"""Exact offline GPU-grid batching with call-scoped candidate validation reuse.

One request still validates its own fields and SLOs. A candidate list already
validated during this call can skip its unchanged eight-row grid checks. The
caller must not mutate requests or the model bundle while the call runs.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import _grid_key, _predict_grids
from train_performance import FEATURES, TARGETS


def _request_head_errors(request: Any) -> tuple[list[str], bool]:
    """Return request/SLO errors and whether the original validator stops here.

    This deliberately matches the first part of ``shape._request_errors``.
    Invalid SLOs do not stop that validator: a cold candidate grid must still
    be checked so its additional errors appear in the original order.
    """
    if not isinstance(request, dict):
        return ['request_must_be_object'], True
    errors = []
    missing = shape.REQUEST_FIELDS - request.keys()
    extra = request.keys() - shape.REQUEST_FIELDS
    if missing:
        errors.append('missing_request_fields:' + ','.join(sorted(missing)))
    if extra:
        errors.append('unsupported_request_fields:'
                      + ','.join(sorted(map(str, extra))))
    if errors:
        return errors, True
    if not shape._positive_finite_number(request.get('batch_latency_p95_limit_ms')):
        errors.append('invalid_batch_latency_p95_limit_ms')
    if not shape._positive_finite_number(request.get('min_requests_per_second')):
        errors.append('invalid_min_requests_per_second')
    return errors, False


def assess_gpu_shape_microbatch_v3(bundle: dict, requests: list[dict],
                                   max_grids: int = 8) -> list[dict]:
    """Return v2's exact results while validating a shared grid only once.

    A cache entry holds the candidate list itself alongside the key, both to
    make object identity explicit and to prevent an ``id`` from being reused
    during this call. Invalid grids are never cached. Separate but equal lists
    still share raw model predictions through the canonical full-grid key.
    """
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
                    errors = shape._request_errors(request)
                    if not errors:
                        key = _grid_key(candidates)
                        prepared_by_id[id(candidates)] = (candidates, key)
                if not errors:
                    key_by_candidates_id[id(candidates)] = key
                    grids.setdefault(key, candidates)
        checked.append((request, errors))

    raw_by_grid = _predict_grids(bundle, grids, max_grids) if grids else {}

    def predict_cached(_bundle: dict, candidates: list[dict]) -> list[dict]:
        raw = raw_by_grid[key_by_candidates_id[id(candidates)]]
        # The assessor copies predicted numbers into fresh dictionaries. Only
        # unsupported reason lists can escape, so give each result its own.
        return [item if item['status'] == 'PREDICTED'
                else {**item, 'reasons': list(item['reasons'])}
                for item in raw]

    return [shape._assess_gpu_shape(bundle, request, predict_cached,
                                    _prevalidated_errors=errors)
            for request, errors in checked]
