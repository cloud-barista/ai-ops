"""Count support for one-hour observed GPU-SM change without fitting a model.

Only already downloaded online GenAI pod-hour rows are read.  The label is a
change in observed average SM utilization for a pod seen in adjacent hours;
it is neither an overload event nor the effect of a scaling command.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "data/external/alibaba_gpu_2026_temporal/online_genai_gpu_16h.parquet"
)
SOURCE_SHA256 = "04aa37e8c21cbb97beab42f76e5e47afe1b399652d6bee52f0af55ec0577443f"
COLUMNS = (
    "pod_id", "workload_id", "server_id", "gpu_request", "server_gpu_count",
    "avg_gpu_sm_util", "day", "hour",
)
PERIODS = {0: range(8), 1: range(4), 5: range(4)}
PARTITIONS = (("development", 0, 0, 70), ("validation", 1, 70, 85), ("test", 5, 85, 100))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def workload_bucket(workload_id: str) -> int:
    """Stable, disjoint 70/15/15 buckets without using any outcome value."""
    return int(hashlib.sha256(str(workload_id).encode("utf-8")).hexdigest()[:8], 16) % 100


def _eligible_pairs(source: pd.DataFrame, *, strict_one_gpu_server: bool) -> pd.DataFrame:
    rows = source.loc[source["gpu_request"].eq(1)].copy()
    if strict_one_gpu_server:
        rows = rows.loc[rows["server_gpu_count"].eq(1)].copy()

    next_rows = rows[["day", "hour", "pod_id", "workload_id", "server_id", "avg_gpu_sm_util"]].rename(
        columns={
            "hour": "next_hour", "workload_id": "next_workload_id",
            "server_id": "next_server_id", "avg_gpu_sm_util": "next_sm",
        }
    )
    rows["next_hour"] = rows["hour"] + 1
    pairs = rows.merge(next_rows, on=["day", "next_hour", "pod_id"], how="inner", validate="one_to_one")
    if not pairs["workload_id"].eq(pairs["next_workload_id"]).all():
        raise ValueError("workload ID changed within an adjacent pod-hour pair")
    pairs = pairs.loc[
        pairs["avg_gpu_sm_util"].between(0, 100)
        & pairs["next_sm"].between(0, 100)
    ].copy()
    return pairs


def audit_frame(frame: pd.DataFrame) -> dict:
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    if frame["pod_id"].isna().any() or frame[["day", "hour"]].isna().any().any():
        raise ValueError("pod_id, day, and hour must be present")
    if frame.duplicated(["day", "hour", "pod_id"]).any():
        raise ValueError("duplicate pod-hour key")
    unexpected = set(zip(frame["day"], frame["hour"])) - {
        (day, hour) for day, hours in PERIODS.items() for hour in hours
    }
    if unexpected:
        raise ValueError(f"unexpected selected day/hour: {sorted(unexpected)}")

    # A pod must have the same known workload in every selected hour. Dropping
    # mixed/null IDs avoids silently putting one pod into two split buckets.
    by_pod = frame.groupby("pod_id", dropna=False)["workload_id"]
    stable = by_pod.nunique(dropna=True).eq(1) & ~by_pod.apply(lambda values: values.isna().any())
    stable_pods = set(stable.index[stable])
    source = frame.loc[frame["pod_id"].isin(stable_pods), COLUMNS].copy()
    source["bucket"] = source["workload_id"].map(workload_bucket)

    out = {
        "source_rows": int(len(frame)),
        "source_pods": int(frame["pod_id"].nunique()),
        "stable_workload_pods": int(len(stable_pods)),
        "excluded_unstable_or_unknown_pods": int(len(stable) - len(stable_pods)),
        "split": "SHA256(str(workload_id)) first 8 hex digits modulo 100; day 0 buckets 0-69, day 1 buckets 70-84, day 5 buckets 85-99",
        "change_definition": "abs(next_hour_avg_gpu_sm_util - current_avg_gpu_sm_util) > 10 percentage points",
        "cohorts": {},
    }
    for cohort_name, strict in (("one_gpu_pod", False), ("one_gpu_server_and_pod", True)):
        pairs = _eligible_pairs(source, strict_one_gpu_server=strict)
        summaries = {}
        for split_name, day, low, high in PARTITIONS:
            selected = pairs.loc[
                pairs["day"].eq(day)
                & pairs["bucket"].ge(low)
                & pairs["bucket"].lt(high)
            ].copy()
            changed_server = ~selected["server_id"].eq(selected["next_server_id"])
            # A server change would make a one-hour comparison ambiguous.
            selected = selected.loc[~changed_server].copy()
            selected["changed"] = (
                selected["next_sm"] - selected["avg_gpu_sm_util"]
            ).abs().gt(10)
            change_by_workload = selected.groupby("workload_id")["changed"].sum().sort_values(ascending=False)
            changes = int(selected["changed"].sum())
            summaries[split_name] = {
                "day": day,
                "bucket_range": [low, high],
                "eligible_pairs": int(len(selected)),
                "pods": int(selected["pod_id"].nunique()),
                "workloads": int(selected["workload_id"].nunique()),
                "server_changed_pairs_excluded": int(changed_server.sum()),
                "changes_gt_10pp": changes,
                "workloads_with_change": int(change_by_workload.gt(0).sum()),
                "top_workload_change_counts": [int(v) for v in change_by_workload.head(5)],
                "top_workload_change_share": float(change_by_workload.iloc[0] / changes)
                if changes else 0.0,
            }
        out["cohorts"][cohort_name] = summaries
    return out


def main() -> None:
    actual = sha256_file(SOURCE)
    if actual != SOURCE_SHA256:
        raise ValueError(f"source SHA256 mismatch: {actual}")
    frame = pd.read_parquet(SOURCE, columns=list(COLUMNS))
    observed = set(zip(frame["day"], frame["hour"]))
    expected = {(day, hour) for day, hours in PERIODS.items() for hour in hours}
    if observed != expected:
        raise ValueError(f"selected periods differ: missing={sorted(expected - observed)}")
    result = audit_frame(frame)
    result["source_sha256"] = actual
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
