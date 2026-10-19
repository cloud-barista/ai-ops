"""Build a submit-time JOB dataset for first SCHEDULE later than five seconds.

Only the explicitly named, verified Google 2019 cell e or g collection-event
object is accepted by the CLI. The gzip is read without modifying it; a
temporary SQLite database handles the source's nonchronological row order.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path


INT64_MAX = 2**63 - 1
FIVE_SECONDS_US = 5_000_000
FIVE_MINUTES_US = 300_000_000
SOURCE_NAME = "collection_events-000000000000.json.gz"
SCHEMA_SHA256 = "63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3"
PINNED_SHA256 = {
    "e": "9b847a5e761f74743d2188a009b04242969b4d37544c4f7f96efa10330d597f5",
    "g": "ed7c850444c09d51112f7b15d67324700e01e9768b403e6a294941a241062c13",
}
CSV_COLUMNS = (
    "cell", "job_key", "submit_time_us", "scheduling_class", "priority",
    "scheduler", "has_alloc_collection", "has_parent_collection",
    "start_after_count", "late_schedule_5s",
)
MODEL_FEATURES = CSV_COLUMNS[3:-1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def optional_uint(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= INT64_MAX:
        raise ValueError("invalid unsigned integer")
    return value


def _features(row: dict) -> list[int | None]:
    scheduling_class = optional_uint(row.get("scheduling_class"))
    priority = optional_uint(row.get("priority"))
    scheduler = optional_uint(row.get("scheduler"))
    alloc_id = optional_uint(row.get("alloc_collection_id"))
    parent_id = optional_uint(row.get("parent_collection_id"))
    dependencies = row.get("start_after_collection_ids", [])
    if not isinstance(dependencies, list):
        raise ValueError("invalid dependency list")
    for dependency in dependencies:
        if optional_uint(dependency) is None:
            raise ValueError("null dependency ID")
    # -1 is an explicit missing category, distinct from the valid category 0.
    return [(-1 if scheduling_class is None else scheduling_class),
            (-1 if priority is None else priority),
            (-1 if scheduler is None else scheduler),
            int(alloc_id not in (None, 0)),
            int(parent_id not in (None, 0)), len(dependencies)]


def verify_official_input(input_path: Path, cell: str) -> dict:
    """Fail closed before opening gzip; never admit an f/h or unpinned source."""
    if cell not in PINNED_SHA256:
        raise ValueError("only cell e and cell g are allowed")
    path = input_path.resolve(strict=True)
    if path.parent.name != f"google_cluster_2019_{cell}" or path.name != SOURCE_NAME:
        raise ValueError("input must be the single official cell e/g collection-event object")
    manifest_path = path.parent / "verified_collection_events_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("objects")
    if manifest.get("bucket") != f"clusterdata_2019_{cell}" or not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("invalid verified source manifest")
    entry = entries[0]
    if entry.get("name") != SOURCE_NAME or entry.get("sha256") != PINNED_SHA256[cell]:
        raise ValueError("source object is not the pinned cell e/g object")
    schema_path = path.parent / "collection_events.schema.json"
    if sha256(schema_path) != SCHEMA_SHA256:
        raise ValueError("official collection-event schema hash differs")
    if path.stat().st_size != entry.get("bytes") or sha256(path) != PINNED_SHA256[cell]:
        raise ValueError("source gzip size or SHA-256 differs from verified manifest")
    return {
        "bucket": manifest["bucket"],
        "generation": entry.get("generation"),
        "object_name": entry["name"],
        "source_sha256": PINNED_SHA256[cell],
        "source_bytes": entry["bytes"],
        "verified_manifest_sha256": sha256(manifest_path),
        "schema_sha256": SCHEMA_SHA256,
    }


def build_dataset(input_path: Path, output_dir: Path, cell: str, *,
                  expected_sha256: str, provenance: dict | None = None) -> dict:
    """Build into a fresh directory. Tests may pass a synthetic pinned gzip."""
    if cell not in PINNED_SHA256:
        raise ValueError("only cell e and cell g are allowed")
    input_path = input_path.resolve(strict=True)
    if any(part in {"google_cluster_2019_f", "google_cluster_2019_h",
                    "clusterdata_2019_f", "clusterdata_2019_h"}
           for part in input_path.parts):
        raise ValueError("cell f/h inputs are forbidden")
    if input_path.suffix != ".gz":
        raise ValueError("input must be a gzip file")
    source_hash = sha256(input_path)
    if source_hash != expected_sha256:
        raise ValueError("input SHA-256 differs from expected value")
    output_dir = output_dir.resolve()
    if output_dir == input_path.parent or input_path.parent in output_dir.parents:
        raise ValueError("output must be a new directory outside the source")
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix=".job_schedule_scratch_", dir=output_dir) as temp:
            metadata = _extract(input_path, output_dir / "jobs.csv", Path(temp) / "events.sqlite3", cell)
        metadata["source"] = {
            "input_path": str(input_path), "sha256": source_hash,
            "bytes": input_path.stat().st_size, **(provenance or {}),
        }
        metadata["builder_sha256"] = sha256(Path(__file__))
        metadata["csv_sha256"] = sha256(output_dir / "jobs.csv")
        (output_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return metadata
    except BaseException:
        shutil.rmtree(output_dir)
        raise


def _extract(input_path: Path, csv_path: Path, db_path: Path, cell: str) -> dict:
    counts: Counter[str] = Counter()
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("CREATE TABLE submits (id INTEGER PRIMARY KEY, time_us INTEGER NOT NULL, "
                    "second_time_us INTEGER, features TEXT NOT NULL)")
        con.execute("CREATE TABLE schedules (id INTEGER PRIMARY KEY, time_us INTEGER NOT NULL, "
                    "first_n INTEGER NOT NULL, n INTEGER NOT NULL)")
        con.execute("CREATE TABLE terminals (id INTEGER PRIMARY KEY, time_us INTEGER NOT NULL)")
        con.execute("CREATE TABLE bad_events (id INTEGER NOT NULL, time_us INTEGER NOT NULL)")
        con.execute("CREATE INDEX bad_events_id_time ON bad_events(id,time_us)")
        con.execute("CREATE TABLE pretrace_jobs (id INTEGER PRIMARY KEY)")
        submit_sql = ("INSERT INTO submits VALUES (?, ?, NULL, ?) ON CONFLICT(id) DO UPDATE SET "
                      "second_time_us=CASE WHEN excluded.time_us<=submits.time_us "
                      "THEN submits.time_us WHEN submits.second_time_us IS NULL "
                      "OR excluded.time_us<submits.second_time_us THEN excluded.time_us "
                      "ELSE submits.second_time_us END, "
                      "features=CASE WHEN excluded.time_us<submits.time_us "
                      "THEN excluded.features ELSE submits.features END, "
                      "time_us=MIN(submits.time_us, excluded.time_us)")
        schedule_sql = ("INSERT INTO schedules VALUES (?, ?, 1, 1) ON CONFLICT(id) DO UPDATE SET "
                        "first_n=CASE WHEN excluded.time_us<schedules.time_us THEN 1 "
                        "WHEN excluded.time_us=schedules.time_us THEN schedules.first_n+1 "
                        "ELSE schedules.first_n END, "
                        "time_us=MIN(schedules.time_us, excluded.time_us), n=schedules.n+1")
        batches: dict[str, list[tuple]] = {"submit": [], "schedule": [], "terminal": [],
                                           "bad_event": [], "pretrace": []}

        def flush() -> None:
            con.executemany(submit_sql, batches["submit"])
            con.executemany(schedule_sql, batches["schedule"])
            con.executemany(
                "INSERT INTO terminals VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET "
                "time_us=MIN(terminals.time_us, excluded.time_us)", batches["terminal"]
            )
            con.executemany("INSERT INTO bad_events VALUES (?, ?)", batches["bad_event"])
            con.executemany("INSERT OR IGNORE INTO pretrace_jobs VALUES (?)", batches["pretrace"])
            con.commit()
            for batch in batches.values():
                batch.clear()

        min_time: int | None = None
        max_time: int | None = None
        with gzip.open(input_path, "rt", encoding="utf-8", newline="") as stream:
            for line_no, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at line {line_no}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"non-object JSON at line {line_no}")
                counts["source_rows"] += 1
                try:
                    parsed_time = optional_uint(row.get("time"))
                except ValueError:
                    parsed_time = None
                timestamp = (parsed_time if parsed_time not in (None, 0, INT64_MAX)
                             else None)
                if timestamp is None:
                    counts["excluded_invalid_or_sentinel_time_rows"] += 1
                    if parsed_time == 0:
                        counts["pretrace_time_rows"] += 1
                    elif parsed_time == INT64_MAX:
                        counts["ignored_posttrace_time_rows"] += 1
                    else:
                        counts["missing_or_invalid_time_rows"] += 1
                else:
                    min_time = timestamp if min_time is None else min(min_time, timestamp)
                    max_time = timestamp if max_time is None else max(max_time, timestamp)
                try:
                    collection_id = optional_uint(row.get("collection_id"))
                except ValueError:
                    collection_id = None
                if collection_id is None:
                    counts["excluded_invalid_collection_id_rows"] += 1
                    continue
                try:
                    collection_type = optional_uint(row.get("collection_type"))
                    event_type = optional_uint(row.get("type"))
                except ValueError:
                    collection_type = event_type = None
                if collection_type is None or event_type is None:
                    counts["excluded_missing_or_invalid_type_rows"] += 1
                    if timestamp is not None:
                        batches["bad_event"].append((collection_id, timestamp))
                    elif parsed_time == 0:
                        batches["pretrace"].append((collection_id,))
                    continue
                if collection_type != 0:
                    counts["non_job_rows"] += 1
                    continue
                # v3 MissingEventType=0 means no missing event; positive values
                # denote an inferred/missing record and make this job ambiguous.
                missing_type = row.get("missing_type")
                try:
                    missing_type = optional_uint(missing_type)
                except ValueError:
                    missing_type = -1
                if missing_type not in (None, 0):
                    counts["excluded_missing_type_job_rows"] += 1
                    if timestamp is not None:
                        batches["bad_event"].append((collection_id, timestamp))
                    elif parsed_time == 0:
                        batches["pretrace"].append((collection_id,))
                    continue
                if timestamp is None:
                    if parsed_time == 0:
                        batches["pretrace"].append((collection_id,))
                    continue
                if event_type == 0:
                    counts["valid_job_submit_rows"] += 1
                    try:
                        features = _features(row)
                    except ValueError:
                        counts["invalid_submit_feature_rows"] += 1
                        features = []
                    batches["submit"].append((collection_id, timestamp, json.dumps(features)))
                elif event_type == 3:
                    counts["valid_job_schedule_rows"] += 1
                    batches["schedule"].append((collection_id, timestamp))
                elif event_type in (5, 6, 7, 8):
                    counts["valid_job_terminal_rows"] += 1
                    batches["terminal"].append((collection_id, timestamp))
                elif event_type == 1:
                    counts["valid_job_queue_rows"] += 1
                elif event_type == 2:
                    counts["valid_job_enable_rows"] += 1
                if sum(map(len, batches.values())) >= 10_000:
                    flush()
        flush()
        if min_time is None or max_time is None:
            raise ValueError("source has no valid timed events")
        counts["unique_valid_submit_ids"] = con.execute("SELECT COUNT(*) FROM submits").fetchone()[0]
        counts["orphan_schedule_ids"] = con.execute(
            "SELECT COUNT(*) FROM schedules s LEFT JOIN submits u ON s.id=u.id WHERE u.id IS NULL"
        ).fetchone()[0]
        with csv_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(CSV_COLUMNS)
            query = ("SELECT u.id,u.time_us,u.second_time_us,u.features,s.time_us,s.first_n,"
                     "t.time_us,p.id,"
                     "EXISTS(SELECT 1 FROM bad_events b WHERE b.id=u.id AND "
                     "b.time_us BETWEEN u.time_us AND u.time_us+5000000),"
                     "EXISTS(SELECT 1 FROM bad_events b WHERE b.id=u.id AND "
                     "b.time_us>u.time_us+5000000) "
                     "FROM submits u LEFT JOIN schedules s ON s.id=u.id "
                     "LEFT JOIN terminals t ON t.id=u.id "
                     "LEFT JOIN pretrace_jobs p ON p.id=u.id ORDER BY u.time_us,u.id")
            for (job_id, submit_time, second_submit, features_json, schedule_time,
                 first_n, terminal_time, pretrace_id, bad_in_window,
                 bad_after_window) in con.execute(query):
                end_time = submit_time + FIVE_SECONDS_US
                if second_submit is not None and second_submit <= end_time:
                    counts["excluded_duplicate_submit_within_5s_ids"] += 1
                    continue
                if pretrace_id is not None:
                    counts["excluded_pretrace_job_ids"] += 1
                    continue
                if bad_in_window:
                    counts["excluded_bad_event_within_5s_ids"] += 1
                    continue
                if first_n is not None and first_n > 1 and schedule_time <= end_time:
                    counts["excluded_duplicate_first_schedule_within_5s_ids"] += 1
                    continue
                if schedule_time is not None and schedule_time < submit_time:
                    counts["excluded_reversed_schedule_ids"] += 1
                    continue
                if terminal_time is not None and terminal_time < submit_time:
                    counts["excluded_reversed_terminal_ids"] += 1
                    continue
                if (schedule_time is not None and schedule_time == terminal_time
                        and schedule_time <= end_time):
                    counts["excluded_schedule_terminal_within_5s_collision_ids"] += 1
                    continue
                if max_time - submit_time < FIVE_SECONDS_US:
                    counts["excluded_incomplete_5s_ids"] += 1
                    continue
                features = json.loads(features_json)
                if len(features) != len(MODEL_FEATURES):
                    counts["excluded_invalid_first_submit_feature_ids"] += 1
                    continue
                if max_time - submit_time < FIVE_MINUTES_US:
                    counts["would_lack_complete_5m_observation_ids"] += 1
                if second_submit is not None and second_submit > end_time:
                    counts["ignored_later_duplicate_submit_ids"] += 1
                if first_n is not None and first_n > 1 and schedule_time > end_time:
                    counts["ignored_later_duplicate_first_schedule_ids"] += 1
                if schedule_time is not None and schedule_time == terminal_time and schedule_time > end_time:
                    counts["ignored_later_schedule_terminal_collision_ids"] += 1
                if bad_after_window:
                    counts["ignored_later_bad_event_ids"] += 1
                delay = None if schedule_time is None else schedule_time - submit_time
                competing_terminal = (terminal_time is not None
                                      and terminal_time <= submit_time + FIVE_SECONDS_US
                                      and (schedule_time is None or terminal_time < schedule_time))
                label = int(competing_terminal or delay is None or delay > FIVE_SECONDS_US)
                job_key = hashlib.sha256(f"{cell}:{job_id}".encode()).hexdigest()
                writer.writerow((cell, job_key, submit_time, *features, label))
                counts["labeled_jobs"] += 1
                counts[f"label_{label}_jobs"] += 1
                if label == 1:
                    if competing_terminal:
                        counts["label_1_competing_terminal_within_5s"] += 1
                    elif delay is None:
                        counts["label_1_no_schedule_observed_by_trace_end"] += 1
                    else:
                        counts["label_1_later_schedule_observed"] += 1
                for name, value in zip(MODEL_FEATURES[:3], features[:3], strict=True):
                    if value == -1:
                        counts[f"labeled_missing_{name}"] += 1
        return {
            "version": 1,
            "cell": cell,
            "target": "first_JOB_SCHEDULE_later_than_5s_after_SUBMIT",
            "label_definition": {
                "0": "first valid SCHEDULE occurs from SUBMIT through 5 seconds inclusive",
                "1": "no first SCHEDULE by 5 seconds, including a competing earlier terminal event",
                "queue_enable": "QUEUE and ENABLE are audited events, not SCHEDULE or model inputs",
                "censoring": "all SUBMITs require 5 seconds of subsequent trace coverage; 5-minute coverage is audited separately",
                "terminal": "FAIL, FINISH, KILL, or LOST before first SCHEDULE within 5 seconds is competing non-start; pre-SUBMIT terminals and same-time collisions within 5 seconds are excluded",
                "post_window": "missing, duplicate, and colliding events later than 5 seconds are counted but cannot remove or relabel an already observed request",
            },
            "submit_only_model_features": list(MODEL_FEATURES),
            "missing_category_encoding": {"scheduling_class": -1, "priority": -1,
                                          "scheduler": -1},
            "split_only_columns": ["cell", "job_key", "submit_time_us"],
            "time_min_us": min_time,
            "time_max_us": max_time,
            "audit": dict(sorted(counts.items())),
        }
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", choices=tuple(PINNED_SHA256), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    provenance = verify_official_input(args.input, args.cell)
    result = build_dataset(args.input, args.output_dir, args.cell,
                           expected_sha256=PINNED_SHA256[args.cell], provenance=provenance)
    print(json.dumps({"jobs_csv": str(args.output_dir / "jobs.csv"),
                      "metadata": str(args.output_dir / "metadata.json"),
                      "audit": result["audit"]}, sort_keys=True))


if __name__ == "__main__":
    main()
