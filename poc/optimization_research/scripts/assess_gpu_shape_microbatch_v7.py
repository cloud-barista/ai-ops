"""Research GPU shape assessment with exact2-worker row partition prediction.

Keeps v6's256-grid limit, verification, support/abstention, SLO decisions and
per-grid retry. Small predictions stay serial. Original saved fit and v6 remain
unchanged; this API returns offline research estimates, not VM commands.
"""
from assess_gpu_shape_row_partition import assess_gpu_shape_row_partition


def assess_gpu_shape_microbatch_v7(bundle: dict, requests: list[dict],
                                   max_grids: int = 256) -> list[dict]:
    return assess_gpu_shape_row_partition(bundle, requests, workers=2,
                                          max_grids=max_grids)
