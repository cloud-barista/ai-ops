"""Write a reproducible paired-results table and Korean research report."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def delta(old, new):
    return 100 * (old - new) / old if old else float('nan')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--artifacts', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    data = json.loads((args.artifacts / 'metrics.json').read_text())
    rows = []
    for name, result in data['cohorts'].items():
        for method, score in result['paired_v1_v2']['scores'].items():
            rows.append({'cohort': name, 'method': method, **{key: score[key] for key in
                ['mae', 'workload_macro_mae', 'p95_absolute_error', 'changed_gt10_mae']}})
    table = pd.DataFrame(rows)
    table.to_csv(args.artifacts / 'comparison.csv', index=False)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for ax, name, title in zip(axes, ['one_gpu_pod_and_server', 'one_gpu_pod'],
                              ['Primary: pod and server 1 GPU', 'Secondary: pod requests 1 GPU']):
        part = table[table.cohort == name]
        ax.bar(['Persistence', 'v1', 'v2'], part.mae, color=['#9ca3af', '#64748b', '#2563eb'])
        for i, value in enumerate(part.mae):
            ax.text(i, value, f'{value:.4f}', ha='center', va='bottom')
        ax.set_title(title)
        ax.set_ylabel('Row MAE, source SM units')
        ax.set_ylim(0, part.mae.max() * 1.2)
    fig.suptitle('Same day122 cases; exploratory previously seen-period evaluation')
    fig.savefig(args.artifacts / 'comparison.png', dpi=180)
    fig.savefig(args.artifacts / 'comparison.pdf')
    plt.close(fig)
    lines = ['# Alibaba GPU 다음 1시간 SM 예측 v2: 보존한 두 기간을 합친 재학습', '',
        '## 결과 요약', '',
        '- 요청 GPU가 1개인 보조 집합: 같은 평가 사례에서 v1 대비 행 MAE 1.618087→1.572560, 2.81% 감소. '
        '다만 변화>10 사례의 MAE는 15.843890→16.234859로 증가했다.',
        '- 서버까지 GPU 1개인 주 집합: 행 MAE 0.904966→0.907482, 0.28% 증가. '
        '작업 동일가중 MAE는 소폭 낮지만 재표집 구간이 0을 포함한다. v1 대비 주 성능 개선을 확인했다고 말하지 않는다.',
        '- 모델 파일 두 개에는 각각 직접·잔차 HGB가 들어 있다. v1을 교체하지 않고 연구 v2로 따로 저장한다.', '',
        '## 실험 범위', '',
        '기존 109~115일과 추가 116~122일 pod_hourly를 합쳐 HGB 직접 예측기와 '
        '현재 측정값에 학습 잔차를 더하는 예측기를 다시 학습했다. 기존 v1 모델과 원본 자료를 보존했다. '
        '두 기간은 이미 이전 실험에서 열람·평가됐으므로 아래 점수는 탐색 결과이며 새로운 독립 검증이 아니다.', '',
        '예측 대상은 정확히 1 GPU를 요청하는 온라인 GenAI pod의 다음 시간 SM 원본 측정값이다. '
        '과거 두 시간·현재·다음 시간 모두 같은 서버에서 유효하게 관측되는 사례만 포함한다. '
        '서버 GPU 수까지 네 시간 모두 1인 집합을 주 평가, 요청 GPU 수만 1인 집합을 보조 평가로 둔다. '
        '스케일 명령, 사용자 SLO, 최소 VM 수를 학습한 결과는 아니다.', '',
        '## 데이터 사용과 분할', '',
        '- 전체 원본 기간: 상대 일 번호 109~122, 336개 pod_hourly Parquet 파티션.',
        '- 학습: 109~120일, workload SHA256 bucket 0~69.',
        '- 모델 선택: 121일, bucket 70~84.',
        '- 평가: 122일, bucket 85~99.',
        '- 각 분할의 마지막 판단은 22시, 다음 시간 정답은 같은 분할 기간 안의 23시까지. '
        '학습 기간 중간 날짜의 23시 판단은 다음 날도 학습 기간이면 포함한다.',
        '- 서로 다른 분할의 workload_id와 pod_id 교집합은 0이다. '
        '평가 작업의 과거 완료 관측은 사용하지만 그 작업의 정답으로 모델을 적합하지 않는다.',
        '- 추가 기간 중 116~120일은 실제 모델 적합에 사용하고 121~122일은 선택·평가에 남겼다.',
        '- 모든 날짜에 걸친 ID 일관성 검사와 미래 관측 존재 조건을 적용했다. '
        '그 조건에 따른 관측 표본의 성능으로 해석한다. 계측 available_at은 공개 원본에 없어 시간 종료 즉시 이용 가능하다고 가정한다.', '',
        '## 학습 방법', '',
        'v1의 해시 고정 코드에서 동일한 정답·특징·HGB 설정·workload 동일 총 가중치를 재사용했다. '
        '직접 HGB는 다음 시간 값을, 잔차 HGB는 다음 값−현재 값을 학습한다. '
        '잔차 강도 α=0/0.25/0.5/1 및 직접 모델을 검증 구간의 (행 MAE+작업 동일가중 MAE)/2로 선택했다. '
        '현재값 유지·최근 3시간 평균·학습 GPU 종류 중앙값을 기준선으로 비교했다. 평가 결과를 보고 재조정하지 않았다.', '',
        '설정: loss=absolute_error, max_iter=150, learning_rate=0.05, max_leaf_nodes=15, '
        'min_samples_leaf=50, l2_regularization=1, early_stopping=False, seed=42. '
        '예측은 [0,100]으로 제한한다. GPU 종류·우선순위 one-hot, 과거 SM 수준·변화·평균·표준편차, '
        '시각 sin/cos, 서버 GPU 수, CPU 요청량을 사용한다. pod/workload ID와 다음 시간 측정값은 모델 입력에 넣지 않는다.', '',
        '## 같은 평가 행에서 v1과 v2 비교', '']
    for name in ['one_gpu_pod_and_server', 'one_gpu_pod']:
        result = data['cohorts'][name]
        paired = result['paired_v1_v2']
        scores = paired['scores']
        title = '주 평가: 요청·서버 모두 1 GPU' if name.endswith('and_server') else '보조 평가: 요청 1 GPU'
        lines.extend([f'### {title}', '',
            '| 분할 | 행 수 | workload 수 | pod 수 |', '|---|---:|---:|---:|'])
        for split, count in result['split_counts'].items():
            lines.append(f"| {split} | {count['rows']:,} | {count['workloads']:,} | {count['pods']:,} |")
        lines.extend(['', f"추가 기간의 실제 학습 행: {result['new_period_training_rows']:,}; "
            f"해당 행의 workload 수 {result['new_period_training_workloads']:,}. 이 workload 수는 기존과 겹칠 수 있다.", '',
            f"v1 경로: `{paired['v1_selected']}`, v2 검증 선택 경로: `{paired['v2_selected']}`.", '',
            '| 방법 | 행 MAE | 작업 동일가중 MAE | 절대오차 p95 | 변화>10 MAE |', '|---|---:|---:|---:|---:|'])
        for key, score in scores.items():
            vals = [score[k] for k in ['mae', 'workload_macro_mae', 'p95_absolute_error', 'changed_gt10_mae']]
            lines.append('| '+key+' | '+' | '.join('해당 없음' if v is None else f'{v:.6f}' for v in vals)+' |')
        old, new = scores['v1_selected'], scores['v2_selected']
        lines.extend(['', f"같은 122일 행에서 v1 대비 v2 행 MAE 감소율: {delta(old['mae'], new['mae']):.2f}%. "
            f"작업 동일가중 MAE 감소율: {delta(old['workload_macro_mae'], new['workload_macro_mae']):.2f}%. "
            '음수이면 오차가 증가한 것이다.',
            f"v1−v2 작업별 MAE 차의 2,000회 workload 재표집 구간: "
            f"{paired['v1_minus_v2_bootstrap']['workload_bootstrap_95_ci']}. "
            '고정 모델·이미 본 날짜에 조건부인 구간이며 학습 변동이나 독립 검증을 대신하지 않는다.',
            f"평가 변화>10 사례: {result['split_counts']['test']['changed_gt10']:,}행 / "
            f"{result['split_counts']['test']['workloads_with_change']:,}workload. "
            f"다음 SM≥80 사례: {result['split_counts']['test']['next_sm_ge80']:,}행.", ''])
        if name.endswith('and_server'):
            lines.extend(['주 집합에서 v1 대비 전반적 개선은 확인되지 않았다. 변화 사례도 4개 작업에만 있고 '
                          '고사용률 정답은 0건이다. 새 데이터 추가만으로 v1을 대체할 근거는 부족하다.', ''])
        else:
            lines.extend(['보조 집합의 평균 오차·작업 동일가중 오차·p95는 v1보다 낮지만, '
                          '변화>10 오차는 v1보다 높다. 급격한 변화 대응까지 일괄 개선됐다고 말하지 않는다.', ''])
        view = paired['views'].get('absent_from_v1_processed_examples')
        if view:
            lines.extend(['**기존 v1 가공 사례에 없던 workload 진단**', '',
                f"{view['support']['rows']:,}행·{view['support']['workloads']:,}workload; "
                f"변화>10 {view['support']['changed_gt10']:,}행·{view['support']['workloads_with_change']:,}workload."])
            for key, score in view['scores'].items():
                lines.append(f"- {key}: MAE {score['mae']:.6f}, 작업 동일가중 MAE {score['workload_macro_mae']:.6f}")
            lines.extend(['', '이 부분집합을 보고 모델을 선택하지 않았다. 새 앱 의미 일반화나 새 클러스터 시험으로 해석하지 않는다.', ''])
    lines.extend(['## 검증과 보존', '',
        '- 분할 경계·두 다운로드 기간 사이의 과거 연결·workload 충돌 제외·미관측 정답 제외·선택된 α 로드 추론에 대한 단위 검사 6개 통과.',
        '- 모델 파일을 다시 불러와 저장된 모든 v2 예측 경로를 재계산했다. 모든 경로 최대 절대 차이 0.',
        '- v1 모델 해시를 비교 전후 확인했고 변화가 없다. 기존 미래 기간 확인 보고서는 그대로 유지한다.',
        '- headline 행 MAE와 작업 동일가중 MAE를 별도 오차 집계로 확인했다.',
        '- 새 다운로드나 원본 삭제 없이 보존 중인 파일을 사용했다. '
        '이번 실행은 이전 전체 해시 감사의 manifest를 고정하고 파티션 범위·파일 크기를 확인했다.', '',
        '## 적용 범위와 다음 확인', '',
        '14일 규모 확대만으로 학습 샘플의 유효 독립 수가 14일 전체 행 수와 같아지지는 않는다. '
        '인접 시간 관측은 상관되어 있고 동일 workload에 많은 pod가 속한다. '
        '검증·평가는 각각 하루뿐이며 GPU 고사용률 정답과 실제 스케일 효과는 별도 과제다. '
        '새로운 미사용 기간을 사전에 정해 v2를 고정한 뒤 다시 평가해야 외부 확인 성과로 추가할 수 있다.', '',
        '## 파일 위치', '',
        '- 학습 코드: `/home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour_v2.py`',
        '- 기존 특징·학습 코드: `/home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour.py`',
        '- 검사: `/home/researcher/ai-mcmp/research/scripts/test_alibaba_gpu_sm_hour_v2.py`',
        '- 프로토콜: `/home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_protocol_v2.json`',
        '- 결과·모델·분할·비교 예측: `/home/researcher/ai-mcmp/research/artifacts/alibaba_gpu_sm_hour_v2/`',
        '- 원본109~115: `/home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days109_115/`',
        '- 원본116~122: `/home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days116_122/`', '',
        '재현 명령(새 output 경로를 지정해야 한다):', '',
        '```bash',
        'POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python /home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour_v2.py --output /tmp/alibaba_gpu_sm_hour_v2_rerun --protocol /home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_protocol_v2.json',
        '```', '',
        '원본 설명: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md', ''])
    args.report.write_text('\n'.join(lines))
    files = []
    for path in sorted(args.artifacts.rglob('*')):
        if path.is_file() and path.name != 'ARTIFACT_MANIFEST.json':
            files.append({'path': str(path.relative_to(args.artifacts)), 'bytes': path.stat().st_size,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (args.artifacts / 'ARTIFACT_MANIFEST.json').write_text(json.dumps({'files': files}, indent=2)+'\n')
    print(json.dumps({'report': str(args.report), 'files': len(files)}))


if __name__ == '__main__':
    main()
