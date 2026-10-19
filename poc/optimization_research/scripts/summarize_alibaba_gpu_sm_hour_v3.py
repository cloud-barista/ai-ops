"""Publish v3 data provenance, future comparisons, limitations and figures."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd


def change(before, after):
    return 100 * (before - after) / before if before else float('nan')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    metrics = json.loads((args.artifacts / 'metrics.json').read_text())
    raw = json.loads((args.raw / 'MANIFEST.json').read_text())
    source = raw['summary']['asi_opensource_pod_hourly']
    rows = []
    for c, result in metrics['cohorts'].items():
        for key in ['persistence', 'v1_selected', 'v2_selected', result['selected']]:
            s = result['scores'][key]
            rows.append({'cohort': c, 'path': key, 'mae': s['mae'], 'macro_mae': s['workload_macro_mae'],
                         'p95_absolute_error': s['p95_absolute_error'], 'changed_mae': s['changed_gt10_mae']})
    table = pd.DataFrame(rows)
    table.to_csv(args.artifacts / 'comparison.csv', index=False)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for ax, name, title in zip(axes, ['one_gpu_pod_and_server', 'one_gpu_pod'],
                              ['Primary: pod and server 1 GPU', 'Auxiliary: pod requests 1 GPU']):
        part = table[table.cohort == name]
        ax.bar(['Persistence', 'v1', 'v2', 'v3'], part.mae, color=['#9ca3af', '#64748b', '#60a5fa', '#2563eb'])
        for i, value in enumerate(part.mae):
            ax.text(i, value, f'{value:.4f}', ha='center', va='bottom', fontsize=9)
        ax.set_title(title)
        ax.set_ylabel('Row MAE, source SM units')
        ax.set_ylim(0, part.mae.max() * 1.22)
    fig.suptitle('Fixed model selection; same previously unused days126-129')
    fig.savefig(args.artifacts / 'comparison.png', dpi=180)
    fig.savefig(args.artifacts / 'comparison.pdf')
    plt.close(fig)
    primary = metrics['cohorts']['one_gpu_pod_and_server']
    aux = metrics['cohorts']['one_gpu_pod']
    lines = ['# Alibaba GPU SM 예측 v3: 데이터 확대 학습과 미사용 4일 평가', '',
        '## 요약', '',
        f"- 새 원본: 상대 일 번호123~129, {source['files']:,}개 Parquet, {source['rows']:,}행, "
        f"{source['bytes'] / 10**9:.2f}GB. 모든 원본 열과 행을 보존했다.",
        '- 기존109~122와 합쳐 총21일을 확보했다. 모델 적합109~124, 모델 선택125, 최종 평가126~129를 다운로드 전에 고정했다.',
        '- 학습·검증·평가 정제를 별도로 실행했고, 최종 평가를 읽기 전에 두 모델 파일과 선택 경로·해시를 고정했다.',
        f"- 주 평가의 v2 대비 전체 확인 조건: **{primary['comparisons']['v2_selected']['gate']}**.",
        f"- 보조 평가의 v2 대비 전체 확인 조건: **{aux['comparisons']['v2_selected']['gate']}**.",
        '- 이번 데이터 확대 재학습에서 v2 대비 성능 개선은 확인되지 않았다. v1·v2를 교체하지 않고 v3를 별도 연구 모델로 보존한다.',
        '- 아래는 다음 시간 GPU SM 원본 계측 예측 성능이다. 실제 스케일 명령의 효과나 사용자 SLO 개선을 검증한 결과가 아니다.', '',
        '## 데이터와 모델', '',
        'Alibaba Cluster Trace GPU v2026의 pod_hourly 파티션만 추가했다. 공식 ZIP 전체를 저장하지 않고 '
        '선택한 일별 Parquet 구간을 HTTP Range로 받았다. 추출 중 CRC32와 SHA256을 확인하고 '
        '모델이 읽는 새 파티션의 SHA256·행 수·열 수를 다시 확인했다. 기존 데이터와 v1·v2는 보존했다.', '',
        '학습 대상은 online_inference·genai·gpu_request=1인 pod다. 현재와 이전 두 시간, 다음 한 시간이 '
        '유효한 SM0~100 측정이며 같은 서버에서 관측되는 사례를 사용한다. 네 시간의 server_gpu_count도 '
        '모두1인 집합을 주 평가로 둔다. 미관측 다음 시간을 0으로 채우지 않는다.', '',
        '직접 HGB는 다음 SM값을 학습하고, 잔차 HGB는 다음 SM−현재 SM을 학습한다. '
        '고정 α0/0.25/0.5/1로 현재값에 잔차를 더해 비교한다. 선택 지표는 검증 구간의 '
        '(행 MAE+workload 동일가중 MAE)/2다. 현재값 유지, 최근3시간 평균, 학습 GPU 종류별 중앙값도 함께 비교하고 '
        '검증에서 단순 기준선이 가장 좋으면 그 경로를 선택한다.', '',
        'scikit-learn HGB: absolute_error,150 iterations, learning_rate0.05,max_leaf_nodes15,'
        'min_samples_leaf50,l2_regularization1,early_stoppingFalse,seed42. '
        'workload마다 학습 총 가중치를 같게 했다. v1·v2와 같은 고정 설정이며 최종 평가 결과로 재조정하지 않았다.', '',
        '특징: GPU 종류와 우선순위 one-hot, 현재/과거2시간 SM,3시간 평균·표준편차·변화, '
        '시각 sin/cos,서버 GPU 수,CPU 요청량. pod/workload ID와 미래 측정값은 입력에 쓰지 않는다.', '',
        '## 시간·작업 분리와 평가 순서', '',
        '- workload SHA256 bucket0~69:109~124일 모델 적합.',
        '- bucket70~84:125일 모델 선택.',
        '- bucket85~99:126~129일 평가.',
        '- 각 분할의 정답은 해당 기간 안에 끝난다. 검증·평가 첫 시간은 이전 날22/23시의 완료된 과거 관측을 사용할 수 있다.',
        '- 학습 데이터 정제에는 검증·평가의 ID나 결과를 사용하지 않았다. 검증은 학습과 겹치는 pod를, '
        '평가는 학습·검증과 겹치는 pod를 제외한다. 분할 간 workload와 pod 교집합은0이다.',
        '- 두 v3 모델의 검증 선택과 해시를 SELECTION_FREEZE.json에 저장한 다음 평가 측정값을 읽었다.',
        '- 사용률 측정이 실제로 전달된 available_at은 원본에 없다. 시간 종료 즉시 사용할 수 있다고 가정했다.',
        '- 미래 관측 존재·같은 서버라는 조건은 평가 표본의 경계다. 사라지거나 이주한 pod의 실패 위험을 평가한 것은 아니다.', '',
        '## 동일한 미래 평가 사례에서 비교', '']
    for c in ['one_gpu_pod_and_server', 'one_gpu_pod']:
        result = metrics['cohorts'][c]
        fit = metrics['fit']['cohorts'][c]
        title = '주 평가: 요청·서버 모두1GPU' if c.endswith('and_server') else '보조 평가: 요청1GPU'
        lines.extend([f'### {title}', '', '| 집합 | 행 수 | workload 수 | pod 수 |', '|---|---:|---:|---:|'])
        for label, count in [('학습109~124', fit['train_counts']), ('선택125', fit['validation_counts']), ('평가126~129', result['counts'])]:
            lines.append(f"| {label} | {count['rows']:,} | {count['workloads']:,} | {count['pods']:,} |")
        lines.extend(['', f"새123~124일의 실제 적합 행: {fit['new123_124_training_rows']:,}. "
            f"선택 경로: `{result['selected']}`. 검증에서 고른 단순 비교기: `{result['best_baseline']}`.", '',
            '| 경로 | 행 MAE | 작업 동일가중 MAE | 오차p95 | 변화>10 MAE |', '|---|---:|---:|---:|---:|'])
        for key in ['persistence', 'v1_selected', 'v2_selected', result['selected']]:
            s = result['scores'][key]
            vals = [s[k] for k in ['mae', 'workload_macro_mae', 'p95_absolute_error', 'changed_gt10_mae']]
            lines.append('| '+key+' | '+' | '.join('해당 없음' if v is None else f'{v:.6f}' for v in vals)+' |')
        v2, v3 = result['scores']['v2_selected'], result['scores'][result['selected']]
        lines.extend(['', f"v2 대비 행 MAE 감소율 {change(v2['mae'], v3['mae']):.2f}%, "
            f"작업 동일가중 MAE 감소율 {change(v2['workload_macro_mae'], v3['workload_macro_mae']):.2f}%. 음수이면 오차 증가다.", '',
            '| 비교 대상 | 전체 조건 | 개선 날짜 수/4 | 작업별 차의95% 재표집 구간 |', '|---|---|---:|---|'])
        for comparator, pair in result['comparisons'].items():
            lines.append(f"| {comparator} | {pair['gate']} | {pair['improved_days']} | "
                         f"{pair['bootstrap']['workload_bootstrap_95_ci']} |")
        lines.extend(['', '전체 조건은 사전에 고정했다:1000행·20workload·변화30행·변화5workload·4일 존재; '
            '행/작업 동일가중 MAE 감소, 변화 오차·p95 비악화,4일 중3일 이상 감소,workload 재표집 차 구간 하한>0. '
            'INSUFFICIENT_DATA는 필요한 표본 부족,NO_GO는 조건 일부 미충족이다.', '',
            f"평가 변화>10: {result['counts']['changed_gt10']:,}행 / {result['counts']['workloads_with_change']:,}workload. "
            f"최대 작업의 변화 점유율 {result['counts']['largest_workload_change_share']}. "
            f"다음 SM≥80: {result['counts']['next_sm_ge80']:,}행. 미지 GPU 범주 {result['unknown_test_gpu_category_rows']:,}행.", '',
            '| 날짜 | v1 MAE | v2 MAE | v3 MAE |', '|---|---:|---:|---:|'])
        for day in ['126', '127', '128', '129']:
            values = [result['scores'][key]['by_day'][day]['mae'] for key in ['v1_selected', 'v2_selected', result['selected']]]
            lines.append('| '+day+' | '+' | '.join(f'{v:.6f}' for v in values)+' |')
        lines.extend(['', '조건별 검사 결과:', ''])
        for comparator, pair in result['comparisons'].items():
            failed = [name for name, passed in {**pair['support_checks'], **pair['checks']}.items() if not passed]
            lines.append(f"- {comparator}: 미충족 {', '.join(failed) if failed else '없음'}")
        lines.append('')
    lines.extend(['## 검증·해석', '',
        '- 단위 검사7개: 분할/정답 경계,첫 평가 시점 과거 창,학습 reader의 평가 기간 접근 금지,'
        '평가를 읽지 않는 선택 고정,변경된 모델 거부,추론 중 fit 금지,확인 조건 PASS/NO_GO/표본부족.',
        '- 저장 모델을 다시 불러와 디스크의 평가 예측을 재현했다. 모든 v3 경로의 최대 절대 차이0.',
        '- 행 MAE와 workload 동일가중 MAE를 독립 집계했다. v1·v2·선택된 v3 해시가 평가 전후 같았다.',
        '- 같은 클러스터의 미사용 미래 기간이다. 새 클러스터·GPU 세대·앱 의미의 일반화 시험은 아니다.',
        '- 학습 기간 확대와 데이터 정제의 기간 분리를 함께 적용했다. v2와의 차이를 순수 표본 수만의 인과 효과로 주장하지 않는다.',
        '- workload 재표집 구간은 이 고정 모델과4일에 조건부다. 학습 seed 변동·장기 기간 이동·클러스터 간 불확실성을 포함하지 않는다.',
        '- 실제 DEPLOY/SCALE_OUT/SCALE_IN의 인과 효과와 사용자SLO는 이 자료로 학습하지 않았다.', '',
        '## 보존 위치와 재현', '',
        '- 새 원본: `/home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days123_129/`',
        '- 모델·분할·검증선택·평가 예측·지표: `/home/researcher/ai-mcmp/research/artifacts/alibaba_gpu_sm_hour_v3/`',
        '- 학습: `/home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour_v3.py`',
        '- 다운로드: `/home/researcher/ai-mcmp/research/scripts/download_alibaba_gpu_sm_period.py`',
        '- 단위 검사: `/home/researcher/ai-mcmp/research/scripts/test_alibaba_gpu_sm_hour_v3.py`',
        '- 사전 프로토콜: `/home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_protocol_v3.json`', '',
        '재현할 때 새 output 경로를 지정한다. 기존 실험 파일은 덮어쓰지 않는다.', '',
        '```bash',
        'POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python /home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour_v3.py --phase all --new-root /home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days123_129 --protocol /home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_protocol_v3.json --output /tmp/alibaba_gpu_sm_hour_v3_rerun',
        '```', '',
        '공식 원본: [Alibaba 데이터 다운로드 설명](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/data_download.md), '
        '[스키마](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md).', ''])
    args.report.write_text('\n'.join(lines))
    manifest = []
    for path in sorted(args.artifacts.rglob('*')):
        if path.is_file() and path.name != 'ARTIFACT_MANIFEST.json':
            manifest.append({'path': str(path.relative_to(args.artifacts)), 'bytes': path.stat().st_size,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (args.artifacts / 'ARTIFACT_MANIFEST.json').write_text(json.dumps({'files': manifest}, indent=2)+'\n')
    print(json.dumps({'report': str(args.report), 'artifact_files': len(manifest)}))


if __name__ == '__main__':
    main()
