"""Command line entrypoint for the KHU model policy PoC."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="khu-policy-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Generate non-FL/SL KHU deployment scenarios.")
    generate.add_argument("--source-db", required=True)
    generate.add_argument("--output-db", required=True)
    generate.add_argument("--csv-dir", default="")
    generate.add_argument("--count", type=int, default=1000)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--run-id", default="")
    train = commands.add_parser("train", help="Train the guarded reward model.")
    train.add_argument("--dataset-db", required=True)
    train.add_argument("--artifact-dir", required=True)
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--trees", type=int, default=200)
    evaluate = commands.add_parser("evaluate", help="Evaluate a saved model against a dataset.")
    evaluate.add_argument("--dataset-db", required=True)
    evaluate.add_argument("--artifact-dir", required=True)
    server = commands.add_parser("serve", help="Serve guarded policy inference over HTTP.")
    server.add_argument("--artifact-dir", required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=18500)
    collect = commands.add_parser("collect-live", help="Run real local Hugging Face inference and measure this node.")
    collect.add_argument("--output-db", required=True)
    collect.add_argument("--cache-dir", required=True)
    collect.add_argument("--csv-dir", default="")
    collect.add_argument("--run-id", default="")
    collect.add_argument("--device", choices=("cpu", "gpu", "both"), default="both")
    collect.add_argument(
        "--profile-set",
        choices=("baseline", "diverse", "local-novel", "local-space-filling", "local-exhaustive", "cpu-scaling", "remote-cpu-scaling", "remote-diverse", "remote-novel", "remote-space-filling", "remote-exhaustive", "remote-model-diverse"),
        default="baseline",
    )
    collect.add_argument(
        "--remote-grid-shard",
        type=int,
        default=0,
        help=(
            "Non-overlapping remote-exhaustive workload-grid shard. "
            "Shard 0 is the original matrix; higher shards select unseen shapes."
        ),
    )
    collect.add_argument("--max-cases", type=int, default=12)
    collect.add_argument("--sample-interval-seconds", type=float, default=0.25)
    collect.add_argument("--seed", type=int, default=42)
    collect.add_argument(
        "--cpu-core-counts",
        default="",
        help="Comma-separated CPU affinity/core limits, e.g. 2,4,8,16. Empty preserves each profile default.",
    )
    collect.add_argument(
        "--cpu-core-samples-per-profile",
        type=int,
        default=0,
        help=(
            "For CPU plans, retain this many deterministic, balanced core limits per workload. "
            "Zero retains every requested limit."
        ),
    )
    collect.add_argument(
        "--apply-cpu-core-counts-to-gpu",
        action="store_true",
        help="Also vary the CPU allocation for GPU candidates (otherwise CPU limits expand CPU candidates only).",
    )
    collect.add_argument(
        "--skip-existing-conditions",
        action="store_true",
        help="Skip completed physical conditions already present in --output-db or --dedupe-from-db.",
    )
    collect.add_argument(
        "--dedupe-from-db",
        action="append",
        default=[],
        help="Additional same-host KHU collection DB to scan for completed physical conditions; repeat as needed.",
    )
    prepare_live = commands.add_parser(
        "prepare-live-dataset",
        help="Convert actual HF measurements into observed CPU/GPU placement outcomes.",
    )
    prepare_live.add_argument("--source-db", required=True)
    prepare_live.add_argument("--output-db", required=True)
    prepare_live.add_argument("--csv-dir", default="")
    prepare_live.add_argument("--run-id", default="")
    prepare_live.add_argument("--collection-run-id", default="")
    prepare_live.add_argument("--seed", type=int, default=42)
    prepare_lifecycle = commands.add_parser(
        "prepare-observed-lifecycle-dataset",
        help="Build DEPLOY/KEEP and paired static scale comparison evidence from real measurements.",
    )
    prepare_lifecycle.add_argument("--source-db", required=True)
    prepare_lifecycle.add_argument("--output-db", required=True)
    prepare_lifecycle.add_argument("--csv-dir", default="")
    prepare_lifecycle.add_argument("--run-id", default="")
    prepare_lifecycle.add_argument("--collection-run-id", default="")
    prepare_lifecycle.add_argument("--seed", type=int, default=42)
    train_live = commands.add_parser(
        "train-live-placement",
        help="Train a DEPLOY-only shadow placement model from observed HF measurements.",
    )
    train_live.add_argument("--dataset-db", required=True)
    train_live.add_argument("--artifact-dir", required=True)
    train_live.add_argument("--seed", type=int, default=42)
    train_live.add_argument("--trees", type=int, default=200)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        from .generator import generate_dataset

        result = generate_dataset(
            source_db=args.source_db,
            destination_db=args.output_db,
            csv_dir=args.csv_dir or None,
            count=args.count,
            seed=args.seed,
            run_id=args.run_id or None,
        )
    elif args.command == "train":
        from .learning import train_policy

        result = train_policy(dataset_db=args.dataset_db, artifact_dir=args.artifact_dir, seed=args.seed, trees=args.trees)
    elif args.command == "evaluate":
        from .learning import evaluate_rows, load_policy, load_rows

        result = evaluate_rows(load_policy(args.artifact_dir), load_rows(args.dataset_db))
    elif args.command == "collect-live":
        from .live_collector import collect_live

        cpu_core_counts = tuple(int(value.strip()) for value in args.cpu_core_counts.split(",") if value.strip())

        result = collect_live(
            destination_db=args.output_db,
            cache_dir=args.cache_dir,
            csv_dir=args.csv_dir or None,
            run_id=args.run_id or None,
            device_scope=args.device,
            profile_set=args.profile_set,
            remote_grid_shard=args.remote_grid_shard,
            max_cases=args.max_cases,
            sample_interval_seconds=args.sample_interval_seconds,
            seed=args.seed,
            cpu_core_counts=cpu_core_counts or None,
            cpu_core_samples_per_profile=args.cpu_core_samples_per_profile or None,
            apply_cpu_core_counts_to_gpu=bool(args.apply_cpu_core_counts_to_gpu),
            skip_existing_conditions=bool(args.skip_existing_conditions),
            dedupe_from_dbs=tuple(args.dedupe_from_db),
        )
    elif args.command == "prepare-live-dataset":
        from .live_dataset import build_live_placement_dataset

        result = build_live_placement_dataset(
            source_db=args.source_db,
            destination_db=args.output_db,
            csv_dir=args.csv_dir or None,
            run_id=args.run_id or None,
            collection_run_id=args.collection_run_id or None,
            seed=args.seed,
        )
    elif args.command == "prepare-observed-lifecycle-dataset":
        from .live_dataset import build_observed_lifecycle_pair_dataset

        result = build_observed_lifecycle_pair_dataset(
            source_db=args.source_db,
            destination_db=args.output_db,
            csv_dir=args.csv_dir or None,
            run_id=args.run_id or None,
            collection_run_id=args.collection_run_id or None,
            seed=args.seed,
        )
    elif args.command == "train-live-placement":
        from .learning import train_policy
        from .live_dataset import annotate_live_placement_manifest

        result = train_policy(dataset_db=args.dataset_db, artifact_dir=args.artifact_dir, seed=args.seed, trees=args.trees)
        result["training_scope"] = annotate_live_placement_manifest(args.artifact_dir)["training_scope"]
    else:
        from .service import serve

        serve(host=args.host, port=args.port, artifact_dir=args.artifact_dir)
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
