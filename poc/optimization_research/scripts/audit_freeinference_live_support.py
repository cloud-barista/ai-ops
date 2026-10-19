#!/usr/bin/env python3
"""Audit a pinned public FreeInference telemetry snapshot for KHU labels."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/external/freeinference_live_2026_09_29'
OUTPUT = ROOT / 'artifacts/freeinference_live_support_v1/audit.json'
SHA256 = {
    'dashboard.json': '72bf30e02401bcac6fb63ac3e34758198e36c8e34d1cf35dc3ceddadcd6adb50',
    'history_2h.json': '94dbd79d239719426003ca83cae0c793a7e76562fbf9c985eb3e2a4555f366b6',
    'history_24h.json': '775029f29708032bf3bdd9890384079ebd9993222e3bb03b9edc7d6c1499723e',
    'history_168h.json': '9019eb970675031ad0c1fb41dd0607d188893cbd08d82b410ce571bff8616ac4',
}
FORBIDDEN_ACTION_FIELDS = {'action_id', 'decision_id', 'deployment_id',
                           'desired_replicas', 'current_replicas',
                           'issued_at', 'ready_at'}


def utc_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def read_pinned(name: str) -> dict:
    content = (DATA / name).read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if actual != SHA256[name]:
        raise ValueError(name + ':sha256_mismatch:' + actual)
    return json.loads(content)


def nested_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value))
    return set()


def summarize_history(name: str, payload: dict) -> dict:
    if nested_keys(payload) & FORBIDDEN_ACTION_FIELDS:
        raise ValueError(name + ':unexpected_action_fields')
    summary = {'grain_minutes': payload['grain_ms'] / 60000,
               'generated_at_utc': utc_ms(payload['generated_at'])}
    for kind, key in (('sglang', 'server_id'), ('gpu', 'gpu_id'), ('cpu', 'node_id')):
        rows = payload[kind]
        pairs = [(row[key], row['ts']) for row in rows]
        if len(pairs) != len(set(pairs)):
            raise ValueError(name + ':duplicate_' + kind + '_id_ts')
        summary[kind] = {
            'rows': len(rows), 'identities': len({row[key] for row in rows}),
            'timestamps': len({row['ts'] for row in rows}),
            'min_at_utc': utc_ms(min(row['ts'] for row in rows)),
            'max_at_utc': utc_ms(max(row['ts'] for row in rows)),
        }
        if kind == 'sglang':
            for column in ('queued', 'running', 'req_per_min', 'ttft_avg_s',
                           'tpot_avg_s', 'e2e_latency_avg_s', 'cache_hit_rate',
                           'http_error_per_min'):
                values = [row.get(column) for row in rows]
                summary[kind][column] = {
                    'observed': sum(value is not None for value in values),
                    'positive': sum(value is not None and value > 0
                                    for value in values),
                }
    return summary


def run(output: Path = OUTPUT) -> dict:
    dashboard = read_pinned('dashboard.json')
    if dashboard['demo'] is not False:
        raise ValueError('dashboard_demo_mode')
    if nested_keys(dashboard) & FORBIDDEN_ACTION_FIELDS:
        raise ValueError('dashboard_action_fields_unexpected')
    history = {name: summarize_history(name, read_pinned(name))
               for name in ('history_2h.json', 'history_24h.json',
                            'history_168h.json')}
    ids = {str(row['id']) for row in dashboard['sglang']}
    requests = dashboard['requests']
    request_ids = {str(row['server_id']) for row in requests}
    if not request_ids <= ids:
        raise ValueError('dashboard_request_server_unmatched')
    result = {
        'source': 'https://data.freeinference.org/ (public /api/dashboard and /api/history)',
        'snapshot_sha256': SHA256,
        'dashboard': {
            'generated_at_utc': utc_ms(dashboard['generated_at']),
            'demo': dashboard['demo'],
            'nodes': len(dashboard['nodes']),
            'gpus': len(dashboard['gpus']),
            'servers': len(dashboard['sglang']),
            'recent_requests': len(requests),
            'request_server_ids': len(request_ids),
            'requests_with_ttft': sum(row.get('ttft_s') is not None for row in requests),
        },
        'history': history,
        'action_fields_present': False,
        'decision': 'no_go_scale_action_or_user_slo_policy_training',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
