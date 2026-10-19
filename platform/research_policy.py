"""Read-only original measurements -> bounded training -> shadow/replay service.

The policy implementation is imported from geon-khudata, not reimplemented.
Replay scores held-out recorded outcomes; it never invokes an app or cloud API.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys


def canonical_group(value):
    return value.removeprefix('ubuntu216:')


def select_groups(records, limit=24):
    groups = defaultdict(set)
    for record in records:
        groups[(record.get('dataset', ''), record['model_id'])].add(canonical_group(record['source_scenario_id']))
    return {group for values in groups.values() for group in sorted(values, key=lambda s: sha256(s.encode()).hexdigest())[:limit]}


def replay(policy, case):
    scored = []
    for row in case['rows']:
        scored.append({**{k: row[k] for k in ['candidate_id', 'action', 'desired_replicas', 'reward']},
                       'feasible': bool(row['feasible']), **policy.predict(row['features'])})
    scored.sort(key=lambda r: (-r['expected_utility'], r['candidate_id'], r['action'], r['desired_replicas']))
    evaluated = [r for r in scored if r['feasible']]
    top = evaluated[0] if evaluated else None
    guard = policy.manifest.get('guard', {})
    reason = 'no_feasible_candidate' if top is None else 'confidence_below_threshold' if top['confidence'] < guard.get('minimum_confidence', .7) else 'out_of_distribution' if top['ood_ratio'] > guard.get('maximum_ood_ratio', .2) else ''
    proposal = {**(top or {}), 'action': 'ABSTAIN' if reason else top['action'], 'abstain_reason': reason}
    viable = [r for r in case['rows'] if r['feasible']]
    oracle = sorted(viable, key=lambda r: (-r['reward'], r['candidate_id'], r['action']))[0] if viable else None
    agreement = None if reason else bool(oracle and (oracle['candidate_id'], oracle['action'], oracle['desired_replicas']) == (top['candidate_id'], top['action'], top['desired_replicas']))
    regret = round(max(0, oracle['reward'] - top['reward']), 8) if not reason and oracle else None
    baseline = scored[0] if scored else None
    baseline_agreement = bool(baseline['feasible'] and (baseline['candidate_id'], baseline['action'], baseline['desired_replicas']) == (oracle['candidate_id'], oracle['action'], oracle['desired_replicas'])) if baseline and oracle else None
    baseline_regret = round(max(0, oracle['reward'] - baseline['reward']), 8) if baseline and baseline['feasible'] and oracle else None
    comparison = {'schema_version': 'khu-policy-comparison/v1',
                  'baseline': {**(baseline or {'action': 'ABSTAIN'}), 'agreement': baseline_agreement,
                               'constraint_violation': not baseline['feasible'] if baseline else None, 'regret': baseline_regret},
                  'validated': {**proposal, 'agreement': agreement,
                                'constraint_violation': False if not reason else None, 'regret': regret}}
    return {**{k: v for k, v in case.items() if k != 'rows'}, 'source': 'khu-policy-experiment-20260911', 'execution_performed': False,
            'model_version': policy.manifest.get('model_version'), 'dataset_sha256': policy.manifest.get('dataset_sha256'), 'artifact_sha256': policy.manifest.get('artifact_sha256'),
            'proposal': proposal, 'oracle': {k: oracle[k] for k in ['candidate_id', 'action', 'desired_replicas', 'reward']} if oracle else None,
            'agreement': agreement, 'regret': regret, 'comparison': comparison,
            'candidate_evaluations': evaluated, 'cost_basis': 'original_cost_proxy_not_aws_bill'}


def build(source_root, destination):
    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    if source_root == destination or source_root in destination.parents:
        raise ValueError('Derived artifacts must be outside the original data folder')
    sys.path.insert(0, str(source_root / 'src'))
    from khu_policy_agent.learning import train_policy, load_rows, _group_split
    metadata, connections, origins = [], [], []
    try:
        for name in ['placement', 'observed-lifecycle']:
            path = source_root / 'runs' / (name + '.sqlite3')
            con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            con.row_factory = sqlite3.Row
            connections.append((name, con))
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Original SQLite integrity check failed')
            origins.append({'name': name, 'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest(), 'opened_read_only': True})
            metadata.extend({**dict(r), 'dataset': name} for r in con.execute("SELECT DISTINCT source_scenario_id, json_extract(application_json,'$.app_id') AS model_id FROM deployment_scenarios"))
        selected = select_groups(metadata)
        scenarios, rows = {}, []
        for name, con in connections:
            for scenario in con.execute('SELECT * FROM deployment_scenarios ORDER BY scenario_id'):
                group = canonical_group(scenario['source_scenario_id'])
                if group not in selected:
                    continue
                sid = name + ':' + scenario['scenario_id']
                s = dict(scenario)
                s['dataset'] = name
                s['source_scenario_id'] = group
                s['application'] = json.loads(s.pop('application_json'))
                s['operation'] = json.loads(s.pop('operation_json'))
                s['phase'] = 'planning' if s['lifecycle_state'] == 'NEW' else 'operation'
                scenarios[sid] = s
                for raw in con.execute('SELECT * FROM action_outcomes WHERE scenario_id=? ORDER BY outcome_id', (scenario['scenario_id'],)):
                    outcome = json.loads(raw['outcome_json'])
                    if outcome.get('outcome_source') != 'actual_huggingface_inference':
                        raise ValueError('Non-measured outcome in original research subset')
                    rows.append({**dict(raw), 'scenario_id': sid, 'source_scenario_id': group})
        destination.mkdir(parents=True, exist_ok=True)
        db = destination / 'training-subset.sqlite3'
        with sqlite3.connect(db) as out:
            out.execute('CREATE TABLE IF NOT EXISTS action_outcomes(scenario_id TEXT,source_scenario_id TEXT,candidate_id TEXT,action TEXT,feature_json TEXT,reward REAL,feasible INTEGER,slo_satisfied INTEGER,is_oracle INTEGER)')
            out.execute('DELETE FROM action_outcomes')
            for row in rows:
                out.execute('INSERT INTO action_outcomes VALUES(?,?,?,?,?,?,?,?,?)', tuple(row[k] for k in ['scenario_id','source_scenario_id','candidate_id','action','feature_json','reward','feasible','slo_satisfied','is_oracle']))
        train_policy(dataset_db=db, artifact_dir=destination, seed=42, trees=32)
        splits = _group_split(load_rows(db), 42)
        assignment = {r.source_scenario_id: split for split, values in splits.items() for r in values}
        case_groups = defaultdict(list)
        for row in rows:
            if assignment[row['source_scenario_id']] == 'train':
                continue
            case_groups[row['scenario_id']].append(row)
        # One reproducible held-out case per model/lifecycle state, without selecting on reward.
        chosen = {}
        for sid in sorted(case_groups, key=lambda s: sha256(s.encode()).hexdigest()):
            s = scenarios[sid]
            key = (s['application']['app_id'], s['lifecycle_state'], s['dataset'])
            chosen.setdefault(key, sid)
        cases = []
        for key, sid in sorted(chosen.items()):
            s, values = scenarios[sid], case_groups[sid]
            original = json.loads(values[0]['outcome_json'])
            cases.append({'case_id': 'case-' + sha256(sid.encode()).hexdigest()[:20], 'scenario_id': s['scenario_id'], 'source_scenario_id': s['source_scenario_id'], 'dataset': s['dataset'],
                          'phase': s['phase'], 'lifecycle_state': s['lifecycle_state'], 'model_id': key[0], 'model_revision': original['model_revision'],
                          'label': key[0].split('/')[-1] + ' · ' + s['lifecycle_state'] + ' · ' + original.get('profile_name', ''),
                          'split': assignment[s['source_scenario_id']], 'application': s['application'], 'operation': s['operation'],
                          'transition_evidence': 'paired_static_measurements' if s['lifecycle_state'] in ['SLO_PRESSURE','UNDERUTILIZED'] else 'direct_static_measurement',
                          'rows': [{**{k: row[k] for k in ['candidate_id','action','desired_replicas','reward','feasible','slo_satisfied','latency_p95_ms','throughput_rps','cost_per_hour']},
                                    'features': json.loads(row['feature_json']), 'measurement_source': json.loads(row['outcome_json'])} for row in values]})
        report = {'schema_version': 'khu-original-research/v1', 'source': 'khu-policy-experiment-20260911', 'origins': origins,
                  'policy_code_sha256': {name: sha256((source_root / 'src/khu_policy_agent' / name).read_bytes()).hexdigest() for name in ['learning.py','service.py','contracts.py']},
                  'source_root': str(source_root), 'python': sys.executable, 'trees': 32, 'seed': 42, 'subset_groups': len(selected), 'subset_outcomes': len(rows),
                  'split_groups': {split: sorted({r.source_scenario_id for r in values}) for split, values in splits.items()},
                  'cases': cases, 'cost_basis': 'original_cost_proxy_not_aws_bill', 'live_scaling_evidence': False}
        (destination / 'research-cases.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: report[k] for k in ['subset_groups','subset_outcomes','trees','source']}, ensure_ascii=False))
        print('Held-out cases:', len(cases))
    finally:
        for _, con in connections:
            con.close()


def serve(artifact_dir, port=18170):
    root = Path(artifact_dir).resolve()
    report = json.loads((root / 'research-cases.json').read_text(encoding='utf-8'))
    sys.path.insert(0, str(Path(report['source_root']) / 'src'))
    from khu_policy_agent.learning import load_policy
    from khu_policy_agent.service import evaluate_request
    for name, expected in report['policy_code_sha256'].items():
        if sha256((Path(report['source_root']) / 'src/khu_policy_agent' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Policy code changed; rebuild derived research artifacts')
    model = load_policy(root)
    model.model.n_jobs = 2
    cases = {c['case_id']: c for c in report['cases']}

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/healthz':
                return self.send(200, {'status':'ok','service':'khu-original-research-policy','mode':'shadow','artifact_sha256':model.manifest['artifact_sha256']})
            if self.path == '/api/v1/khu-policy/cases':
                return self.send(200, {'source':report['source'],'model_version':model.manifest['model_version'],'cases':[{k:v for k,v in c.items() if k not in ['rows','application','operation']} for c in cases.values()]})
            self.send(404, {'error':'not_found'})

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length',0))
                if not 0 < size <= 131072:
                    return self.send(400, {'error':'invalid_request'})
                value = json.loads(self.rfile.read(size))
                if self.path == '/api/v1/khu-policy/evaluate':
                    return self.send(200, evaluate_request(model, value))
                if self.path == '/api/v1/khu-policy/replay':
                    case = cases.get(value.get('case_id'))
                    if not case or case['phase'] != value.get('phase'):
                        return self.send(422, {'error':'research_case_unavailable'})
                    return self.send(200, replay(model, case))
                self.send(404, {'error':'not_found'})
            except (ValueError, TypeError):
                self.send(400, {'error':'invalid_request'})
            except Exception:
                self.send(503, {'error':'policy_unavailable'})

        def log_message(self, *_):
            pass

    print('Original KHU policy shadow service http://127.0.0.1:' + str(port), flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['build','serve'])
    parser.add_argument('--source-root')
    parser.add_argument('--artifact-dir', required=True)
    args = parser.parse_args()
    if args.action == 'build':
        if not args.source_root:
            parser.error('--source-root is required for build')
        build(args.source_root, args.artifact_dir)
    else:
        serve(args.artifact_dir)
