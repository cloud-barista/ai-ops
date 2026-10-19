#!/usr/bin/env python3
"""Audit two pinned, bounded row groups from the Chutes production trace.

The full trace has 45 multi-gigabyte shards. `--fetch` reads only the selected
row groups and columns through HTTP range requests; the default mode reads the
already materialized sample. This script audits support and trains no model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import fsspec
import pandas as pd
import pyarrow.parquet as pq
import requests


REVISION = "85722e694bf7bb8a5a5ccdf70424fa5998372467"
BASE_URL = f"https://huggingface.co/datasets/harvardMadsys/chutes_metadata/resolve/{REVISION}"
MODEL_META = {
    "remote": "data/chute_models.parquet",
    "name": "chute_models.parquet",
    "size": 199541,
    "sha256": "13a15bcfaf3603fe75cfe559e823a5dabd1255b1122eb241cbf4a888a92b2637",
}
SAMPLES = [
    {
        "shard": 22,
        "row_group": 68,
        "name": "sample_shard22_rg068.parquet",
        "source_size": 2069684575,
        "source_sha256": "59ced447000490de50c10b0c935a190b2edebead37e5b9c025dcf763c81c6531",
        "sample_sha256": "32082b8288057e5c72ddc5cdf4296a08b8aaa31e4ac45dd2c4aa52790dc7bed0",
    },
    {
        "shard": 44,
        "row_group": 65,
        "name": "sample_shard44_rg065.parquet",
        "source_size": 2046207562,
        "source_sha256": "ca99b91b33d04bf56f3d0b26241c4a083a7e1579f0f571887eee4ee1004c90dd",
        "sample_sha256": "92fe8c48ab1aadfe33d214570a167cebb4bf01e0fdb7eda296304cc91c0eac71",
    },
]
COLUMNS = [
    "invocation_id", "function_name", "chute_id", "instance_id",
    "started_at", "completed_at", "it", "ot", "ct", "ttft",
]
STREAM_FUNCTIONS = {"chat_stream", "completion_stream"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_remote(remote: str, size: int, digest: str) -> str:
    url = f"{BASE_URL}/{remote}"
    response = requests.head(url, allow_redirects=False, timeout=30)
    response.raise_for_status()
    actual_size = int(response.headers.get("X-Linked-Size", "-1"))
    actual_digest = response.headers.get("X-Linked-ETag", "").strip('"')
    actual_revision = response.headers.get("X-Repo-Commit")
    if (actual_size, actual_digest, actual_revision) != (size, digest, REVISION):
        raise ValueError(f"pinned remote source changed: {remote}")
    return url


def fetch(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    meta_url = verify_remote(MODEL_META["remote"], MODEL_META["size"], MODEL_META["sha256"])
    response = requests.get(meta_url, timeout=60)
    response.raise_for_status()
    content = response.content
    path = root / MODEL_META["name"]
    path.write_bytes(content)
    if sha256(path) != MODEL_META["sha256"]:
        raise ValueError("model metadata checksum mismatch")
    for sample in SAMPLES:
        shard = sample["shard"]
        remote = f"data/trace/train-{shard:05d}-of-00045.parquet"
        url = verify_remote(remote, sample["source_size"], sample["source_sha256"])
        with fsspec.open(url, "rb", block_size=1024 * 1024, cache_type="readahead") as stream:
            parquet = pq.ParquetFile(stream)
            if parquet.metadata.row_group(sample["row_group"]).num_rows != 1001472:
                raise ValueError("row group structure changed")
            table = parquet.read_row_group(sample["row_group"], columns=COLUMNS)
        path = root / sample["name"]
        pq.write_table(table, path, compression="zstd")
        if sha256(path) != sample["sample_sha256"]:
            raise ValueError(f"sample checksum mismatch: {path}")


def audit_sample(path: Path, model_ids: set[str]) -> dict:
    rows = pd.read_parquet(path)
    if list(rows.columns) != COLUMNS:
        raise ValueError(f"unexpected columns: {path}")
    if rows.invocation_id.duplicated().any():
        raise ValueError(f"duplicate invocation ID: {path}")
    if not rows.started_at.is_monotonic_increasing:
        raise ValueError(f"sample is not ordered by arrival: {path}")

    stream = rows.function_name.isin(STREAM_FUNCTIONS)
    duration = (rows.completed_at - rows.started_at).dt.total_seconds()
    ttft = rows.ttft
    valid_ttft = stream & ttft.notna() & (ttft > 0) & (
        rows.completed_at.notna() & (duration > 0) & (ttft <= duration + 0.001)
    )
    token_valid = rows.it.notna() & rows.ot.notna() & (rows.it >= 0) & (rows.ot >= 0)
    cache_valid = token_valid & rows.ct.notna() & (rows.ct >= 0) & (rows.ct <= rows.it)
    multi_model_instances = int((rows.groupby("instance_id").chute_id.nunique() > 1).sum())

    # Only use complete five-minute arrival windows inside the sampled row group.
    first_full = rows.started_at.min().ceil("5min")
    last_full = rows.started_at.max().floor("5min")
    labelled = rows.loc[valid_ttft & rows.started_at.ge(first_full) & rows.started_at.lt(last_full),
                        ["chute_id", "instance_id", "started_at", "ttft"]].copy()
    labelled["window_start"] = labelled.started_at.dt.floor("5min")
    windows = labelled.groupby(["chute_id", "window_start"], sort=False).agg(
        requests=("ttft", "size"),
        observed_instances=("instance_id", "nunique"),
        p90_ttft_s=("ttft", lambda values: values.quantile(0.9)),
    )
    busy = windows.loc[windows.requests >= 100]
    busy_multi = busy.loc[busy.observed_instances >= 2]
    after_history_warmup = busy.loc[
        busy.index.get_level_values("window_start") >= first_full + pd.Timedelta(minutes=30)
    ]
    after_history_warmup_multi = after_history_warmup.loc[
        after_history_warmup.observed_instances >= 2
    ]
    return {
        "file": path.name,
        "sha256": sha256(path),
        "rows": int(len(rows)),
        "first_start": rows.started_at.min().isoformat(),
        "last_start": rows.started_at.max().isoformat(),
        "full_5min_windows": int((last_full - first_full).total_seconds() / 300),
        "models": int(rows.chute_id.nunique()),
        "mapped_model_rows": int(rows.chute_id.isin(model_ids).sum()),
        "distinct_request_instances": int(rows.instance_id.nunique()),
        "instances_seen_with_multiple_models": multi_model_instances,
        "endpoint_counts": {str(k): int(v) for k, v in rows.function_name.value_counts().items()},
        "stream_rows": int(stream.sum()),
        "ttft_nonnull_rows": int(ttft.notna().sum()),
        "stream_ttft_nonnull_rows": int((stream & ttft.notna()).sum()),
        "valid_stream_ttft_rows": int(valid_ttft.sum()),
        "invalid_or_missing_stream_ttft_rows": int((stream & ~valid_ttft).sum()),
        "stream_ttft_exceeds_completed_duration_by_1ms": int((
            stream & ttft.notna() & ((ttft - duration) > 0.001)
        ).sum()),
        "completed_nonnull_rows": int(rows.completed_at.notna().sum()),
        "valid_token_rows": int(token_valid.sum()),
        "cached_token_nonnull_rows": int(rows.ct.notna().sum()),
        "valid_cache_token_rows": int(cache_valid.sum()),
        "positive_cache_token_rows": int((rows.ct > 0).sum()),
        "full_window_model_groups_with_100plus_valid_stream_ttft": int(len(busy)),
        "full_window_models_with_100plus_valid_stream_ttft": int(busy.index.get_level_values(0).nunique()),
        "full_window_model_groups_with_100plus_and_2plus_instances": int(len(busy_multi)),
        "post_30min_history_label_groups_with_100plus": int(len(after_history_warmup)),
        "post_30min_history_label_groups_with_100plus_and_2plus_instances": int(
            len(after_history_warmup_multi)
        ),
        "busy_group_p90_ttft_s": {
            "min": float(busy.p90_ttft_s.min()) if len(busy) else None,
            "median": float(busy.p90_ttft_s.median()) if len(busy) else None,
            "max": float(busy.p90_ttft_s.max()) if len(busy) else None,
        },
        "top_model_requests": [
            {"chute_id": str(model), "rows": int(count)}
            for model, count in rows.chute_id.value_counts().head(5).items()
        ],
    }


def audit(root: Path) -> dict:
    meta_path = root / MODEL_META["name"]
    if sha256(meta_path) != MODEL_META["sha256"]:
        raise ValueError("model metadata checksum mismatch")
    metadata = pd.read_parquet(meta_path)
    if list(metadata.columns) != ["chute_id", "name", "invocation_count", "current_estimated_price", "supported_gpus"]:
        raise ValueError("model metadata schema changed")
    model_ids = set(metadata.chute_id)
    result = {
        "dataset": "harvardMadsys/chutes_metadata",
        "revision": REVISION,
        "model_metadata": {
            "rows": int(len(metadata)),
            "sha256": sha256(meta_path),
            "supported_gpus_nonnull_rows": int(metadata.supported_gpus.notna().sum()),
        },
        "samples": [],
    }
    for sample in SAMPLES:
        path = root / sample["name"]
        if sha256(path) != sample["sample_sha256"]:
            raise ValueError(f"sample checksum mismatch: {path}")
        audited = audit_sample(path, model_ids)
        audited["source_shard"] = sample["shard"]
        audited["source_row_group"] = sample["row_group"]
        audited["source_file_sha256"] = sample["source_sha256"]
        audited["source_file_size_bytes"] = sample["source_size"]
        result["samples"].append(audited)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/external/chutes_metadata"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/chutes_metadata_sample_v1/audit.json"))
    parser.add_argument("--fetch", action="store_true", help="fetch exactly two pinned row groups")
    args = parser.parse_args()
    if args.fetch:
        fetch(args.root)
    result = audit(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
