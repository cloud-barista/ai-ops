"""Count JOB SUBMIT and UPDATE_PENDING events in verified 2019 e/g traces.

The output contains aggregate counts only. It does not persist job or user IDs.
UPDATE_PENDING is a Borg scheduler event, not an AI-App update action label.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAY_US = 86_400_000_000
SOURCES = {
    'e': ('google_cluster_2019_e',
          '9b847a5e761f74743d2188a009b04242969b4d37544c4f7f96efa10330d597f5'),
    'g': ('google_cluster_2019_g',
          'ed7c850444c09d51112f7b15d67324700e01e9768b403e6a294941a241062c13'),
}


def count_cell(cell: str) -> dict:
    directory, expected_sha = SOURCES[cell]
    path = (ROOT / 'data/external' / directory /
            'collection_events-000000000000.json.gz')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected_sha:
        raise ValueError(f'{cell}_source_sha256_mismatch')

    submit_ids: set[str] = set()
    update_ids: set[str] = set()
    update_days: Counter[int] = Counter()
    rows = submits = updates = 0
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            rows += 1
            event = json.loads(line)
            if event.get('collection_type') != '0':
                continue
            event_type = event.get('type')
            if event_type == '0':
                submits += 1
                submit_ids.add(event['collection_id'])
            elif event_type == '9':
                updates += 1
                update_ids.add(event['collection_id'])
                timestamp = int(event['time'])
                if timestamp >= 0:
                    update_days[timestamp // DAY_US] += 1

    return {
        'cell': cell,
        'source_sha256': expected_sha,
        'source_rows': rows,
        'job_submit_event_rows': submits,
        'job_submit_unique_ids': len(submit_ids),
        'job_update_pending_event_rows': updates,
        'job_update_pending_unique_ids': len(update_ids),
        'updated_ids_with_submit': len(update_ids & submit_ids),
        'update_pending_rows_by_day': {
            str(day): count for day, count in sorted(update_days.items())},
    }


if __name__ == '__main__':
    print(json.dumps({cell: count_cell(cell) for cell in SOURCES},
                     indent=2, sort_keys=True))
