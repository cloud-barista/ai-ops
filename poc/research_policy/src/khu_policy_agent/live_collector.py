"""Direct, real Hugging Face inference collection for the KHU policy PoC.

Unlike the synthetic scenario generator this module loads models from the local
Hugging Face cache, executes real Torch inference on this host, and samples the
host's CPU, memory, and NVIDIA GPU state while each run is active. It does not
need the Go ExecutionController, so evidence remains collectable when the full
AIOps platform is unavailable.
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import gc
from itertools import product
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import resource
import socket
import sqlite3
import statistics
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence
from uuid import uuid4


LIVE_SCHEMA_VERSION = "khu-hf-live/v2"
COLLECTOR_VERSION = "khu-hf-direct-collector/v2"


@dataclass(frozen=True)
class InferenceProfile:
    name: str
    model_id: str
    revision: str
    batch_size: int
    max_length: int
    repeats: int
    dataset: str = "operations"
    family: str = "sequence_classification"
    devices: tuple[str, ...] = ("cpu", "cuda")
    workload_id: str = ""
    request_concurrency: int = 1
    cpu_threads: int = 2
    gpu_replica_counts: tuple[int, ...] = (1,)
    load_condition: str = "nominal"
    target_throughput_rps: float = 0.0
    latency_slo_ms: float = 0.0


DEFAULT_PROFILES = (
    InferenceProfile(
        name="bert-tiny-latency",
        model_id="google/bert_uncased_L-2_H-128_A-2",
        revision="30b0a37ccaaa32f332884b96992754e246e48c5f",
        batch_size=1,
        max_length=64,
        repeats=12,
    ),
    InferenceProfile(
        name="bert-tiny-throughput",
        model_id="google/bert_uncased_L-2_H-128_A-2",
        revision="30b0a37ccaaa32f332884b96992754e246e48c5f",
        batch_size=8,
        max_length=128,
        repeats=12,
    ),
    InferenceProfile(
        name="bert-mini-throughput",
        model_id="google/bert_uncased_L-4_H-256_A-4",
        revision="387825ce42dbb39b87911cdf8e383ee3b25184f8",
        batch_size=8,
        max_length=128,
        repeats=10,
    ),
    InferenceProfile(
        name="bert-mini-latency",
        model_id="google/bert_uncased_L-4_H-256_A-4",
        revision="387825ce42dbb39b87911cdf8e383ee3b25184f8",
        batch_size=1,
        max_length=64,
        repeats=8,
    ),
    InferenceProfile(
        name="electra-small-throughput",
        model_id="google/electra-small-discriminator",
        revision="fa8239aadc095e9164941d05878b98afe9b953c3",
        batch_size=8,
        max_length=128,
        repeats=10,
    ),
    InferenceProfile(
        name="albert-base-throughput",
        model_id="albert/albert-base-v2",
        revision="8e2f239c5f8a2c0f253781ca60135db913e5c80c",
        batch_size=8,
        max_length=128,
        repeats=8,
    ),
    InferenceProfile(
        name="bert-small-throughput",
        model_id="google/bert_uncased_L-4_H-512_A-8",
        revision="606e4d55252882ac25ba1f1d1a182075830f5a90",
        batch_size=8,
        max_length=128,
        repeats=8,
    ),
    InferenceProfile(
        name="distilbert-base-throughput",
        model_id="distilbert/distilbert-base-uncased",
        revision="12040accade4e8a0f71eabdb258fecc2e7e948be",
        batch_size=8,
        max_length=128,
        repeats=8,
    ),
    InferenceProfile(
        name="distilbert-base-latency",
        model_id="distilbert/distilbert-base-uncased",
        revision="12040accade4e8a0f71eabdb258fecc2e7e948be",
        batch_size=1,
        max_length=64,
        repeats=6,
    ),
    InferenceProfile(
        name="bert-base-throughput",
        model_id="google-bert/bert-base-uncased",
        revision="86b5e0934494bd15c9632b12f734a8a67f723594",
        batch_size=8,
        max_length=128,
        repeats=6,
    ),
    InferenceProfile(
        name="roberta-base-throughput",
        model_id="FacebookAI/roberta-base",
        revision="e2da8e2f811d1448a5b465c236feacd80ffbac7b",
        batch_size=8,
        max_length=128,
        repeats=6,
    ),
    InferenceProfile(
        name="roberta-base-latency",
        model_id="FacebookAI/roberta-base",
        revision="e2da8e2f811d1448a5b465c236feacd80ffbac7b",
        batch_size=1,
        max_length=64,
        repeats=6,
    ),
    InferenceProfile(
        name="qwen2.5-0.5b-forward",
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775",
        batch_size=1,
        max_length=128,
        repeats=6,
        family="causal_lm",
        devices=("cuda",),
    ),
)


# These are bounded workload shapes, selected to stay within the host's two
# 48-GiB GPUs and 56 CPU cores. ``request_concurrency`` is closed-loop client
# pressure on each model replica; it is not an unrelated synthetic CPU/GPU
# stress process. GPU replica count two means the collector starts one genuine
# model process on each physical GPU and aggregates the service result.
DIVERSE_PROFILES = (
    InferenceProfile(
        name="bert-tiny-short-interactive",
        workload_id="bert-tiny-short-interactive",
        model_id="google/bert_uncased_L-2_H-128_A-2",
        revision="30b0a37ccaaa32f332884b96992754e246e48c5f",
        batch_size=1,
        max_length=64,
        repeats=12,
        request_concurrency=1,
        load_condition="nominal_interactive",
        target_throughput_rps=30.0,
        latency_slo_ms=125.0,
    ),
    InferenceProfile(
        name="bert-tiny-long-burst",
        workload_id="bert-tiny-long-burst",
        model_id="google/bert_uncased_L-2_H-128_A-2",
        revision="30b0a37ccaaa32f332884b96992754e246e48c5f",
        batch_size=4,
        max_length=256,
        repeats=8,
        request_concurrency=4,
        gpu_replica_counts=(1, 2),
        load_condition="closed_loop_burst",
        target_throughput_rps=160.0,
        latency_slo_ms=450.0,
    ),
    InferenceProfile(
        name="bert-mini-mixed",
        workload_id="bert-mini-mixed",
        model_id="google/bert_uncased_L-4_H-256_A-4",
        revision="387825ce42dbb39b87911cdf8e383ee3b25184f8",
        batch_size=4,
        max_length=128,
        repeats=8,
        request_concurrency=2,
        load_condition="moderate_closed_loop",
        target_throughput_rps=90.0,
        latency_slo_ms=300.0,
    ),
    InferenceProfile(
        name="albert-base-mixed",
        workload_id="albert-base-mixed",
        model_id="albert/albert-base-v2",
        revision="8e2f239c5f8a2c0f253781ca60135db913e5c80c",
        batch_size=4,
        max_length=256,
        repeats=8,
        request_concurrency=2,
        load_condition="moderate_closed_loop",
        target_throughput_rps=90.0,
        latency_slo_ms=400.0,
    ),
    InferenceProfile(
        name="electra-small-long-burst",
        workload_id="electra-small-long-burst",
        model_id="google/electra-small-discriminator",
        revision="fa8239aadc095e9164941d05878b98afe9b953c3",
        batch_size=8,
        max_length=256,
        repeats=8,
        request_concurrency=4,
        gpu_replica_counts=(1, 2),
        load_condition="closed_loop_burst",
        target_throughput_rps=170.0,
        latency_slo_ms=500.0,
    ),
    InferenceProfile(
        name="distilbert-base-long-sustained",
        workload_id="distilbert-base-long-sustained",
        model_id="distilbert/distilbert-base-uncased",
        revision="12040accade4e8a0f71eabdb258fecc2e7e948be",
        batch_size=8,
        max_length=384,
        repeats=8,
        request_concurrency=4,
        gpu_replica_counts=(1, 2),
        load_condition="closed_loop_sustained",
        target_throughput_rps=170.0,
        latency_slo_ms=650.0,
    ),
    InferenceProfile(
        name="bert-base-long-sustained",
        workload_id="bert-base-long-sustained",
        model_id="google-bert/bert-base-uncased",
        revision="86b5e0934494bd15c9632b12f734a8a67f723594",
        batch_size=4,
        max_length=384,
        repeats=6,
        request_concurrency=2,
        gpu_replica_counts=(1, 2),
        load_condition="closed_loop_sustained",
        target_throughput_rps=85.0,
        latency_slo_ms=750.0,
    ),
    InferenceProfile(
        name="roberta-base-long-sustained",
        workload_id="roberta-base-long-sustained",
        model_id="FacebookAI/roberta-base",
        revision="e2da8e2f811d1448a5b465c236feacd80ffbac7b",
        batch_size=4,
        max_length=384,
        repeats=6,
        request_concurrency=2,
        gpu_replica_counts=(1, 2),
        load_condition="closed_loop_sustained",
        target_throughput_rps=75.0,
        latency_slo_ms=800.0,
    ),
    InferenceProfile(
        name="qwen2.5-0.5b-short-interactive",
        workload_id="qwen2.5-0.5b-short-interactive",
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775",
        batch_size=1,
        max_length=128,
        repeats=8,
        family="causal_lm",
        devices=("cuda",),
        request_concurrency=1,
        load_condition="nominal_interactive",
        target_throughput_rps=18.0,
        latency_slo_ms=220.0,
    ),
    InferenceProfile(
        name="qwen2.5-0.5b-long-burst",
        workload_id="qwen2.5-0.5b-long-burst",
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775",
        batch_size=1,
        max_length=512,
        repeats=6,
        family="causal_lm",
        devices=("cuda",),
        request_concurrency=2,
        gpu_replica_counts=(1, 2),
        load_condition="closed_loop_burst",
        target_throughput_rps=25.0,
        latency_slo_ms=900.0,
    ),
)


# A shared, bounded matrix for heterogeneous-node experiments. Qwen 0.5B is
# cached on both KHU experiment hosts, so the same model/revision, request
# pattern and SLO can be observed under a real 2/4/8/16-core allocation and
# 1/2/4 physical-GPU replica count. Hosts with fewer GPUs transparently skip
# the unavailable replica shapes; no synthetic substitute is inserted.
CPU_SCALING_PROFILES = (
    InferenceProfile(
        name="qwen2.5-0.5b-cpu-scaling-short",
        workload_id="qwen2.5-0.5b-cpu-scaling-short",
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775",
        batch_size=1,
        max_length=128,
        repeats=8,
        family="causal_lm",
        devices=("cpu", "cuda"),
        request_concurrency=1,
        gpu_replica_counts=(1, 2, 4),
        load_condition="nominal_interactive",
        target_throughput_rps=18.0,
        latency_slo_ms=220.0,
    ),
    InferenceProfile(
        name="qwen2.5-0.5b-cpu-scaling-long",
        workload_id="qwen2.5-0.5b-cpu-scaling-long",
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775",
        batch_size=1,
        max_length=512,
        repeats=6,
        family="causal_lm",
        devices=("cpu", "cuda"),
        request_concurrency=2,
        gpu_replica_counts=(1, 2, 4),
        load_condition="closed_loop_burst",
        target_throughput_rps=25.0,
        latency_slo_ms=900.0,
    ),
)


# These models are pinned to complete snapshots already present on icns-216.
# They deliberately remain a separate profile set because Qwen 0.5B is not a
# complete snapshot on that host. The measured host/model metadata remains in
# the raw DB, so these observations are never misrepresented as Qwen results.
REMOTE_CPU_SCALING_PROFILES = (
    InferenceProfile(
        name="pythia-160m-cpu-scaling",
        workload_id="pythia-160m-cpu-scaling",
        model_id="EleutherAI/pythia-160m-deduped",
        revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        batch_size=1,
        max_length=128,
        repeats=6,
        family="causal_lm",
        devices=("cpu", "cuda"),
        request_concurrency=1,
        gpu_replica_counts=(1, 2, 4),
        load_condition="nominal_interactive",
        target_throughput_rps=22.0,
        latency_slo_ms=350.0,
    ),
    InferenceProfile(
        name="tinyllama-1.1b-cpu-scaling",
        workload_id="tinyllama-1.1b-cpu-scaling",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        batch_size=1,
        max_length=128,
        repeats=4,
        family="causal_lm",
        devices=("cpu", "cuda"),
        request_concurrency=1,
        gpu_replica_counts=(1, 2, 4),
        load_condition="moderate_closed_loop",
        target_throughput_rps=12.0,
        latency_slo_ms=650.0,
    ),
)


# This matrix uses only the two complete model snapshots confirmed on icns-216,
# but varies sequence shape and client pressure rather than repeating one
# static 128-token benchmark.  It is intentionally distinct from
# REMOTE_CPU_SCALING_PROFILES so existing runs retain their original lineage.
def _remote_diverse_profile(
    *,
    name: str,
    workload_id: str,
    model_id: str,
    revision: str,
    max_length: int,
    repeats: int,
    request_concurrency: int,
    load_condition: str,
    target_throughput_rps: float,
    latency_slo_ms: float,
) -> InferenceProfile:
    return InferenceProfile(
        name=name,
        workload_id=workload_id,
        model_id=model_id,
        revision=revision,
        batch_size=1,
        max_length=max_length,
        repeats=repeats,
        family="causal_lm",
        devices=("cpu", "cuda"),
        request_concurrency=request_concurrency,
        gpu_replica_counts=(1, 2, 4),
        load_condition=load_condition,
        target_throughput_rps=target_throughput_rps,
        latency_slo_ms=latency_slo_ms,
    )


REMOTE_DIVERSE_PROFILES = (
    _remote_diverse_profile(
        name="pythia-160m-short-interactive", workload_id="pythia-160m-short-interactive",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        max_length=64, repeats=6, request_concurrency=1, load_condition="nominal_interactive",
        target_throughput_rps=24.0, latency_slo_ms=300.0,
    ),
    _remote_diverse_profile(
        name="pythia-160m-medium", workload_id="pythia-160m-medium",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        max_length=128, repeats=6, request_concurrency=2, load_condition="moderate_closed_loop",
        target_throughput_rps=35.0, latency_slo_ms=450.0,
    ),
    _remote_diverse_profile(
        name="pythia-160m-long-burst", workload_id="pythia-160m-long-burst",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        max_length=256, repeats=4, request_concurrency=4, load_condition="closed_loop_burst",
        target_throughput_rps=48.0, latency_slo_ms=750.0,
    ),
    _remote_diverse_profile(
        name="pythia-160m-long-sustained", workload_id="pythia-160m-long-sustained",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        max_length=512, repeats=4, request_concurrency=2, load_condition="closed_loop_sustained",
        target_throughput_rps=18.0, latency_slo_ms=1200.0,
    ),
    _remote_diverse_profile(
        name="tinyllama-1.1b-short-interactive", workload_id="tinyllama-1.1b-short-interactive",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        max_length=64, repeats=4, request_concurrency=1, load_condition="nominal_interactive",
        target_throughput_rps=14.0, latency_slo_ms=600.0,
    ),
    _remote_diverse_profile(
        name="tinyllama-1.1b-medium", workload_id="tinyllama-1.1b-medium",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        max_length=128, repeats=4, request_concurrency=2, load_condition="moderate_closed_loop",
        target_throughput_rps=22.0, latency_slo_ms=900.0,
    ),
    _remote_diverse_profile(
        name="tinyllama-1.1b-long-burst", workload_id="tinyllama-1.1b-long-burst",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        max_length=256, repeats=4, request_concurrency=4, load_condition="closed_loop_burst",
        target_throughput_rps=30.0, latency_slo_ms=1400.0,
    ),
    _remote_diverse_profile(
        name="tinyllama-1.1b-long-sustained", workload_id="tinyllama-1.1b-long-sustained",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        max_length=512, repeats=4, request_concurrency=2, load_condition="closed_loop_sustained",
        target_throughput_rps=10.0, latency_slo_ms=2200.0,
    ),
)


def _local_novel_profile(
    *,
    name: str,
    model_id: str,
    revision: str,
    batch_size: int,
    max_length: int,
    request_concurrency: int,
    repeats: int,
    load_condition: str,
    target_throughput_rps: float,
    latency_slo_ms: float,
    devices: tuple[str, ...] = ("cpu", "cuda"),
    family: str = "sequence_classification",
) -> InferenceProfile:
    """Build a workload shape outside the earlier local matrices."""
    return InferenceProfile(
        name=name,
        workload_id=name,
        model_id=model_id,
        revision=revision,
        batch_size=batch_size,
        max_length=max_length,
        repeats=repeats,
        family=family,
        devices=devices,
        request_concurrency=request_concurrency,
        gpu_replica_counts=(1, 2),
        load_condition=load_condition,
        target_throughput_rps=target_throughput_rps,
        latency_slo_ms=latency_slo_ms,
    )


# Every physical shape below differs from the completed baseline/diverse/CPU
# matrices: model revision, batch, length, concurrency, CPU allocation or GPU
# replicas all remain traceable in the raw row.  The medium profiles measure a
# CPU-vs-GPU decision; wide profiles are GPU-only so a 2-core CPU experiment is
# not allowed to turn an otherwise useful high-pressure campaign into a stall.
LOCAL_NOVEL_PROFILES = (
    _local_novel_profile(
        name="bert-tiny-medium-paired", model_id="google/bert_uncased_L-2_H-128_A-2",
        revision="30b0a37ccaaa32f332884b96992754e246e48c5f", batch_size=2, max_length=128,
        request_concurrency=2, repeats=10, load_condition="paired_interactive", target_throughput_rps=55.0,
        latency_slo_ms=180.0,
    ),
    _local_novel_profile(
        name="bert-tiny-wide-sustained", model_id="google/bert_uncased_L-2_H-128_A-2",
        revision="30b0a37ccaaa32f332884b96992754e246e48c5f", batch_size=16, max_length=512,
        request_concurrency=8, repeats=6, load_condition="wide_sustained", target_throughput_rps=300.0,
        latency_slo_ms=900.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="bert-mini-medium-paired", model_id="google/bert_uncased_L-4_H-256_A-4",
        revision="387825ce42dbb39b87911cdf8e383ee3b25184f8", batch_size=2, max_length=256,
        request_concurrency=1, repeats=8, load_condition="paired_interactive", target_throughput_rps=35.0,
        latency_slo_ms=260.0,
    ),
    _local_novel_profile(
        name="bert-mini-wide-sustained", model_id="google/bert_uncased_L-4_H-256_A-4",
        revision="387825ce42dbb39b87911cdf8e383ee3b25184f8", batch_size=16, max_length=512,
        request_concurrency=4, repeats=5, load_condition="wide_sustained", target_throughput_rps=180.0,
        latency_slo_ms=1200.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="electra-small-medium-paired", model_id="google/electra-small-discriminator",
        revision="fa8239aadc095e9164941d05878b98afe9b953c3", batch_size=2, max_length=128,
        request_concurrency=1, repeats=8, load_condition="paired_interactive", target_throughput_rps=45.0,
        latency_slo_ms=220.0,
    ),
    _local_novel_profile(
        name="electra-small-wide-sustained", model_id="google/electra-small-discriminator",
        revision="fa8239aadc095e9164941d05878b98afe9b953c3", batch_size=16, max_length=384,
        request_concurrency=4, repeats=6, load_condition="wide_sustained", target_throughput_rps=230.0,
        latency_slo_ms=850.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="albert-base-medium-paired", model_id="albert/albert-base-v2",
        revision="8e2f239c5f8a2c0f253781ca60135db913e5c80c", batch_size=2, max_length=128,
        request_concurrency=1, repeats=8, load_condition="paired_interactive", target_throughput_rps=35.0,
        latency_slo_ms=260.0,
    ),
    _local_novel_profile(
        name="albert-base-wide-sustained", model_id="albert/albert-base-v2",
        revision="8e2f239c5f8a2c0f253781ca60135db913e5c80c", batch_size=16, max_length=512,
        request_concurrency=4, repeats=5, load_condition="wide_sustained", target_throughput_rps=160.0,
        latency_slo_ms=1250.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="bert-small-medium-paired", model_id="google/bert_uncased_L-4_H-512_A-8",
        revision="606e4d55252882ac25ba1f1d1a182075830f5a90", batch_size=2, max_length=256,
        request_concurrency=2, repeats=7, load_condition="paired_interactive", target_throughput_rps=28.0,
        latency_slo_ms=360.0,
    ),
    _local_novel_profile(
        name="bert-small-wide-sustained", model_id="google/bert_uncased_L-4_H-512_A-8",
        revision="606e4d55252882ac25ba1f1d1a182075830f5a90", batch_size=12, max_length=512,
        request_concurrency=4, repeats=5, load_condition="wide_sustained", target_throughput_rps=105.0,
        latency_slo_ms=1600.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="distilbert-medium-paired", model_id="distilbert/distilbert-base-uncased",
        revision="12040accade4e8a0f71eabdb258fecc2e7e948be", batch_size=2, max_length=128,
        request_concurrency=1, repeats=8, load_condition="paired_interactive", target_throughput_rps=40.0,
        latency_slo_ms=230.0,
    ),
    _local_novel_profile(
        name="distilbert-wide-sustained", model_id="distilbert/distilbert-base-uncased",
        revision="12040accade4e8a0f71eabdb258fecc2e7e948be", batch_size=16, max_length=512,
        request_concurrency=4, repeats=5, load_condition="wide_sustained", target_throughput_rps=170.0,
        latency_slo_ms=1350.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="bert-base-medium-paired", model_id="google-bert/bert-base-uncased",
        revision="86b5e0934494bd15c9632b12f734a8a67f723594", batch_size=2, max_length=256,
        request_concurrency=2, repeats=6, load_condition="paired_interactive", target_throughput_rps=25.0,
        latency_slo_ms=480.0,
    ),
    _local_novel_profile(
        name="bert-base-wide-sustained", model_id="google-bert/bert-base-uncased",
        revision="86b5e0934494bd15c9632b12f734a8a67f723594", batch_size=12, max_length=512,
        request_concurrency=4, repeats=4, load_condition="wide_sustained", target_throughput_rps=85.0,
        latency_slo_ms=1800.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="roberta-medium-paired", model_id="FacebookAI/roberta-base",
        revision="e2da8e2f811d1448a5b465c236feacd80ffbac7b", batch_size=2, max_length=128,
        request_concurrency=1, repeats=6, load_condition="paired_interactive", target_throughput_rps=25.0,
        latency_slo_ms=500.0,
    ),
    _local_novel_profile(
        name="roberta-wide-sustained", model_id="FacebookAI/roberta-base",
        revision="e2da8e2f811d1448a5b465c236feacd80ffbac7b", batch_size=12, max_length=512,
        request_concurrency=4, repeats=4, load_condition="wide_sustained", target_throughput_rps=70.0,
        latency_slo_ms=1900.0, devices=("cuda",),
    ),
    _local_novel_profile(
        name="qwen-medium-burst", model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775", batch_size=1, max_length=256,
        request_concurrency=4, repeats=6, load_condition="medium_burst", target_throughput_rps=30.0,
        latency_slo_ms=700.0, devices=("cuda",), family="causal_lm",
    ),
    _local_novel_profile(
        name="qwen-batched-sustained", model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775", batch_size=2, max_length=256,
        request_concurrency=2, repeats=5, load_condition="batched_sustained", target_throughput_rps=38.0,
        latency_slo_ms=900.0, devices=("cuda",), family="causal_lm",
    ),
)


_LOCAL_SPACE_FILLING_MODELS = (
    ("bert-tiny", "google/bert_uncased_L-2_H-128_A-2", "30b0a37ccaaa32f332884b96992754e246e48c5f"),
    ("bert-mini", "google/bert_uncased_L-4_H-256_A-4", "387825ce42dbb39b87911cdf8e383ee3b25184f8"),
    ("electra-small", "google/electra-small-discriminator", "fa8239aadc095e9164941d05878b98afe9b953c3"),
    ("albert-base", "albert/albert-base-v2", "8e2f239c5f8a2c0f253781ca60135db913e5c80c"),
    ("bert-small", "google/bert_uncased_L-4_H-512_A-8", "606e4d55252882ac25ba1f1d1a182075830f5a90"),
    ("distilbert", "distilbert/distilbert-base-uncased", "12040accade4e8a0f71eabdb258fecc2e7e948be"),
    ("bert-base", "google-bert/bert-base-uncased", "86b5e0934494bd15c9632b12f734a8a67f723594"),
    ("roberta", "FacebookAI/roberta-base", "e2da8e2f811d1448a5b465c236feacd80ffbac7b"),
)

_LOCAL_SPACE_FILLING_SHAPES = (
    # This first point is CPU/GPU paired; remaining high-pressure points are
    # GPU-only to keep the campaign bounded while still varying placement.
    ("paired-192", 2, 192, 3, 8, "spacefill_paired", 42.0, 330.0, ("cpu", "cuda")),
    ("burst-320", 4, 320, 3, 7, "spacefill_burst", 76.0, 650.0, ("cuda",)),
    ("sustained-448", 8, 448, 6, 6, "spacefill_sustained", 140.0, 1100.0, ("cuda",)),
    ("wide-96", 12, 96, 8, 6, "spacefill_wide", 190.0, 700.0, ("cuda",)),
    ("wide-512", 16, 512, 2, 5, "spacefill_long", 120.0, 1900.0, ("cuda",)),
)


LOCAL_SPACE_FILLING_PROFILES = tuple(
    _local_novel_profile(
        name=f"{model_name}-{shape_name}",
        model_id=model_id,
        revision=revision,
        batch_size=batch_size,
        max_length=max_length,
        request_concurrency=concurrency,
        repeats=repeats,
        load_condition=load_condition,
        target_throughput_rps=throughput,
        latency_slo_ms=latency,
        devices=devices,
    )
    for model_name, model_id, revision in _LOCAL_SPACE_FILLING_MODELS
    for shape_name, batch_size, max_length, concurrency, repeats, load_condition, throughput, latency, devices
    in _LOCAL_SPACE_FILLING_SHAPES
) + tuple(
    _local_novel_profile(
        name=f"qwen-spacefill-{shape_name}",
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775",
        batch_size=batch_size,
        max_length=max_length,
        request_concurrency=concurrency,
        repeats=repeats,
        load_condition=load_condition,
        target_throughput_rps=throughput,
        latency_slo_ms=latency,
        devices=("cuda",),
        family="causal_lm",
    )
    for shape_name, batch_size, max_length, concurrency, repeats, load_condition, throughput, latency in (
        ("interactive-96", 1, 96, 3, 6, "spacefill_interactive", 24.0, 400.0),
        ("burst-384", 1, 384, 6, 5, "spacefill_burst", 34.0, 1200.0),
        ("batched-512", 2, 512, 3, 4, "spacefill_batched", 42.0, 1700.0),
        ("wide-256", 4, 256, 1, 4, "spacefill_wide", 48.0, 1000.0),
    )
)


_EXHAUSTIVE_ENCODER_SHAPES = tuple(product((1, 2, 4, 8, 12, 16), (32, 48, 80, 112, 160, 224, 288, 352, 416, 480, 512), (1, 2, 3, 4, 6, 8)))
_EXHAUSTIVE_CAUSAL_SHAPES = tuple(product((1, 2, 4, 8), (64, 96, 160, 224, 320, 448, 512, 640, 768, 896, 1024), (1, 2, 3, 4)))


def _grid_selection(shapes: Sequence[tuple[int, int, int]], *, count: int, stride: int, offset: int = 0) -> tuple[tuple[int, int, int], ...]:
    return tuple(shapes[(offset + index * stride) % len(shapes)] for index in range(count))


_LOCAL_EXHAUSTIVE_ENCODER_DESIGNS = _grid_selection(_EXHAUSTIVE_ENCODER_SHAPES, count=64, stride=47)
_LOCAL_EXHAUSTIVE_QWEN_DESIGNS = _grid_selection(_EXHAUSTIVE_CAUSAL_SHAPES, count=64, stride=43, offset=11)

LOCAL_EXHAUSTIVE_PROFILES = tuple(
    _local_novel_profile(
        name=f"{model_name}-grid-{index:03d}", model_id=model_id, revision=revision,
        batch_size=batch, max_length=length, request_concurrency=concurrency, repeats=8,
        load_condition="exhaustive_grid", target_throughput_rps=float(batch * concurrency * 12),
        latency_slo_ms=float(250 + length * 3), devices=("cpu", "cuda") if index % 8 == 0 else ("cuda",),
    )
    for index, (batch, length, concurrency) in enumerate(_LOCAL_EXHAUSTIVE_ENCODER_DESIGNS)
    for model_name, model_id, revision in _LOCAL_SPACE_FILLING_MODELS
) + tuple(
    _local_novel_profile(
        name=f"qwen-grid-{index:03d}", model_id="Qwen/Qwen2.5-0.5B-Instruct",
        revision="7ae557604adf67be50417f59c2c2f167def9a775", batch_size=batch, max_length=length,
        request_concurrency=concurrency, repeats=6, load_condition="exhaustive_grid",
        target_throughput_rps=float(batch * concurrency * 8), latency_slo_ms=float(400 + length * 3),
        devices=("cuda",), family="causal_lm",
    )
    for index, (batch, length, concurrency) in enumerate(_LOCAL_EXHAUSTIVE_QWEN_DESIGNS)
)


# Equivalent non-overlap set for icns-216.  It is limited to Pythia and
# TinyLlama because these are the complete pinned snapshots confirmed there;
# batch 2/4 and 1024-token requests do not occur in either remote campaign
# already recorded on that host.
REMOTE_NOVEL_PROFILES = (
    InferenceProfile(
        name="pythia-160m-batched-medium", workload_id="pythia-160m-batched-medium",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        batch_size=2, max_length=128, repeats=6, family="causal_lm", devices=("cpu", "cuda"),
        request_concurrency=1, gpu_replica_counts=(1, 2, 4), load_condition="batched_interactive",
        target_throughput_rps=36.0, latency_slo_ms=450.0,
    ),
    InferenceProfile(
        name="pythia-160m-wide-burst", workload_id="pythia-160m-wide-burst",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        batch_size=4, max_length=256, repeats=5, family="causal_lm", devices=("cuda",),
        request_concurrency=4, gpu_replica_counts=(1, 2, 4), load_condition="wide_closed_loop_burst",
        target_throughput_rps=80.0, latency_slo_ms=1100.0,
    ),
    InferenceProfile(
        name="pythia-160m-very-long", workload_id="pythia-160m-very-long",
        model_id="EleutherAI/pythia-160m-deduped", revision="582159a2dfe3e712a8d47ae83dec95ae3bde8e7e",
        batch_size=1, max_length=1024, repeats=4, family="causal_lm", devices=("cuda",),
        request_concurrency=2, gpu_replica_counts=(1, 2, 4), load_condition="very_long_sustained",
        target_throughput_rps=14.0, latency_slo_ms=2500.0,
    ),
    InferenceProfile(
        name="tinyllama-batched-medium", workload_id="tinyllama-batched-medium",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        batch_size=2, max_length=128, repeats=4, family="causal_lm", devices=("cpu", "cuda"),
        request_concurrency=1, gpu_replica_counts=(1, 2, 4), load_condition="batched_interactive",
        target_throughput_rps=18.0, latency_slo_ms=900.0,
    ),
    InferenceProfile(
        name="tinyllama-wide-burst", workload_id="tinyllama-wide-burst",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        batch_size=4, max_length=256, repeats=4, family="causal_lm", devices=("cuda",),
        request_concurrency=2, gpu_replica_counts=(1, 2, 4), load_condition="wide_closed_loop_burst",
        target_throughput_rps=30.0, latency_slo_ms=1800.0,
    ),
    InferenceProfile(
        name="tinyllama-very-long", workload_id="tinyllama-very-long",
        model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0", revision="fe8a4ea1ffedaf415f4da2f062534de366a451e6",
        batch_size=1, max_length=1024, repeats=3, family="causal_lm", devices=("cuda",),
        request_concurrency=1, gpu_replica_counts=(1, 2, 4), load_condition="very_long_sustained",
        target_throughput_rps=7.0, latency_slo_ms=3500.0,
    ),
)


_REMOTE_SPACE_FILLING_MODELS = (
    ("pythia-160m", "EleutherAI/pythia-160m-deduped", "582159a2dfe3e712a8d47ae83dec95ae3bde8e7e"),
    ("tinyllama", "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "fe8a4ea1ffedaf415f4da2f062534de366a451e6"),
)

_REMOTE_SPACE_FILLING_SHAPES = (
    ("paired-192", 2, 192, 3, 5, "spacefill_paired", 28.0, 900.0, ("cpu", "cuda")),
    ("burst-320", 4, 320, 3, 4, "spacefill_burst", 48.0, 1500.0, ("cuda",)),
    ("sustained-448", 8, 448, 6, 3, "spacefill_sustained", 70.0, 2400.0, ("cuda",)),
    ("long-768", 1, 768, 3, 4, "spacefill_long", 12.0, 3500.0, ("cuda",)),
)


REMOTE_SPACE_FILLING_PROFILES = tuple(
    InferenceProfile(
        name=f"{model_name}-{shape_name}",
        workload_id=f"{model_name}-{shape_name}",
        model_id=model_id,
        revision=revision,
        batch_size=batch_size,
        max_length=max_length,
        repeats=repeats,
        family="causal_lm",
        devices=devices,
        request_concurrency=concurrency,
        gpu_replica_counts=(1, 2, 4),
        load_condition=load_condition,
        target_throughput_rps=throughput,
        latency_slo_ms=latency,
    )
    for model_name, model_id, revision in _REMOTE_SPACE_FILLING_MODELS
    for shape_name, batch_size, max_length, concurrency, repeats, load_condition, throughput, latency, devices
    in _REMOTE_SPACE_FILLING_SHAPES
)


# Additional causal-LM families with complete local snapshots on icns-216.
# The two small models receive a bounded CPU/GPU comparison.  The 1.4B and
# 1.5B models are GPU-only so the production collection campaign does not
# spend hours on an unrepresentative large-model CPU forward pass.
_REMOTE_MODEL_DIVERSE_MODELS = (
    ("gpt2", "openai-community/gpt2", "607a30d783dfa663caf39e06633721c8d4cfcd7e", True),
    ("opt-125m", "facebook/opt-125m", "27dcfa74d334bc871f3234de431e71c6eeba5dd6", True),
    ("pythia-1.4b", "EleutherAI/pythia-1.4b-deduped", "554d9c1bae3877f740aece41feb90f912cf9fedc", False),
    ("qwen2.5-1.5b", "Qwen/Qwen2.5-1.5B-Instruct", "989aa7980e4cf806f80c7fef2b1adb7bc71aa306", False),
)
_REMOTE_MODEL_DIVERSE_SHAPES = (
    ("interactive", 1, 128, 1, 3, "model_diverse_interactive"),
    ("batched", 2, 256, 2, 3, "model_diverse_batched"),
    ("long", 1, 512, 2, 2, "model_diverse_long"),
)


REMOTE_MODEL_DIVERSE_PROFILES = tuple(
    InferenceProfile(
        name=f"{model_name}-{shape_name}",
        workload_id=f"{model_name}-{shape_name}",
        model_id=model_id,
        revision=revision,
        batch_size=batch_size,
        max_length=max_length,
        repeats=repeats,
        family="causal_lm",
        devices=("cpu", "cuda") if cpu_comparison and shape_name == "interactive" else ("cuda",),
        request_concurrency=concurrency,
        gpu_replica_counts=(1, 2, 4),
        load_condition=load_condition,
        target_throughput_rps=float(batch_size * concurrency * 6),
        latency_slo_ms=float(500 + max_length * 4),
    )
    for model_name, model_id, revision, cpu_comparison in _REMOTE_MODEL_DIVERSE_MODELS
    for shape_name, batch_size, max_length, concurrency, repeats, load_condition in _REMOTE_MODEL_DIVERSE_SHAPES
)


REMOTE_EXHAUSTIVE_SHARD_SIZE = 64
REMOTE_EXHAUSTIVE_STRIDE = 43
REMOTE_EXHAUSTIVE_OFFSET = 29
# icns-216 has only the Pythia/TinyLlama snapshots.  This larger, bounded
# workload space therefore varies request shape rather than pretending that
# unavailable model variants are measured.  The token budget is deliberately
# below the largest previously successful remote workload (32,768 tokens), so
# every new point remains conservative for the 16 GiB GPUs.
REMOTE_DIVERSE_BATCH_SIZES = (1, 2, 3, 4, 6, 8, 12, 16)
REMOTE_DIVERSE_SEQUENCE_LENGTHS = tuple(range(32, 1025, 32))
REMOTE_DIVERSE_CONCURRENCIES = (1, 2, 3, 4, 5, 6, 8)
REMOTE_DIVERSE_MAX_TOKENS = 16_384
_REMOTE_DIVERSE_CAUSAL_SHAPES = tuple(
    (batch, length, concurrency)
    for batch, length, concurrency in product(
        REMOTE_DIVERSE_BATCH_SIZES,
        REMOTE_DIVERSE_SEQUENCE_LENGTHS,
        REMOTE_DIVERSE_CONCURRENCIES,
    )
    if batch * length * concurrency <= REMOTE_DIVERSE_MAX_TOKENS
)


def remote_exhaustive_profiles(shard: int = 0) -> tuple[InferenceProfile, ...]:
    """Return one deterministic, non-overlapping shard of remote grid profiles.

    The grid traversal is a permutation because the stride is coprime with the
    exhaustive causal-shape count.  Consecutive shards therefore cover new
    batch/sequence-length/concurrency shapes without repeating shard zero,
    which was the original remote-exhaustive campaign.
    """
    if shard < 0:
        raise ValueError("remote_grid_shard must be non-negative")
    max_shards = math.ceil(len(_REMOTE_DIVERSE_CAUSAL_SHAPES) / REMOTE_EXHAUSTIVE_SHARD_SIZE)
    if shard >= max_shards:
        raise ValueError(
            f"remote_grid_shard must be less than {max_shards}; "
            "higher shards would repeat an existing physical workload shape"
        )
    remaining = len(_REMOTE_DIVERSE_CAUSAL_SHAPES) - shard * REMOTE_EXHAUSTIVE_SHARD_SIZE
    count = min(REMOTE_EXHAUSTIVE_SHARD_SIZE, remaining)
    offset = REMOTE_EXHAUSTIVE_OFFSET + shard * REMOTE_EXHAUSTIVE_SHARD_SIZE * REMOTE_EXHAUSTIVE_STRIDE
    designs = _grid_selection(
        _REMOTE_DIVERSE_CAUSAL_SHAPES,
        count=count,
        stride=REMOTE_EXHAUSTIVE_STRIDE,
        offset=offset,
    )
    return tuple(
        InferenceProfile(
            name=f"{model_name}-grid-s{shard:02d}-{index:03d}",
            workload_id=f"{model_name}-grid-s{shard:02d}-{index:03d}",
            model_id=model_id, revision=revision, batch_size=batch, max_length=length, repeats=5,
            family="causal_lm", devices=("cpu", "cuda") if index % 8 == 0 else ("cuda",),
            request_concurrency=concurrency, gpu_replica_counts=(1, 2, 4), load_condition="exhaustive_grid",
            target_throughput_rps=float(batch * concurrency * 7), latency_slo_ms=float(600 + length * 4),
        )
        for index, (batch, length, concurrency) in enumerate(designs)
        for model_name, model_id, revision in _REMOTE_SPACE_FILLING_MODELS
    )


REMOTE_EXHAUSTIVE_PROFILES = remote_exhaustive_profiles()


@dataclass(frozen=True)
class ExecutionPlan:
    profile: InferenceProfile
    device: str
    replica_count: int
    cpu_core_limit: int

    @property
    def condition_id(self) -> str:
        return (
            f"{self.profile.workload_id or self.profile.name}:"
            f"{self.device}:{self.replica_count}x:cpu{self.cpu_core_limit}"
        )


ConditionKey = tuple[str, str, str, int, int, int, int, int]


def _condition_key(
    *,
    model_id: str,
    model_revision: str,
    device: str,
    batch_size: int,
    max_length: int,
    request_concurrency: int,
    cpu_core_limit: int,
    replica_count: int,
) -> ConditionKey:
    """Return the physical execution shape used to prevent duplicate runs.

    Workload/SLO labels are excluded because they are policy metadata.  This
    key is only the actual model, request shape, node allocation and accelerator
    replica configuration that gets measured.
    """
    return (
        str(model_id), str(model_revision), str(device), int(batch_size),
        int(max_length), max(1, int(request_concurrency)),
        max(1, int(cpu_core_limit)), max(1, int(replica_count)),
    )


def _plan_condition_key(plan: ExecutionPlan) -> ConditionKey:
    return _condition_key(
        model_id=plan.profile.model_id,
        model_revision=plan.profile.revision,
        device=plan.device,
        batch_size=plan.profile.batch_size,
        max_length=plan.profile.max_length,
        request_concurrency=plan.profile.request_concurrency,
        cpu_core_limit=plan.cpu_core_limit,
        replica_count=plan.replica_count,
    )


def _successful_condition_keys(paths: Sequence[str | Path]) -> set[ConditionKey]:
    """Read successful conditions from completed compatible collection DBs."""
    keys: set[ConditionKey] = set()
    for source in dict.fromkeys(Path(item).expanduser().resolve() for item in paths):
        if not source.is_file():
            continue
        connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"collection_runs", "execution_measurements"} <= tables:
                continue
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(execution_measurements)")}
            required = {"model_id", "model_revision", "device", "batch_size", "max_length", "success"}
            if not required <= columns:
                continue
            request_concurrency = "request_concurrency" if "request_concurrency" in columns else "1"
            cpu_core_limit = "cpu_core_limit" if "cpu_core_limit" in columns else "cpu_threads"
            replica_count = "replica_count" if "replica_count" in columns else "1"
            rows = connection.execute(
                f"""
                SELECT em.model_id, em.model_revision, em.device, em.batch_size, em.max_length,
                       {request_concurrency} AS request_concurrency,
                       {cpu_core_limit} AS cpu_core_limit,
                       {replica_count} AS replica_count
                  FROM execution_measurements em
                  JOIN collection_runs cr ON cr.run_id = em.run_id
                 WHERE em.success = 1 AND cr.status = 'complete'
                """
            )
            keys.update(
                _condition_key(
                    model_id=row[0], model_revision=row[1], device=row[2], batch_size=row[3], max_length=row[4],
                    request_concurrency=row[5], cpu_core_limit=row[6], replica_count=row[7],
                )
                for row in rows
            )
        finally:
            connection.close()
    return keys


def _select_unobserved_plans(
    plans: Sequence[ExecutionPlan], existing: set[ConditionKey]
) -> tuple[list[ExecutionPlan], int]:
    """Retain one previously unmeasured plan per physical execution shape."""
    selected: list[ExecutionPlan] = []
    queued: set[ConditionKey] = set()
    skipped = 0
    for plan in plans:
        key = _plan_condition_key(plan)
        if key in existing or key in queued:
            skipped += 1
            continue
        queued.add(key)
        selected.append(plan)
    return selected, skipped


class LiveCollectionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self._create_schema()

    def __enter__(self) -> "LiveCollectionStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.connection.close()

    def begin_run(self, run_id: str, config: Mapping[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO collection_runs(
                    run_id, schema_version, collector_version, host, started_at, status, config_json
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (run_id, LIVE_SCHEMA_VERSION, COLLECTOR_VERSION, socket.gethostname(), _now(), _json(config)),
            )

    def finish_run(self, run_id: str, status: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE collection_runs
                   SET finished_at = ?,
                       status = ?,
                       execution_count = (SELECT COUNT(*) FROM execution_measurements WHERE run_id = ?),
                       success_count = (SELECT COUNT(*) FROM execution_measurements WHERE run_id = ? AND success = 1)
                 WHERE run_id = ?
                """,
                (_now(), status, run_id, run_id, run_id),
            )

    def add_execution(
        self,
        *,
        execution_id: str,
        run_id: str,
        ordinal: int,
        profile: InferenceProfile,
        device: str,
        started_at: str,
        result: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
        node_summary: Mapping[str, Any],
        replica_count: int = 1,
        cpu_core_limit: int | None = None,
        cpu_affinity: Sequence[int] = (),
    ) -> None:
        payload = dict(result or {})
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO execution_measurements(
                    execution_id, run_id, ordinal, profile_name, model_id, model_revision,
                    device, workload_id, request_concurrency, cpu_threads, cpu_core_limit,
                    cpu_affinity_json, replica_count,
                    load_condition, target_throughput_rps, latency_slo_ms, gpu_indices_json,
                    batch_size, max_length, repeats, dataset, started_at, finished_at,
                    success, load_seconds, compute_seconds, wall_seconds, cpu_seconds,
                    examples, examples_per_second, latency_p95_ms, max_rss_kib,
                    gpu_memory_allocated_bytes, gpu_memory_reserved_bytes,
                    node_summary_json, result_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id, run_id, int(ordinal), profile.name, profile.model_id, profile.revision,
                    device, profile.workload_id or profile.name, int(profile.request_concurrency),
                    int(profile.cpu_threads), int(cpu_core_limit or profile.cpu_threads),
                    _json([int(value) for value in cpu_affinity]), int(replica_count), profile.load_condition,
                    float(profile.target_throughput_rps), float(profile.latency_slo_ms),
                    _json(payload.get("replica_gpu_indices", [])),
                    profile.batch_size, profile.max_length, profile.repeats, profile.dataset,
                    started_at, str(payload.get("finished_at") or _now()), int(bool(payload.get("success", False))),
                    _number_or_none(payload.get("load_seconds")), _number_or_none(payload.get("compute_seconds")),
                    _number_or_none(payload.get("wall_seconds")), _number_or_none(payload.get("cpu_seconds")),
                    _integer_or_none(payload.get("inference_examples")),
                    _number_or_none(payload.get("inference_examples_per_second")),
                    _number_or_none(payload.get("batch_latency_p95_ms")),
                    _integer_or_none(payload.get("max_rss_kib")),
                    _integer_or_none(payload.get("gpu_memory_allocated_bytes")),
                    _integer_or_none(payload.get("gpu_memory_reserved_bytes")),
                    _json(node_summary), _json(payload) if payload else None,
                    _json(error or {}),
                ),
            )

    def add_samples(self, execution_id: str, samples: Iterable[Mapping[str, Any]]) -> None:
        rows = [
            (
                execution_id,
                str(sample["observed_at"]),
                str(sample["phase"]),
                _number_or_none(sample.get("cpu_percent")),
                _integer_or_none(sample.get("memory_total_bytes")),
                _integer_or_none(sample.get("memory_available_bytes")),
                _number_or_none(sample.get("load_average_1m")),
                _json(sample.get("gpus", [])),
                _json(sample),
            )
            for sample in samples
        ]
        if not rows:
            return
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO node_samples(
                    execution_id, observed_at, phase, cpu_percent, memory_total_bytes,
                    memory_available_bytes, load_average_1m, gpus_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def export_csv(self, directory: str | Path) -> dict[str, str]:
        destination = Path(directory).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        files = {
            "runs": destination / "collection_runs.csv",
            "executions": destination / "execution_measurements.csv",
            "samples": destination / "node_samples.csv",
        }
        for name, query in {
            "runs": "SELECT * FROM collection_runs ORDER BY started_at, run_id",
            "executions": "SELECT * FROM execution_measurements ORDER BY run_id, ordinal, execution_id",
            "samples": "SELECT * FROM node_samples ORDER BY sample_id",
        }.items():
            cursor = self.connection.execute(query)
            with files[name].open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([column[0] for column in cursor.description])
                writer.writerows(cursor)
        return {key: str(value) for key, value in files.items()}

    def summary(self, run_id: str) -> dict[str, int]:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS executions,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                   (SELECT COUNT(*) FROM node_samples ns
                      JOIN execution_measurements em ON em.execution_id = ns.execution_id
                     WHERE em.run_id = ?) AS samples
              FROM execution_measurements WHERE run_id = ?
            """,
            (run_id, run_id),
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                collector_version TEXT NOT NULL,
                host TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                execution_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                config_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_measurements (
                execution_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
                ordinal INTEGER NOT NULL,
                profile_name TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                device TEXT NOT NULL,
                workload_id TEXT NOT NULL DEFAULT '',
                request_concurrency INTEGER NOT NULL DEFAULT 1,
                cpu_threads INTEGER NOT NULL DEFAULT 2,
                cpu_core_limit INTEGER NOT NULL DEFAULT 2,
                cpu_affinity_json TEXT NOT NULL DEFAULT '[]',
                replica_count INTEGER NOT NULL DEFAULT 1,
                load_condition TEXT NOT NULL DEFAULT 'nominal',
                target_throughput_rps REAL NOT NULL DEFAULT 0,
                latency_slo_ms REAL NOT NULL DEFAULT 0,
                gpu_indices_json TEXT NOT NULL DEFAULT '[]',
                batch_size INTEGER NOT NULL,
                max_length INTEGER NOT NULL,
                repeats INTEGER NOT NULL,
                dataset TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                load_seconds REAL,
                compute_seconds REAL,
                wall_seconds REAL,
                cpu_seconds REAL,
                examples INTEGER,
                examples_per_second REAL,
                latency_p95_ms REAL,
                max_rss_kib INTEGER,
                gpu_memory_allocated_bytes INTEGER,
                gpu_memory_reserved_bytes INTEGER,
                node_summary_json TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS node_samples (
                sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL REFERENCES execution_measurements(execution_id) ON DELETE CASCADE,
                observed_at TEXT NOT NULL,
                phase TEXT NOT NULL,
                cpu_percent REAL,
                memory_total_bytes INTEGER,
                memory_available_bytes INTEGER,
                load_average_1m REAL,
                gpus_json TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_khu_hf_execution_run
                ON execution_measurements(run_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_khu_hf_sample_execution
                ON node_samples(execution_id, sample_id);
            """
        )
        existing = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(execution_measurements)")}
        for name, definition in {
            "workload_id": "TEXT NOT NULL DEFAULT ''",
            "request_concurrency": "INTEGER NOT NULL DEFAULT 1",
            "cpu_threads": "INTEGER NOT NULL DEFAULT 2",
            "cpu_core_limit": "INTEGER NOT NULL DEFAULT 2",
            "cpu_affinity_json": "TEXT NOT NULL DEFAULT '[]'",
            "replica_count": "INTEGER NOT NULL DEFAULT 1",
            "load_condition": "TEXT NOT NULL DEFAULT 'nominal'",
            "target_throughput_rps": "REAL NOT NULL DEFAULT 0",
            "latency_slo_ms": "REAL NOT NULL DEFAULT 0",
            "gpu_indices_json": "TEXT NOT NULL DEFAULT '[]'",
        }.items():
            if name not in existing:
                self.connection.execute(f"ALTER TABLE execution_measurements ADD COLUMN {name} {definition}")
        self.connection.commit()


class NodeSampler:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.samples: list[dict[str, Any]] = []
        self._stop = Event()
        self._thread: Thread | None = None
        self._previous_cpu: tuple[int, int] | None = None

    def snapshot(self, phase: str) -> None:
        sample, self._previous_cpu = _node_snapshot(phase, self._previous_cpu)
        self.samples.append(sample)

    def start(self) -> None:
        self.snapshot("before")
        self._thread = Thread(target=self._loop, name="khu-node-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 3))
        self.snapshot("after")

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.snapshot("running")


def collect_live(
    *,
    destination_db: str | Path,
    cache_dir: str | Path,
    csv_dir: str | Path | None = None,
    run_id: str | None = None,
    device_scope: str = "both",
    profile_set: str = "baseline",
    remote_grid_shard: int = 0,
    max_cases: int = 6,
    sample_interval_seconds: float = 0.25,
    seed: int = 42,
    cpu_core_counts: Sequence[int] | None = None,
    cpu_core_samples_per_profile: int | None = None,
    apply_cpu_core_counts_to_gpu: bool = False,
    skip_existing_conditions: bool = False,
    dedupe_from_dbs: Sequence[str | Path] = (),
) -> dict[str, Any]:
    if device_scope not in {"cpu", "gpu", "both"}:
        raise ValueError("device_scope must be cpu, gpu, or both")
    if profile_set not in {
        "baseline", "diverse", "local-novel", "local-space-filling", "local-exhaustive", "cpu-scaling", "remote-cpu-scaling", "remote-diverse", "remote-novel", "remote-space-filling", "remote-exhaustive", "remote-model-diverse"
    }:
        raise ValueError(
            "profile_set must be baseline, diverse, local-novel, local-space-filling, local-exhaustive, cpu-scaling, remote-cpu-scaling, remote-diverse, remote-novel, remote-space-filling, remote-exhaustive, or remote-model-diverse"
        )
    if max_cases <= 0:
        raise ValueError("max_cases must be positive")
    if remote_grid_shard < 0:
        raise ValueError("remote_grid_shard must be non-negative")
    if cpu_core_samples_per_profile is not None and cpu_core_samples_per_profile <= 0:
        raise ValueError("cpu_core_samples_per_profile must be positive when provided")
    if profile_set != "remote-exhaustive" and remote_grid_shard:
        raise ValueError("remote_grid_shard is supported only with profile_set=remote-exhaustive")
    selected_run = run_id or f"khu-hf-{uuid4().hex[:12]}"
    devices = ("cpu", "cuda") if device_scope == "both" else (("cpu",) if device_scope == "cpu" else ("cuda",))
    profiles = {
        "baseline": DEFAULT_PROFILES,
        "diverse": DIVERSE_PROFILES,
        "local-novel": LOCAL_NOVEL_PROFILES,
        "local-space-filling": LOCAL_SPACE_FILLING_PROFILES,
        "local-exhaustive": LOCAL_EXHAUSTIVE_PROFILES,
        "cpu-scaling": CPU_SCALING_PROFILES,
        "remote-cpu-scaling": REMOTE_CPU_SCALING_PROFILES,
        "remote-diverse": REMOTE_DIVERSE_PROFILES,
        "remote-novel": REMOTE_NOVEL_PROFILES,
        "remote-space-filling": REMOTE_SPACE_FILLING_PROFILES,
        "remote-exhaustive": remote_exhaustive_profiles(remote_grid_shard),
        "remote-model-diverse": REMOTE_MODEL_DIVERSE_PROFILES,
    }[profile_set]
    available_gpu_indices = _available_gpu_indices()
    available_cpu_indices = _available_cpu_indices()
    requested_cpu_core_counts = _normalize_cpu_core_counts(cpu_core_counts)
    plans, skipped = _plans_for(
        profiles,
        devices,
        available_gpu_indices,
        available_cpu_indices=available_cpu_indices,
        cpu_core_counts=requested_cpu_core_counts,
        cpu_core_samples_per_profile=cpu_core_samples_per_profile,
        apply_cpu_core_counts_to_gpu=apply_cpu_core_counts_to_gpu,
    )
    candidate_plan_count = len(plans)
    dedupe_paths = tuple(dedupe_from_dbs)
    if skip_existing_conditions:
        dedupe_paths = (destination_db, *dedupe_paths)
    existing_conditions = _successful_condition_keys(dedupe_paths)
    plans, skipped_existing_conditions = _select_unobserved_plans(plans, existing_conditions)
    plans = plans[:max_cases]
    if not plans:
        raise ValueError("no unobserved execution conditions remain in the requested collection matrix")
    configuration = {
        "source": "actual_huggingface_inference",
        "cache_dir": str(Path(cache_dir).expanduser().resolve()),
        "device_scope": device_scope,
        "profile_set": profile_set,
        "remote_grid_shard": int(remote_grid_shard),
        "max_cases": int(max_cases),
        "sample_interval_seconds": float(sample_interval_seconds),
        "seed": int(seed),
        "available_gpu_indices": available_gpu_indices,
        "available_cpu_indices": available_cpu_indices,
        "requested_cpu_core_counts": requested_cpu_core_counts,
        "cpu_core_samples_per_profile": cpu_core_samples_per_profile,
        "apply_cpu_core_counts_to_gpu": bool(apply_cpu_core_counts_to_gpu),
        "skip_existing_conditions": bool(skip_existing_conditions),
        "dedupe_source_dbs": [str(Path(item).expanduser().resolve()) for item in dedupe_paths],
        "candidate_plan_count": candidate_plan_count,
        "skipped_existing_conditions": skipped_existing_conditions,
        "selected_unique_plan_count": len(plans),
        "skipped_gpu_replica_conditions": skipped,
        "profiles": [
            {
                **asdict(plan.profile),
                "device": plan.device,
                "replica_count": plan.replica_count,
                "cpu_core_limit": plan.cpu_core_limit,
            }
            for plan in plans
        ],
        "local_files_only": True,
    }
    with LiveCollectionStore(destination_db) as store:
        store.begin_run(selected_run, configuration)
        failed = False
        for ordinal, plan in enumerate(plans):
            execution_id = f"{selected_run}-{ordinal:04d}-{plan.device}-{plan.replica_count}x"
            started_at = _now()
            sampler = NodeSampler(sample_interval_seconds)
            result: Mapping[str, Any] | None = None
            error: Mapping[str, Any] | None = None
            sampler.start()
            try:
                with _cpu_affinity(plan.cpu_core_limit, available_cpu_indices) as cpu_affinity:
                    result = _run_plan(
                        plan=plan,
                        cache_dir=str(Path(cache_dir).expanduser().resolve()),
                        seed=int(seed) + ordinal,
                        available_gpu_indices=available_gpu_indices,
                        cpu_affinity=cpu_affinity,
                    )
            except Exception as exc:
                failed = True
                error = {"type": type(exc).__name__, "message": str(exc)}
                print(
                    json.dumps(
                        {
                            "event": "huggingface_workload_failed",
                            "execution_id": execution_id,
                            "profile_name": plan.profile.name,
                            "model_id": plan.profile.model_id,
                            "device": plan.device,
                            "replica_count": plan.replica_count,
                            "error": error,
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                sampler.stop()
            node_summary = _summarize_samples(sampler.samples)
            store.add_execution(
                execution_id=execution_id,
                run_id=selected_run,
                ordinal=ordinal,
                profile=plan.profile,
                device=plan.device,
                replica_count=plan.replica_count,
                cpu_core_limit=plan.cpu_core_limit,
                cpu_affinity=tuple(result.get("cpu_affinity", ())) if result else (),
                started_at=started_at,
                result=result,
                error=error,
                node_summary=node_summary,
            )
            store.add_samples(execution_id, sampler.samples)
            _release_accelerator_memory()
        store.finish_run(selected_run, "failed" if failed else "complete")
        summary = store.summary(selected_run)
        exports = store.export_csv(csv_dir) if csv_dir else {}
    return {
        "run_id": selected_run,
        "db": str(Path(destination_db).expanduser().resolve()),
        "csv": exports,
        "summary": summary,
    }


def _plans_for(
    profiles: Sequence[InferenceProfile],
    devices: Sequence[str],
    available_gpu_indices: Sequence[int],
    *,
    available_cpu_indices: Sequence[int] | None = None,
    cpu_core_counts: Sequence[int] = (),
    cpu_core_samples_per_profile: int | None = None,
    apply_cpu_core_counts_to_gpu: bool = False,
) -> tuple[list[ExecutionPlan], list[dict[str, Any]]]:
    plans: list[ExecutionPlan] = []
    skipped: list[dict[str, Any]] = []
    cpu_indices = tuple(available_cpu_indices or _available_cpu_indices())
    for profile_index, profile in enumerate(profiles):
        if "cpu" in devices and "cpu" in profile.devices:
            limits = tuple(cpu_core_counts) or (int(profile.cpu_threads),)
            if cpu_core_samples_per_profile is not None and cpu_core_samples_per_profile < len(limits):
                # Spread CPU allocations across workload shapes instead of
                # exhaustively repeating all limits per shape.  This keeps the
                # global scaling range while prioritizing workload diversity.
                limits = tuple(
                    limits[(profile_index + offset) % len(limits)]
                    for offset in range(cpu_core_samples_per_profile)
                )
            for core_limit in limits:
                if core_limit <= len(cpu_indices):
                    plans.append(
                        ExecutionPlan(
                            profile=replace(profile, cpu_threads=int(core_limit)),
                            device="cpu",
                            replica_count=1,
                            cpu_core_limit=int(core_limit),
                        )
                    )
                else:
                    skipped.append(
                        {
                            "profile": profile.workload_id or profile.name,
                            "requested_cpu_cores": int(core_limit),
                            "available_cpu_count": len(cpu_indices),
                            "reason": "insufficient_available_cpus",
                        }
                    )
        if "cuda" not in devices or "cuda" not in profile.devices:
            continue
        gpu_cpu_limits = tuple(cpu_core_counts) if apply_cpu_core_counts_to_gpu and cpu_core_counts else (int(profile.cpu_threads),)
        for replica_count in profile.gpu_replica_counts:
            if replica_count <= len(available_gpu_indices):
                for core_limit in gpu_cpu_limits:
                    if core_limit <= len(cpu_indices):
                        plans.append(
                            ExecutionPlan(
                                profile=replace(profile, cpu_threads=int(core_limit)),
                                device="cuda",
                                replica_count=int(replica_count),
                                cpu_core_limit=int(core_limit),
                            )
                        )
                    else:
                        skipped.append(
                            {
                                "profile": profile.workload_id or profile.name,
                                "requested_cpu_cores": int(core_limit),
                                "available_cpu_count": len(cpu_indices),
                                "reason": "insufficient_available_cpus",
                            }
                        )
            else:
                skipped.append(
                    {
                        "profile": profile.workload_id or profile.name,
                        "requested_gpu_replicas": int(replica_count),
                        "available_gpu_count": len(available_gpu_indices),
                        "reason": "insufficient_physical_gpus",
                    }
                )
    return plans, skipped


def _available_gpu_indices() -> list[int]:
    return [int(item["index"]) for item in _gpu_snapshot() if item.get("index") is not None]


def _available_cpu_indices() -> list[int]:
    try:
        return sorted(int(value) for value in os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return list(range(max(1, os.cpu_count() or 1)))


def _normalize_cpu_core_counts(values: Sequence[int] | None) -> tuple[int, ...]:
    if values is None:
        return ()
    normalized = tuple(sorted({int(value) for value in values}))
    if not normalized or any(value <= 0 for value in normalized):
        raise ValueError("cpu_core_counts must contain positive integers")
    return normalized


@contextmanager
def _cpu_affinity(core_limit: int, available_cpu_indices: Sequence[int]) -> Iterator[tuple[int, ...]]:
    available = tuple(int(value) for value in available_cpu_indices)
    selected = available[: max(1, int(core_limit))]
    if len(selected) < int(core_limit):
        raise RuntimeError("requested CPU core limit is unavailable")
    try:
        original = tuple(sorted(int(value) for value in os.sched_getaffinity(0)))
        os.sched_setaffinity(0, selected)
    except (AttributeError, OSError):
        original = ()
    try:
        yield selected
    finally:
        if original:
            try:
                os.sched_setaffinity(0, original)
            except OSError:
                pass


def _run_plan(
    *, plan: ExecutionPlan, cache_dir: str, seed: int, available_gpu_indices: Sequence[int], cpu_affinity: Sequence[int]
) -> Mapping[str, Any]:
    if plan.device == "cpu":
        result = _run_profile(profile=plan.profile, device="cpu", cache_dir=cache_dir, seed=seed)
        return _enrich_result(result, plan, (), cpu_affinity)
    gpu_indices = tuple(int(value) for value in available_gpu_indices[:plan.replica_count])
    if len(gpu_indices) != plan.replica_count:
        raise RuntimeError("requested GPU replica count is unavailable")
    if plan.replica_count == 1:
        result = _run_profile(
            profile=plan.profile, device="cuda", cache_dir=cache_dir, seed=seed, gpu_index=gpu_indices[0]
        )
        result = {**result, "gpu_index": gpu_indices[0]}
        return _enrich_result(result, plan, gpu_indices, cpu_affinity)
    results = _run_gpu_replicas(profile=plan.profile, cache_dir=cache_dir, seed=seed, gpu_indices=gpu_indices)
    return _enrich_result(_aggregate_replicas(results, gpu_indices), plan, gpu_indices, cpu_affinity)


def _run_gpu_replicas(
    *, profile: InferenceProfile, cache_dir: str, seed: int, gpu_indices: Sequence[int]
) -> list[Mapping[str, Any]]:
    # Spawn, rather than thread, one process per physical GPU.  CUDA device
    # visibility is set before Torch is imported in each child process.
    context = mp.get_context("spawn")
    result_queue: Any = context.Queue()
    workers = [
        context.Process(
            target=_gpu_replica_worker,
            args=(result_queue, profile, cache_dir, int(seed) + ordinal, int(gpu_index)),
        )
        for ordinal, gpu_index in enumerate(gpu_indices)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    messages: list[Mapping[str, Any]] = []
    for _ in workers:
        try:
            messages.append(result_queue.get(timeout=5.0))
        except queue.Empty as exc:
            raise RuntimeError("GPU replica exited without a measurement result") from exc
    failures = [item for item in messages if "error" in item]
    if failures:
        detail = failures[0]["error"]
        raise RuntimeError(f"GPU replica failed: {detail.get('type')}: {detail.get('message')}")
    if any(worker.exitcode != 0 for worker in workers):
        raise RuntimeError("GPU replica process returned a non-zero exit status")
    return [dict(item["result"]) for item in messages]


def _gpu_replica_worker(
    result_queue: Any, profile: InferenceProfile, cache_dir: str, seed: int, gpu_index: int
) -> None:
    try:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
        result = dict(_run_profile(profile=profile, device="cuda", cache_dir=cache_dir, seed=seed))
        # CUDA_VISIBLE_DEVICES remaps the process-local device to zero; retain
        # the physical device identity in stored evidence.
        result["gpu_index"] = gpu_index
        result_queue.put({"result": result})
    except Exception as exc:
        result_queue.put({"error": {"type": type(exc).__name__, "message": str(exc)}})


def _aggregate_replicas(results: Sequence[Mapping[str, Any]], gpu_indices: Sequence[int]) -> dict[str, Any]:
    if not results:
        raise ValueError("at least one GPU replica result is required")
    first = dict(results[0])
    number = lambda name: [float(item.get(name, 0.0) or 0.0) for item in results]
    integer = lambda name: [int(item.get(name, 0) or 0) for item in results]
    first.update(
        {
            "device": "cuda:replicated",
            "device_name": " + ".join(str(item.get("device_name", "CUDA")) for item in results),
            "load_seconds": round(max(number("load_seconds")), 6),
            "compute_seconds": round(max(number("compute_seconds")), 6),
            "wall_seconds": round(max(number("wall_seconds")), 6),
            "cpu_seconds": round(sum(number("cpu_seconds")), 6),
            "inference_examples": sum(integer("inference_examples")),
            "inference_batches": sum(integer("inference_batches")),
            "inference_examples_per_second": round(sum(number("inference_examples_per_second")), 6),
            "batch_latency_mean_ms": round(max(number("batch_latency_mean_ms")), 6),
            "batch_latency_p50_ms": round(max(number("batch_latency_p50_ms")), 6),
            "batch_latency_p95_ms": round(max(number("batch_latency_p95_ms")), 6),
            "max_rss_kib": sum(integer("max_rss_kib")),
            "gpu_memory_allocated_bytes": sum(integer("gpu_memory_allocated_bytes")),
            "gpu_memory_reserved_bytes": sum(integer("gpu_memory_reserved_bytes")),
            "model_parameter_count": sum(integer("model_parameter_count")),
            "model_parameter_bytes": sum(integer("model_parameter_bytes")),
            "replica_gpu_indices": [int(value) for value in gpu_indices],
            "replica_results": [dict(item) for item in results],
            "success": all(bool(item.get("success", False)) for item in results),
            "finished_at": _now(),
        }
    )
    return first


def _enrich_result(
    result: Mapping[str, Any], plan: ExecutionPlan, gpu_indices: Sequence[int], cpu_affinity: Sequence[int]
) -> dict[str, Any]:
    payload = dict(result)
    payload.update(
        {
            "workload_id": plan.profile.workload_id or plan.profile.name,
            "request_concurrency": int(plan.profile.request_concurrency),
            "cpu_threads": int(plan.profile.cpu_threads),
            "cpu_core_limit": int(plan.cpu_core_limit),
            "cpu_affinity": [int(value) for value in cpu_affinity],
            "requested_replica_count": int(plan.replica_count),
            "actual_replica_count": int(plan.replica_count),
            "replica_gpu_indices": [int(value) for value in gpu_indices],
            "load_condition": plan.profile.load_condition,
            "target_throughput_rps": float(plan.profile.target_throughput_rps),
            "latency_slo_ms": float(plan.profile.latency_slo_ms),
        }
    )
    return payload


def _run_profile(
    *, profile: InferenceProfile, device: str, cache_dir: str, seed: int, gpu_index: int | None = None
) -> Mapping[str, Any]:
    import torch

    if device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        if gpu_index is not None:
            torch.cuda.set_device(gpu_index)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    if profile.family == "causal_lm":
        return _run_causal_lm(profile=profile, device=device, cache_dir=cache_dir, seed=seed)
    if profile.family != "sequence_classification":
        raise ValueError(f"unsupported model family: {profile.family}")
    from federated_coordination_agent.huggingface_workload import run_inference

    return run_inference(
        model_id=profile.model_id,
        revision=profile.revision,
        cache_dir=cache_dir,
        repeats=profile.repeats,
        batch_size=profile.batch_size,
        max_length=profile.max_length,
        threads=profile.cpu_threads,
        request_concurrency=profile.request_concurrency,
        device=device,
        dataset=profile.dataset,
        seed=seed,
        quality_metric_valid=False,
    )


def _run_causal_lm(
    *, profile: InferenceProfile, device: str, cache_dir: str, seed: int
) -> Mapping[str, Any]:
    """Execute a real local Qwen-style causal-LM forward benchmark."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    target = torch.device("cuda:0" if device == "cuda" else "cpu")
    torch.manual_seed(seed)
    torch.set_num_threads(profile.cpu_threads)
    started_at = _now()
    wall_started = time.monotonic()
    cpu_started = time.process_time()
    loaded_at = time.monotonic()
    model_source = _local_snapshot_source(profile.model_id, profile.revision, cache_dir)
    model = AutoModelForCausalLM.from_pretrained(
        model_source,
        revision=None if model_source != profile.model_id else profile.revision,
        cache_dir=cache_dir,
        local_files_only=True,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    )
    tokenizer = None
    tokenizer_error = ""
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            revision=None if model_source != profile.model_id else profile.revision,
            cache_dir=cache_dir,
            local_files_only=True,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
    except (OSError, ValueError) as exc:
        # Several existing icns-216 cache snapshots contain a complete model
        # but no tokenizer. The model is still genuinely loaded and executed;
        # use a deterministic in-vocabulary tensor and record that provenance.
        tokenizer_error = f"{type(exc).__name__}: {exc}"
    load_seconds = time.monotonic() - loaded_at
    model.to(target)
    model.eval()
    prompts = (
        "Summarize the node resource condition for a reliable AI deployment.",
        "Explain why GPU memory headroom matters for inference placement.",
        "Return a concise deployment health assessment from latency and throughput.",
        "Describe one safe scale-out decision for an overloaded inference service.",
    )
    # ``padding=max_length`` makes the declared sequence length a real tensor
    # shape rather than a merely permissive tokenizer truncation limit.
    filler = " node telemetry latency throughput memory accelerator placement"
    if tokenizer is not None:
        batch_prompts = [
            prompts[index % len(prompts)] + filler * max(1, profile.max_length // 6)
            for index in range(profile.batch_size)
        ]
        encoded = tokenizer(
            batch_prompts,
            padding="max_length",
            truncation=True,
            max_length=profile.max_length,
            return_tensors="pt",
        )
        input_encoding = "tokenizer_text"
    else:
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed)
        vocab_size = max(2, int(getattr(model.config, "vocab_size", 32000)))
        encoded = {
            "input_ids": torch.randint(
                low=0,
                high=vocab_size,
                size=(profile.batch_size, profile.max_length),
                generator=generator,
                dtype=torch.long,
            ),
            "attention_mask": torch.ones(
                (profile.batch_size, profile.max_length), dtype=torch.long
            ),
        }
        input_encoding = "deterministic_in_vocabulary_token_ids"
    encoded = {key: value.to(target) for key, value in encoded.items()}
    with torch.no_grad():
        model(**encoded)
    if device == "cuda":
        torch.cuda.synchronize(target)
    latencies: list[float] = []
    processed = 0
    compute_started = time.monotonic()
    if profile.request_concurrency == 1:
        with torch.no_grad():
            for _ in range(profile.repeats):
                if device == "cuda":
                    torch.cuda.synchronize(target)
                batch_started = time.perf_counter()
                model(**encoded)
                if device == "cuda":
                    torch.cuda.synchronize(target)
                latencies.append((time.perf_counter() - batch_started) * 1000.0)
                processed += profile.batch_size
    else:
        model_lock = Lock()

        def invoke(_: int) -> float:
            request_started = time.perf_counter()
            with model_lock, torch.no_grad():
                if device == "cuda":
                    torch.cuda.synchronize(target)
                model(**encoded)
                if device == "cuda":
                    torch.cuda.synchronize(target)
            return (time.perf_counter() - request_started) * 1000.0

        request_count = profile.repeats * profile.request_concurrency
        with ThreadPoolExecutor(max_workers=profile.request_concurrency, thread_name_prefix="qwen-load") as executor:
            for latency in executor.map(invoke, range(request_count)):
                latencies.append(latency)
                processed += profile.batch_size
    compute_seconds = time.monotonic() - compute_started
    wall_seconds = time.monotonic() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    examples = processed
    parameters = sum(parameter.numel() for parameter in model.parameters())
    parameter_bytes = sum(parameter.numel() * parameter.element_size() for parameter in model.parameters())
    result: dict[str, Any] = {
        "event": "huggingface_workload_finished",
        "workload_task": "causal_lm_forward",
        "model_id": profile.model_id,
        "model_revision": profile.revision,
        "model_source": model_source,
        "input_encoding": input_encoding,
        "tokenizer_fallback_error": tokenizer_error,
        "device": str(target),
        "device_name": torch.cuda.get_device_name(target) if device == "cuda" else "CPU",
        "seed": seed,
        "started_at": started_at,
        "finished_at": _now(),
        "load_seconds": round(load_seconds, 6),
        "compute_seconds": round(compute_seconds, 6),
        "wall_seconds": round(wall_seconds, 6),
        "cpu_seconds": round(cpu_seconds, 6),
        "process_cpu_utilization_percent": round(cpu_seconds / max(wall_seconds, 1e-9) * 100.0, 6),
        "repeats": profile.repeats,
        "request_concurrency": profile.request_concurrency,
        "batch_size": profile.batch_size,
        "max_length": profile.max_length,
        "inference_examples": examples,
        "inference_batches": len(latencies),
        "inference_examples_per_second": round(examples / max(compute_seconds, 1e-9), 6),
        "batch_latency_mean_ms": round(statistics.fmean(latencies), 6),
        "batch_latency_p50_ms": round(_percentile(latencies, 0.50), 6),
        "batch_latency_p95_ms": round(_percentile(latencies, 0.95), 6),
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "model_parameter_count": int(parameters),
        "model_parameter_bytes": int(parameter_bytes),
        "success": True,
    }
    if device == "cuda":
        result.update(
            {
                "gpu_index": int(torch.cuda.current_device()),
                "gpu_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(target)),
                "gpu_memory_reserved_bytes": int(torch.cuda.max_memory_reserved(target)),
            }
        )
    del encoded, model, tokenizer
    return result


def _local_snapshot_source(model_id: str, revision: str, cache_dir: str) -> str:
    """Resolve an explicitly pinned local HF snapshot when it is available."""
    model_key = "models--" + model_id.replace("/", "--")
    root = Path(cache_dir).expanduser()
    for candidate in (
        root / "hub" / model_key / "snapshots" / revision,
        root / model_key / "snapshots" / revision,
    ):
        if (candidate / "config.json").is_file():
            return str(candidate)
    return model_id


def _release_accelerator_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, RuntimeError):
        return


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _node_snapshot(
    phase: str, previous_cpu: tuple[int, int] | None
) -> tuple[dict[str, Any], tuple[int, int] | None]:
    current_cpu = _cpu_counters()
    cpu_percent = None
    if current_cpu is not None and previous_cpu is not None:
        total_delta = current_cpu[0] - previous_cpu[0]
        idle_delta = current_cpu[1] - previous_cpu[1]
        if total_delta > 0:
            cpu_percent = round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 4)
    memory = _memory_info()
    try:
        load_average = round(float(os_getloadavg()[0]), 6)
    except OSError:
        load_average = None
    return (
        {
            "observed_at": _now(),
            "phase": phase,
            "cpu_percent": cpu_percent,
            "memory_total_bytes": memory.get("MemTotal", 0) * 1024,
            "memory_available_bytes": memory.get("MemAvailable", 0) * 1024,
            "load_average_1m": load_average,
            "gpus": _gpu_snapshot(),
        },
        current_cpu,
    )


def os_getloadavg() -> tuple[float, float, float]:
    import os

    return os.getloadavg()


def _cpu_counters() -> tuple[int, int] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
        values = [int(value) for value in fields[1:]]
    except (IndexError, OSError, ValueError):
        return None
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def _memory_info() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            result[key] = int(raw.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    return result


def _gpu_snapshot() -> list[dict[str, Any]]:
    query = "index,name,utilization.gpu,memory.used,memory.total,power.draw"
    try:
        output = subprocess.check_output(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    result: list[dict[str, Any]] = []
    for line in output.splitlines():
        values = [item.strip() for item in line.split(",")]
        if len(values) != 6:
            continue
        result.append(
            {
                "index": _integer_or_none(values[0]),
                "name": values[1],
                "utilization_percent": _number_or_none(values[2]),
                "memory_used_mib": _number_or_none(values[3]),
                "memory_total_mib": _number_or_none(values[4]),
                "power_watts": _number_or_none(values[5]),
            }
        )
    return result


def _summarize_samples(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    running = [sample for sample in samples if sample.get("phase") == "running"]
    source = running or list(samples)
    cpu = [float(sample["cpu_percent"]) for sample in source if sample.get("cpu_percent") is not None]
    available = [
        int(sample["memory_available_bytes"])
        for sample in source
        if sample.get("memory_available_bytes") is not None
    ]
    gpu_utilizations = [
        float(gpu["utilization_percent"])
        for sample in source
        for gpu in sample.get("gpus", [])
        if gpu.get("utilization_percent") is not None
    ]
    gpu_memory = [
        float(gpu["memory_used_mib"])
        for sample in source
        for gpu in sample.get("gpus", [])
        if gpu.get("memory_used_mib") is not None
    ]
    return {
        "sample_count": len(samples),
        "running_sample_count": len(running),
        "cpu_percent_mean": round(sum(cpu) / len(cpu), 6) if cpu else None,
        "cpu_percent_max": round(max(cpu), 6) if cpu else None,
        "memory_available_bytes_min": min(available) if available else None,
        "gpu_utilization_percent_mean": round(sum(gpu_utilizations) / len(gpu_utilizations), 6)
        if gpu_utilizations
        else None,
        "gpu_utilization_percent_max": round(max(gpu_utilizations), 6) if gpu_utilizations else None,
        "gpu_memory_used_mib_max": round(max(gpu_memory), 6) if gpu_memory else None,
    }


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer_or_none(value: Any) -> int | None:
    number = _number_or_none(value)
    return int(number) if number is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
