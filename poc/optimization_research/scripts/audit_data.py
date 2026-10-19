"""Read-only audit and frozen row extraction; never changes source SQLite DBs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ['latency_p95_ms', 'examples_per_second']
# Excludes resource choice and campaign names: all resource alternatives stay together.
GROUP_COLS = ['model_id', 'model_revision', 'batch_size', 'max_length',
              'request_concurrency', 'dataset']
CONDITION_COLS = GROUP_COLS + ['host', 'device', 'cpu_core_limit', 'cpu_threads',
                              'replica_count', 'repeats']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def group_ids(frame, columns=GROUP_COLS):
    return frame[columns].apply(lambda row: digest(row.tolist()), axis=1)


def eligible_mask(frame):
    numeric = frame[TARGETS].apply(pd.to_numeric, errors='coerce')
    return ((frame.success == 1) & (frame.run_status == 'complete')
            & np.isfinite(numeric).all(axis=1) & (numeric > 0).all(axis=1))


def extract(source_root, output):
    output.mkdir(parents=True, exist_ok=True)
    frames, inventories = [], []
    for path in sorted(source_root.glob('khu-hf-*/executions.sqlite3')):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10) as conn:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            frame = pd.read_sql_query('''SELECT e.*, r.host, r.status AS run_status,
                r.collector_version, r.schema_version AS collection_schema
                FROM execution_measurements e LEFT JOIN collection_runs r USING(run_id)
                ORDER BY e.execution_id''', conn)
            phases = dict(conn.execute('SELECT phase,count(*) FROM node_samples GROUP BY phase'))
            before_ids = {r[0] for r in conn.execute(
                "SELECT DISTINCT execution_id FROM node_samples WHERE phase='before'")}
            run_counts = dict(conn.execute('SELECT status,count(*) FROM collection_runs GROUP BY status'))
        frame['source_db'] = str(path)
        frame['campaign'] = path.parent.name
        frame['has_before_sample'] = frame.execution_id.isin(before_ids)
        payloads = [json.loads(v or '{}') for v in frame.result_json]
        frame['workload_task'] = [v.get('workload_task', 'missing') for v in payloads]
        frame['input_encoding'] = [v.get('input_encoding', 'unspecified') for v in payloads]
        frame['device_name'] = [
            (v.get('replica_results') or [v])[0].get('device_name', 'unknown') for v in payloads]
        mismatches = Counter()
        for (_, row), result in zip(frame.iterrows(), payloads):
            for key in ['batch_size', 'max_length', 'request_concurrency', 'cpu_threads']:
                if key in result and result[key] != row[key]:
                    mismatches[key] += 1
        frame['workload_group'] = group_ids(frame)
        frame['condition_group'] = group_ids(frame, CONDITION_COLS)
        errors = Counter(json.loads(v or '{}').get('type', 'unknown')
                         for v in frame.loc[frame.success != 1, 'error_json'])
        inventories.append({
            'path': str(path), 'rows': len(frame), 'successful': int(frame.success.sum()),
            'eligible_rows': int(eligible_mask(frame).sum()),
            'runs_by_status': run_counts, 'rows_by_status': frame.run_status.value_counts().to_dict(),
            'error_types': dict(errors), 'payload_mismatches': dict(mismatches),
            'missing_before': int((~frame.has_before_sample).sum()),
            'node_samples_by_phase': phases,
            'model_count': int(frame.model_id.nunique()),
            'hosts': sorted(frame.host.dropna().unique().tolist()),
            'devices': frame.device_name.value_counts().to_dict(),
            'tasks': frame.workload_task.value_counts().to_dict(),
            'conditions': int(frame.condition_group.nunique()),
            'null_targets': frame[TARGETS].isna().sum().to_dict(),
            'replicas': sorted(frame.replica_count.unique().tolist()),
        })
        frames.append(frame)
    if not frames:
        raise ValueError('No KHU measurement databases found')
    data = pd.concat(frames, ignore_index=True)
    identities = ['host', 'execution_id']
    duplicated = data.duplicated(identities, keep=False)
    conflicts = 0
    for _, group in data[duplicated].groupby(identities):
        if group[['result_json', 'error_json', 'started_at', 'success']].drop_duplicates().shape[0] > 1:
            conflicts += 1
    if conflicts:
        raise ValueError(f'{conflicts} conflicting execution identities; resolve before training')
    data = data.drop_duplicates(identities).copy()
    data['eligible'] = eligible_mask(data)
    eligible = data[data.eligible]
    overlap = {}
    for a in inventories:
        name = Path(a['path']).parent.name
        groups = set(eligible.loc[eligible.campaign == name, 'workload_group'])
        overlap[name] = {Path(b['path']).parent.name: len(groups & set(eligible.loc[
            eligible.campaign == Path(b['path']).parent.name, 'workload_group'])) for b in inventories}
    data.to_parquet(output / 'measurements.parquet', index=False)
    frozen_hash = hashlib.sha256((output / 'measurements.parquet').read_bytes()).hexdigest()
    audit = {'created_at': datetime.now(timezone.utc).isoformat(),
             'snapshot_sha256': frozen_hash, 'inventories': inventories,
             'total_rows_before_deduplication': sum(i['rows'] for i in inventories),
             'duplicate_identity_rows_removed': sum(i['rows'] for i in inventories)-len(data),
             'eligible_rows': len(eligible), 'workload_groups': int(eligible.workload_group.nunique()),
             'conditions': int(eligible.condition_group.nunique()),
             'cross_campaign_workload_group_overlap': overlap,
             'group_columns': GROUP_COLS, 'condition_columns': CONDITION_COLS}
    (output / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=int)+'\n')
    lines = ['# SQLite 데이터 진단', '', f'- 추출 시각: {audit["created_at"]}',
             f'- 원본 행 수: {audit["total_rows_before_deduplication"]:,}',
             f'- 동일 실행 ID 중복 제거: {audit["duplicate_identity_rows_removed"]:,}',
             f'- 성공·완료 회차·유효 타깃을 만족하는 행: {len(eligible):,}',
             f'- 응용/입력 조건 그룹: {audit["workload_groups"]:,}', '',
             '| DB | 전체 | 학습 후보 | 모델 종류 | 누락 before |',
             '| --- | ---: | ---: | ---: | ---: |']
    for inv in inventories:
        lines.append(f'| {Path(inv["path"]).parent.name} | {inv["rows"]} | '
                     f'{inv["eligible_rows"]} | {inv["model_count"]} | {inv["missing_before"]} |')
    lines += ['', '## 해석', '',
        '- 원본은 mode=ro + query_only + 읽기 트랜잭션으로 추출했다. DB별 일관된 뷰이며 DB 간 동시 시점은 보장하지 않는다.',
        '- 추출 행을 Parquet로 고정하고 해시를 기록했다. node_samples 전체는 복사하지 않았고 phase 건수와 before 존재 여부만 진단했다.',
        '- latency_p95_ms는 배치 단위 지연이다. 다중 replica에서는 replica별 p95의 최댓값이다.',
        '- examples_per_second는 처리 샘플/초이며 다중 replica에서는 개별 처리량의 합이다. 온라인 요청/초 또는 생성 token/초가 아니다.',
        '- max_rss_kib는 프로세스 RSS 고수위이며 replica별 합산된다. 최소 필요 VM 메모리의 정답으로 사용하지 않는다.',
        '- 같은 조건의 반복 측정은 중복 실행과 다르다. 반복은 보존하되 평가에서는 workload 그룹을 동일 가중한다.',
        '- 중단/실패 회차는 보존하되 첫 회귀 실험에서 제외한다. 실패 원인은 audit.json에 기록했다.',
        '- 파생 lifecycle/합성 DB는 첫 학습에서 제외한다. 자연어 요구 분석, 실제 UPDATE/스케일 전환 성능은 이 실험의 평가 대상이 아니다.',
        '- 조건 그룹은 같은 모델·revision·batch·길이·동시성·dataset을 묶는다. CPU/GPU·replica 선택과 반복 회차가 분할을 넘어가지 않게 한다.',
        '- 상세 출처, 교차 캠페인 조건 겹침, 측정값 불일치, 장치 정보는 audit.json에 저장했다.', '']
    (output / 'data_audit.md').write_text('\n'.join(lines))
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, default=Path('/path/to/aiops/runs'))
    parser.add_argument('--output', type=Path, default=ROOT/'data'/'audit_v1')
    args = parser.parse_args()
    report = extract(args.source_root.resolve(), args.output)
    print(json.dumps({k: report[k] for k in ['eligible_rows', 'workload_groups', 'snapshot_sha256']}))
