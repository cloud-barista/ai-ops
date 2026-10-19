"""Frozen, retrospective IOG RTX 4090 GPU 2-to-4 response experiment.

Run ``freeze`` once before ``score``. The saved protocol, script, source
manifest and compressed original files are hash-checked before scoring.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/iog_gpu24_response_protocol.md"
OUTPUT = ROOT / "artifacts/iog_gpu24_response_v1"
REPO = "IOG-Foundation/llm-inference-benchmarks"
COMMIT = "4990a807c6f1971edda635c18d98550059782eb8"
EXPECTED_SOURCE_DIGEST = "92ffc3479de653491c7f252c88adfc3ff9c1e231ad23672978b8d3962d32f215"
EXPECTED_SOURCE_FILES = 120
EXPECTED_SOURCE_BYTES = 182641
QPS_VALUES = (1, 4, 8, 16, 32)
GPU_NAME = "NVIDIA-GeForce-RTX-4090"
MODEL_SLUGS = {
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B": "DeepSeek-R1-Distill-Llama-8B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": "DeepSeek-R1-Distill-Qwen-7B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B": "DeepSeek-R1-Distill-Qwen-14B",
}
METHODS = ("anchor_keep", "proportional", "capped_offered_load", "train_mean", "ridge")


@dataclass(frozen=True)
class Cell:
    model_id: str
    qps: int
    gpu_count: int
    request_throughput: float
    p95_ttft_ms: float
    total_input_tokens: int
    total_output_tokens: int
    completed: int
    date: str
    source_path: str
    source_line: int


@dataclass(frozen=True)
class Anchor:
    model_id: str
    qps: int
    request_throughput: float
    p95_ttft_ms: float


@dataclass(frozen=True)
class Pair:
    gpu2: Cell
    gpu4: Cell

    @property
    def model_id(self) -> str:
        return self.gpu2.model_id

    @property
    def qps(self) -> int:
        return self.gpu2.qps

    def anchor(self) -> Anchor:
        return Anchor(self.model_id, self.qps,
                      self.gpu2.request_throughput, self.gpu2.p95_ttft_ms)

    def true_log_response(self) -> np.ndarray:
        return np.array([
            math.log(self.gpu4.request_throughput / self.gpu2.request_throughput),
            math.log(self.gpu4.p95_ttft_ms / self.gpu2.p95_ttft_ms),
        ], dtype=float)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def source_digest(members: dict[str, bytes]) -> str:
    lines = [f"{path} {sha256_bytes(data)}" for path, data in sorted(members.items())]
    return sha256_bytes("\n".join(lines).encode())


def _get_url(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "KHU-AI-MCMP-research-audit"})
    with urlopen(req, timeout=30) as response:
        return response.read()


def download_source() -> dict[str, bytes]:
    tree_url = f"https://api.github.com/repos/{REPO}/git/trees/{COMMIT}?recursive=1"
    tree = json.loads(_get_url(tree_url))
    if tree.get("truncated"):
        raise ValueError("GitHub tree was truncated")
    paths = sorted(item["path"] for item in tree["tree"]
                   if item["type"] == "blob" and
                   item["path"].startswith("results/") and
                   item["path"].endswith(".json"))
    if len(paths) != EXPECTED_SOURCE_FILES:
        raise ValueError(f"Expected {EXPECTED_SOURCE_FILES} source files; got {len(paths)}")

    def fetch(path: str) -> tuple[str, bytes]:
        url = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{quote(path, safe='/')}"
        return path, _get_url(url)

    with ThreadPoolExecutor(max_workers=8) as executor:
        members = dict(executor.map(fetch, paths))
    verify_source_members(members)
    return members


def iter_records(members: dict[str, bytes]):
    for path, raw in sorted(members.items()):
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError(f"Non-UTF8 source: {path}") from exc
        if not lines:
            raise ValueError(f"Empty source: {path}")
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON: {path}:{line_number}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"Non-object JSON: {path}:{line_number}")
            yield path, line_number, item


def verify_source_members(members: dict[str, bytes], *, enforce_expected: bool = True) -> None:
    if len(members) != EXPECTED_SOURCE_FILES and enforce_expected:
        raise ValueError("Wrong number of source files")
    if any(not p.startswith("results/") or not p.endswith(".json") or
           ".." in Path(p).parts for p in members):
        raise ValueError("Unexpected source path")
    if enforce_expected and sum(map(len, members.values())) != EXPECTED_SOURCE_BYTES:
        raise ValueError("Wrong source byte count")
    if enforce_expected and source_digest(members) != EXPECTED_SOURCE_DIGEST:
        raise ValueError("Source manifest hash mismatch")
    records = sum(1 for _ in iter_records(members))
    if enforce_expected and records != 130:
        raise ValueError(f"Expected 130 source records; got {records}")


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, raw in sorted(members.items()):
            info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, raw)
    return buffer.getvalue()


def freeze(output: Path = OUTPUT) -> dict:
    if (output / "freeze_manifest.json").exists():
        raise FileExistsError("Freeze manifest already exists")
    members = download_source()
    archive = make_zip(members)
    source_manifest = {
        "schema_version": "iog_source_manifest_v1",
        "repository": REPO,
        "commit": COMMIT,
        "file_count": len(members),
        "byte_count": sum(map(len, members.values())),
        "record_count": sum(1 for _ in iter_records(members)),
        "aggregate_sha256": source_digest(members),
        "files": [{"path": path, "sha256": sha256_bytes(raw), "bytes": len(raw)}
                  for path, raw in sorted(members.items())],
    }
    source_manifest_bytes = (json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n").encode()
    freeze_manifest = {
        "schema_version": "iog_gpu24_response_freeze_v1",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": sha256_file(PROTOCOL),
        "script_sha256": sha256_file(Path(__file__)),
        "source_manifest_sha256": sha256_bytes(source_manifest_bytes),
        "raw_results_zip_sha256": sha256_bytes(archive),
        "source_aggregate_sha256": source_manifest["aggregate_sha256"],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw_results.zip").write_bytes(archive)
    (output / "source_manifest.json").write_bytes(source_manifest_bytes)
    (output / "freeze_manifest.json").write_text(
        json.dumps(freeze_manifest, ensure_ascii=False, indent=2) + "\n")
    return freeze_manifest


def load_frozen(output: Path = OUTPUT) -> tuple[dict, dict[str, bytes]]:
    frozen = json.loads((output / "freeze_manifest.json").read_text())
    if frozen["protocol_sha256"] != sha256_file(PROTOCOL):
        raise ValueError("Protocol changed after freeze")
    if frozen["script_sha256"] != sha256_file(Path(__file__)):
        raise ValueError("Scoring script changed after freeze")
    manifest_raw = (output / "source_manifest.json").read_bytes()
    archive_raw = (output / "raw_results.zip").read_bytes()
    if frozen["source_manifest_sha256"] != sha256_bytes(manifest_raw):
        raise ValueError("Source manifest changed after freeze")
    if frozen["raw_results_zip_sha256"] != sha256_bytes(archive_raw):
        raise ValueError("Source archive changed after freeze")
    source_manifest = json.loads(manifest_raw)
    if source_manifest["repository"] != REPO or source_manifest["commit"] != COMMIT:
        raise ValueError("Wrong source provenance")
    with ZipFile(BytesIO(archive_raw)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive member")
        members = {path: archive.read(path) for path in names}
    expected = {row["path"]: (row["sha256"], row["bytes"])
                for row in source_manifest["files"]}
    if set(members) != set(expected):
        raise ValueError("Archive file set differs from manifest")
    if any((sha256_bytes(raw), len(raw)) != expected[path]
           for path, raw in members.items()):
        raise ValueError("Archive file differs from source manifest")
    verify_source_members(members)
    if source_manifest["aggregate_sha256"] != source_digest(members):
        raise ValueError("Source digest differs from manifest")
    if source_manifest["file_count"] != len(members) or \
       source_manifest["byte_count"] != sum(map(len, members.values())) or \
       source_manifest["record_count"] != sum(1 for _ in iter_records(members)):
        raise ValueError("Source counts differ from manifest")
    if frozen["source_aggregate_sha256"] != source_digest(members):
        raise ValueError("Source digest differs from freeze")
    return frozen, members


def _positive(value, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid positive {label}")
    return float(value)


def _integer(value, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"Invalid integer {label}")
    return value


def _cell(path: str, line: int, item: dict) -> Cell:
    model = item["model_id"]
    qps_raw = _positive(item["request_rate"], "request_rate")
    if int(qps_raw) != qps_raw or int(qps_raw) not in QPS_VALUES:
        raise ValueError("Unexpected QPS")
    qps = int(qps_raw)
    count = item["gpu_count"]
    if count not in ("2", "4"):
        raise ValueError("Unexpected GPU count")
    expected_path = (f"results/vllm_200prompts{qps}qps_{MODEL_SLUGS[model]}_"
                     f"sharegpt_{count}x{GPU_NAME}.json")
    if path != expected_path:
        raise ValueError("Source path and row identity differ")
    date = item["date"]
    if not isinstance(date, str) or not re.fullmatch(r"\d{8}-\d{6}", date):
        raise ValueError("Unexpected source date")
    datetime.strptime(date, "%Y%m%d-%H%M%S")
    if item["backend"] != "vllm" or item["gpu_name"] != GPU_NAME or \
       item["num_prompts"] != 200:
        raise ValueError("Unexpected source setting")
    return Cell(
        model_id=model, qps=qps, gpu_count=int(count),
        request_throughput=_positive(item["request_throughput"], "request_throughput"),
        p95_ttft_ms=_positive(item["p95_ttft_ms"], "p95_ttft_ms"),
        total_input_tokens=_integer(item["total_input_tokens"], "total_input_tokens"),
        total_output_tokens=_integer(item["total_output_tokens"], "total_output_tokens"),
        completed=_integer(item["completed"], "completed"),
        date=date, source_path=path, source_line=line,
    )


def select_pairs(members: dict[str, bytes]) -> tuple[list[Pair], list[Cell]]:
    candidates: dict[tuple[str, int, int], list[Cell]] = {}
    for path, line, item in iter_records(members):
        if item.get("model_id") not in MODEL_SLUGS or \
           item.get("gpu_name") != GPU_NAME or \
           item.get("gpu_count") not in ("2", "4") or \
           item.get("request_rate") not in QPS_VALUES:
            continue
        cell = _cell(path, line, item)
        candidates.setdefault((cell.model_id, cell.qps, cell.gpu_count), []).append(cell)
    pairs, selected = [], []
    for model in MODEL_SLUGS:
        for qps in QPS_VALUES:
            chosen = []
            for count in (2, 4):
                records = candidates.get((model, qps, count), [])
                if not records:
                    raise ValueError(f"Missing source cell: {model}, {qps}, {count}")
                cell = min(records, key=lambda x: (x.date, x.source_path, x.source_line))
                if cell.completed != 200:
                    raise ValueError("Incomplete selected benchmark")
                chosen.append(cell)
                selected.append(cell)
            if chosen[0].total_input_tokens != chosen[1].total_input_tokens:
                raise ValueError("Input token totals differ within GPU pair")
            pairs.append(Pair(chosen[0], chosen[1]))
    if len(selected) != 30 or len(pairs) != 15:
        raise ValueError("Wrong selected population")
    return pairs, selected


def anchor_features(anchors: list[Anchor]) -> np.ndarray:
    return np.array([[math.log(a.qps), math.log(a.request_throughput),
                      math.log(a.p95_ttft_ms)] for a in anchors], dtype=float)


def fit_predict_fold(train_pairs: list[Pair], held_anchors: list[Anchor]) -> dict[str, np.ndarray]:
    training_models = {p.model_id for p in train_pairs}
    if any(a.model_id in training_models for a in held_anchors):
        raise ValueError("Held-out model leaked into training")
    if not train_pairs or not held_anchors:
        raise ValueError("Empty training or held-out input")
    x_train = anchor_features([p.anchor() for p in train_pairs])
    x_held = anchor_features(held_anchors)
    y_train = np.stack([p.true_log_response() for p in train_pairs])
    scaler = StandardScaler().fit(x_train)
    x_train_scaled = scaler.transform(x_train)
    x_held_scaled = scaler.transform(x_held)
    ridge = np.column_stack([
        Ridge(alpha=1.0, fit_intercept=True).fit(x_train_scaled, y_train[:, k]).predict(x_held_scaled)
        for k in range(2)
    ])
    n = len(held_anchors)
    return {
        "anchor_keep": np.zeros((n, 2), dtype=float),
        "proportional": np.tile([math.log(2.0), 0.0], (n, 1)),
        "capped_offered_load": np.array([
            [math.log(min(2.0 * a.request_throughput,
                          max(float(a.qps), a.request_throughput)) /
                      a.request_throughput), 0.0]
            for a in held_anchors
        ], dtype=float),
        "train_mean": np.tile(y_train.mean(axis=0), (n, 1)),
        "ridge": ridge,
    }


def within_output_tolerance(pair: Pair) -> bool:
    return abs(math.log(pair.gpu4.total_output_tokens /
                        pair.gpu2.total_output_tokens)) <= math.log(1.05)


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def score(output: Path = OUTPUT) -> dict:
    if (output / "metrics.json").exists():
        raise FileExistsError("Score already exists")
    frozen, members = load_frozen(output)
    pairs, selected = select_pairs(members)
    predictions = []
    for held_model in MODEL_SLUGS:
        training = [p for p in pairs if p.model_id != held_model]
        held = [p for p in pairs if p.model_id == held_model]
        if len(training) != 10 or len(held) != 5:
            raise ValueError("Wrong model-complete split")
        estimates = fit_predict_fold(training, [p.anchor() for p in held])
        for index, pair in enumerate(held):
            truth = pair.true_log_response()
            token_ratio = pair.gpu4.total_output_tokens / pair.gpu2.total_output_tokens
            matched = within_output_tolerance(pair)
            for method in METHODS:
                estimate = estimates[method][index]
                predictions.append({
                    "heldout_model_id": held_model, "qps": pair.qps, "method": method,
                    "rps2": pair.gpu2.request_throughput, "ttft2_p95_ms": pair.gpu2.p95_ttft_ms,
                    "rps4_observed": pair.gpu4.request_throughput,
                    "ttft4_p95_ms_observed": pair.gpu4.p95_ttft_ms,
                    "rps4_predicted": pair.gpu2.request_throughput * math.exp(estimate[0]),
                    "ttft4_p95_ms_predicted": pair.gpu2.p95_ttft_ms * math.exp(estimate[1]),
                    "rps_log_response_true": truth[0], "ttft_log_response_true": truth[1],
                    "rps_log_response_pred": estimate[0], "ttft_log_response_pred": estimate[1],
                    "rps_abs_log_error": abs(estimate[0] - truth[0]),
                    "ttft_abs_log_error": abs(estimate[1] - truth[1]),
                    "output_token_ratio_4_over_2": token_ratio,
                    "output_within_5pct": matched,
                    "gpu2_source_path": pair.gpu2.source_path,
                    "gpu4_source_path": pair.gpu4.source_path,
                })
    if len(predictions) != 75:
        raise ValueError("Wrong prediction population")
    by_model = []
    for model in MODEL_SLUGS:
        for method in METHODS:
            subset = [p for p in predictions if p["heldout_model_id"] == model and
                      p["method"] == method]
            matching = [p for p in subset if p["output_within_5pct"]]
            by_model.append({
                "heldout_model_id": model, "method": method, "qps_count": len(subset),
                "rps_male": float(np.mean([p["rps_abs_log_error"] for p in subset])),
                "ttft_male": float(np.mean([p["ttft_abs_log_error"] for p in subset])),
                "matched_qps_count": len(matching),
                "matched_rps_male": (float(np.mean([p["rps_abs_log_error"] for p in matching]))
                                     if matching else None),
                "matched_ttft_male": (float(np.mean([p["ttft_abs_log_error"] for p in matching]))
                                      if matching else None),
                "output_ratio_min": min(p["output_token_ratio_4_over_2"] for p in subset),
                "output_ratio_max": max(p["output_token_ratio_4_over_2"] for p in subset),
            })
    macro = []
    for method in METHODS:
        rows = [r for r in by_model if r["method"] == method]
        complete_match = all(r["matched_qps_count"] > 0 for r in rows)
        macro.append({
            "method": method, "model_count": 3, "qps_per_model": 5,
            "rps_male": float(np.mean([r["rps_male"] for r in rows])),
            "ttft_male": float(np.mean([r["ttft_male"] for r in rows])),
            "matched_model_count": sum(r["matched_qps_count"] > 0 for r in rows),
            "matched_pair_count": sum(r["matched_qps_count"] for r in rows),
            "matched_rps_male": (float(np.mean([r["matched_rps_male"] for r in rows]))
                                   if complete_match else None),
            "matched_ttft_male": (float(np.mean([r["matched_ttft_male"] for r in rows]))
                                    if complete_match else None),
        })
    selected_rows = [{
        "model_id": c.model_id, "qps": c.qps, "gpu_count": c.gpu_count,
        "request_throughput": c.request_throughput, "p95_ttft_ms": c.p95_ttft_ms,
        "total_input_tokens": c.total_input_tokens, "total_output_tokens": c.total_output_tokens,
        "completed": c.completed, "date": c.date, "source_path": c.source_path,
        "source_line": c.source_line,
    } for c in selected]
    _write_csv(output / "selected_cells.csv", selected_rows, tuple(selected_rows[0]))
    _write_csv(output / "predictions.csv", predictions, tuple(predictions[0]))
    _write_csv(output / "per_model_metrics.csv", by_model, tuple(by_model[0]))
    report = {
        "schema_version": "iog_gpu24_response_score_v1",
        "interpretation": "retrospective_same_day_online_vllm_tensor_parallelism_not_vm_scale_action",
        "freeze_manifest_sha256": sha256_file(output / "freeze_manifest.json"),
        "protocol_sha256": frozen["protocol_sha256"],
        "script_sha256": frozen["script_sha256"],
        "source_aggregate_sha256": frozen["source_aggregate_sha256"],
        "source_manifest_sha256": frozen["source_manifest_sha256"],
        "raw_results_zip_sha256": frozen["raw_results_zip_sha256"],
        "sklearn_version": sklearn_version,
        "selected_cell_count": len(selected), "pair_count": len(pairs),
        "heldout_models": list(MODEL_SLUGS), "qps_values": list(QPS_VALUES),
        "methods": list(METHODS), "ridge_alpha": 1.0,
        "output_token_tolerance": "abs(log(output_tokens4/output_tokens2)) <= log(1.05)",
        "per_model": by_model, "macro": macro,
        "output_files_sha256": {
            name: sha256_file(output / name)
            for name in ("selected_cells.csv", "predictions.csv", "per_model_metrics.csv")
        },
    }
    (output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "score"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = freeze(args.output) if args.phase == "freeze" else score(args.output)
    print(json.dumps(result if args.phase == "freeze" else result["macro"], indent=2))


if __name__ == "__main__":
    main()
