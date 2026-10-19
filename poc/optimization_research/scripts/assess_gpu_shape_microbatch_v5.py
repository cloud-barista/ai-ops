"""Offline GPU-grid assessment with the measured 64-grid batch default.

This retains v4's validation, copied-grid reuse, exact output, and per-grid
retry behavior. Only its default upper bound for a predictor call changes.
The result remains a same-host research estimate, not a VM action.
"""

from __future__ import annotations

from assess_gpu_shape_microbatch_v4 import assess_gpu_shape_microbatch_v4


def assess_gpu_shape_microbatch_v5(bundle: dict, requests: list[dict],
                                   max_grids: int = 64) -> list[dict]:
    """Use up to 64 distinct, validated candidate grids per model call."""
    return assess_gpu_shape_microbatch_v4(bundle, requests, max_grids=max_grids)
