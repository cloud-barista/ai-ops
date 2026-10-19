"""Export a read-only CPU baseline advisory and public AWS price snapshot.

Reads verified Windows backups. Does not contact CB-Tumblebug or create VMs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import urllib.request

PRICE_URL = ('https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/'
             'ec2-ondemand-without-sec-sel/US%20West%20(Oregon)/Linux/index.json')
BASELINES = {
    'khu-m6a-2xl-cpu-baseline-v10-20260922': ('aws+us-west-2+m6a.2xlarge', 8),
    'khu-m6a-2xl-cpu-baseline-v10b-20260922': ('aws+us-west-2+m6a.2xlarge', 8),
    'khu-m6a-2xl-cpu-baseline-v10c-20260922': ('aws+us-west-2+m6a.2xlarge', 8),
    'khu-m6a-large-cpu-baseline-v11-20260922': ('aws+us-west-2+m6a.large', 2),
    'khu-m6a-large-cpu-baseline-v11b-20260922': ('aws+us-west-2+m6a.large', 2),
    'khu-m6a-large-cpu-baseline-v11c-20260922': ('aws+us-west-2+m6a.large', 2),
}
WORKLOAD_FIELDS = (
    'profile_name', 'model_id', 'model_revision', 'batch_size', 'max_length',
    'request_concurrency', 'replica_count', 'load_condition', 'target_throughput_rps',
    'latency_slo_ms', 'dataset', 'repeats',
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build_snapshot(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    sources = {row['run_id']: row for row in report['source_files'] if row['run_id'] in BASELINES}
    if set(sources) != set(BASELINES):
        raise ValueError('All six verified CPU baseline sources are required')
    groups = defaultdict(lambda: defaultdict(list))
    evidence = []
    hosts = set()
    measurement_count = 0
    for run_id, (spec, cores) in BASELINES.items():
        source = sources[run_id]
        db = Path(source['database']).resolve()
        provenance_path = db.with_name('provenance.json')
        if sha256(db) != source['database_sha256'] or sha256(provenance_path) != source['provenance_sha256']:
            raise ValueError(f'Baseline source hash mismatch: {run_id}')
        provenance = json.loads(provenance_path.read_text(encoding='utf-8-sig'))
        config = provenance['configuration']
        if provenance['collection_status'] != 'complete':
            raise ValueError(f'Collection is not complete: {run_id}')
        if config['KHU_TB_SPEC_ID'] != spec or config['KHU_RUN_ID'] != run_id:
            raise ValueError(f'Provenance configuration mismatch: {run_id}')
        host = provenance['host']
        if not host or host in hosts:
            raise ValueError('Independent host identities are required')
        hosts.add(host)
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError(f'SQLite integrity failure: {run_id}')
            runs = [tuple(row) for row in con.execute('SELECT run_id,status FROM collection_runs')]
            if runs != [(run_id, 'complete')]:
                raise ValueError(f'Collection must be complete: {run_id}')
            rows = [dict(row) for row in con.execute('SELECT * FROM execution_measurements')]
        if not rows:
            raise ValueError('Baseline has no measurements')
        for row in rows:
            if row['run_id'] != run_id or row['success'] != 1 or row['device'] != 'cpu' or \
                    row['cpu_core_limit'] != cores or row['cpu_threads'] != cores or \
                    len(set(json.loads(row['cpu_affinity_json']))) != cores:
                raise ValueError(f'Invalid baseline measurement: {run_id}')
            for metric in ('examples_per_second', 'latency_p95_ms'):
                if not math.isfinite(row[metric]) or row[metric] <= 0:
                    raise ValueError('Invalid performance measurement')
            result = json.loads(row['result_json'])
            controls = {
                'seed': result['seed'],
                'parameter_bytes_per_parameter': result['model_parameter_bytes'] / result['model_parameter_count'],
                'sample_interval_seconds': float(config['KHU_SAMPLE_INTERVAL_SECONDS']),
                'image_id': config['KHU_TB_IMAGE_ID'], 'python_version': provenance['python'],
            }
            workload = {field: row[field] for field in WORKLOAD_FIELDS}
            workload.update(controls)
            signature = json.dumps(workload, sort_keys=True, separators=(',', ':'))
            groups[signature][spec].append({
                'run_id': run_id, 'throughput': row['examples_per_second'], 'p95_ms': row['latency_p95_ms'],
            })
            measurement_count += 1
        if sha256(db) != source['database_sha256'] or sha256(provenance_path) != source['provenance_sha256']:
            raise ValueError('Baseline source hash changed during export')
        evidence.append({
            'run_id': run_id, 'spec_id': spec, 'measurement_count': len(rows),
            'database_sha256': source['database_sha256'], 'provenance_sha256': source['provenance_sha256'],
            'collection_complete': True, 'sqlite_integrity': 'ok',
        })
    cohorts = []
    for signature, specs in groups.items():
        if len(specs) != 2:
            continue
        options = []
        for spec, measurements in sorted(specs.items()):
            by_run = defaultdict(list)
            for row in measurements:
                by_run[row['run_id']].append(row)
            if len(by_run) != 3:
                break
            throughputs = [statistics.median(row['throughput'] for row in rows) for rows in by_run.values()]
            p95s = [statistics.median(row['p95_ms'] for row in rows) for rows in by_run.values()]
            cores = next(cores for expected, cores in BASELINES.values() if expected == spec)
            options.append({
                'spec_id': spec, 'device': 'cpu', 'cpu_core_limit': cores, 'cpu_threads': cores,
                'independent_run_count': len(by_run), 'run_ids': sorted(by_run),
                'median_throughput': statistics.median(throughputs),
                'throughput_min': min(throughputs), 'throughput_max': max(throughputs),
                'median_p95_ms': statistics.median(p95s), 'p95_min_ms': min(p95s), 'p95_max_ms': max(p95s),
            })
        if len(options) == 2:
            cohorts.append({**json.loads(signature), 'cohort_id': hashlib.sha256(signature.encode()).hexdigest()[:16],
                            'options': options})
    if not cohorts:
        raise ValueError('No matched workloads with three independent runs per CPU option')
    cohorts.sort(key=lambda row: (not row['profile_name'].startswith('bert-tiny'), row['profile_name']))
    return {
        'schema_version': 'khu-cpu-policy/v1', 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'historical_cpu_baseline_advisory', 'verified_source_run_count': len(evidence),
        'measurement_count': measurement_count, 'source_report_sha256': sha256(report_path),
        'sources': evidence, 'cohorts': cohorts,
        'limitations': [
            'Observed ranges from three VM sessions are not statistical guarantees or confidence intervals.',
            'Batch inference latency and closed-loop throughput do not establish service SLOs.',
            'Fixed classification inputs are repeated; request count is not unique input count.',
            'Compute estimates exclude startup, idle time, queueing, storage, transfer and taxes.',
            'GPU and CPU32 recommendation, current VM availability and automatic deployment are not enabled.',
            'Full package freeze and independent held-out service validation remain unavailable.',
        ],
        'service_slo_verified': False, 'cloud_execution_enabled': False,
    }


def fetch_prices() -> dict:
    with urllib.request.urlopen(PRICE_URL, timeout=30) as response:
        body = response.read(8 * 1024 * 1024)
    if body.startswith(b'\x1f\x8b'):
        body = gzip.decompress(body)
    data = json.loads(body)
    if data['manifest']['currencyCode'] != 'USD' or \
            data['manifest']['source'] != 'ec2-ondemand-without-sec-sel':
        raise ValueError('Unexpected AWS pricing source')
    rates = {}
    for record in data['regions']['US West (Oregon)'].values():
        spec = 'aws+us-west-2+' + record.get('Instance Type', '')
        if spec not in {expected for expected, _ in BASELINES.values()}:
            continue
        if record.get('Operating System') != 'Linux' or record.get('Pre Installed S/W') != 'NA':
            raise ValueError('Unexpected price operating system or license')
        rate = float(record['price'])
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('Invalid AWS price')
        rates[spec] = rate
    if set(rates) != {expected for expected, _ in BASELINES.values()}:
        raise ValueError('Missing AWS CPU prices')
    checked = datetime.now(timezone.utc)
    return {
        'region': 'us-west-2', 'operating_system': 'Linux', 'purchase_option': 'OnDemand', 'currency': 'USD',
        'checked_at_utc': checked.isoformat(), 'valid_until_utc': (checked + timedelta(hours=24)).isoformat(),
        'published_at_utc': data['manifest']['hawkFilePublicationDate'], 'source_url': PRICE_URL,
        'hourly_usd': rates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('data') / 'khu-cpu-policy.json')
    args = parser.parse_args()
    snapshot = build_snapshot(args.analysis_report)
    snapshot['price_snapshot'] = fetch_prices()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'source_runs': snapshot['verified_source_run_count'],
                      'measurements': snapshot['measurement_count'], 'workloads': len(snapshot['cohorts']),
                      'hourly_usd': snapshot['price_snapshot']['hourly_usd'],
                      'cloud_vm_created': False, 'source_data_modified': False}))


if __name__ == '__main__':
    main()
