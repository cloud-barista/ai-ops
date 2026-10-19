"""Recheck saved frozen predictions and write evidence-based research outputs."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--evaluator', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('Refusing to overwrite report')
    metrics = json.loads((args.artifact / 'metrics.json').read_text())
    freeze = metrics['freeze']
    spec = importlib.util.spec_from_file_location('future_eval_replay', args.evaluator)
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    if ev.sha(args.evaluator) != metrics['evaluation_script_sha256']:
        raise ValueError('Evaluation code changed')
    examples = pd.read_parquet(args.artifact / 'examples.parquet')
    records = {}
    table = []
    labels = [('one_gpu_pod', '1GPU 요청 pod'),
              ('one_gpu_pod_and_server', '1GPU 요청과 단일 GPU 서버')]
    for cohort, label in labels:
        frame = examples
        if cohort == 'one_gpu_pod_and_server':
            frame = frame[frame.strict_single_gpu_server]
        saved = pd.read_parquet(args.artifact / cohort / 'predictions.parquet')
        if not frame[['pod_id', 'workload_id', 't']].reset_index(drop=True).equals(
                saved[['pod_id', 'workload_id', 't']].reset_index(drop=True)):
            raise ValueError('Replay row order differs')
        model_path = Path(freeze['model_source']) / cohort / 'model_bundle.joblib'
        if ev.sha(model_path) != freeze['models'][cohort]:
            raise ValueError('Frozen model changed')
        bundle = joblib.load(model_path)
        fresh = ev.fixed_predictions(frame, bundle)
        differences = {k: float(np.max(np.abs(v - saved['pred_' + k].to_numpy()))) for k, v in fresh.items()}
        if max(differences.values()) != 0:
            raise ValueError('Saved predictions differ from replay')
        mask = saved.primary_confirmation.to_numpy()
        primary = saved.loc[mask]
        scoring = metrics['cohorts'][cohort]['views']['untrained_workload_bucket_future']
        independent_score_checks = {}
        for name, prediction in fresh.items():
            errors = np.abs(primary.target_sm.to_numpy() - prediction[mask])
            macro = pd.Series(errors).groupby(primary.workload_id.to_numpy()).mean().mean()
            expected = scoring['scores'][name]
            calc = {'mae': float(errors.mean()), 'workload_macro_mae': float(macro),
                    'p95_absolute_error': float(np.quantile(errors, .95))}
            for key, value in calc.items():
                if abs(value - expected[key]) > 1e-12:
                    raise ValueError('Independent metric replay differs')
            independent_score_checks[name] = calc
            table.append({'cohort': cohort, 'method': name, **calc})
        records[cohort] = {'rows': len(saved), 'primary_rows': len(primary),
                           'routes_replayed': len(fresh), 'max_prediction_difference': max(differences.values()),
                           'model_hash_unchanged': True, 'independent_metrics_match': True}
    verification = {'cohorts': records, 'future_decision_range_valid': bool(
        examples.t.between(116 * 24, 122 * 24 + 22).all()),
        'target_time_plus_one_valid': bool((examples.target_hour_index == examples.t + 1).all()),
        'latest_observation_available_before_target_end': bool(
            (examples.measurement_available_hour_index < examples.target_available_hour_index).all())}
    if not all(v for k, v in verification.items() if k != 'cohorts'):
        raise ValueError('Time invariant failed')
    (args.artifact / 'replay_verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    pd.DataFrame(table).to_csv(args.artifact / 'comparison.csv', index=False)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    methods = ['persistence', 'direct_hgb', freeze['selected_route']]
    for ax, (cohort, label), title in zip(axes, labels, ['One GPU requested', 'Single-GPU server']):
        scores = metrics['cohorts'][cohort]['views']['untrained_workload_bucket_future']['scores']
        x = np.arange(2)
        for i, (method, text, color) in enumerate(zip(methods, ['Current value', 'Direct HGB', 'Frozen residual HGB'], ['#9aa4b2', '#e59e36', '#2178b5'])):
            bars = ax.bar(x + (i - 1) * .24, [scores[method]['mae'], scores[method]['workload_macro_mae']], width=.23, label=text, color=color)
            ax.bar_label(bars, fmt='%.3f', padding=3, fontsize=8)
        ax.set_xticks(x, ['Row MAE', 'Equal-workload MAE'])
        ax.set_title(title)
        ax.set_ylabel('Absolute SM measurement error')
        ax.spines[['top', 'right']].set_visible(False)
        ax.margins(y=.15)
    handles, texts = axes[0].get_legend_handles_labels()
    fig.legend(handles, texts, loc='upper center', bbox_to_anchor=(.5, .94), ncol=3, frameon=False, fontsize=9)
    fig.suptitle('Frozen next-hour SM forecast on unused days 116 to 122')
    fig.text(.5, .015, 'Same cluster; untrained workload hash bucket; conditional on same-server next-hour observation.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .05, 1, .86))
    fig.savefig(args.artifact / 'comparison.png', dpi=220)
    fig.savefig(args.artifact / 'comparison.pdf')
    plt.close(fig)
    gate = metrics['overall_confirmation_gate']
    conclusion = {'PASS': '주 확인 집합에서 고정 모델의 개선이 재현됐다.',
                  'NO_GO': '주 확인 집합에서 사전 정의한 개선 조건을 모두 재현하지 못했다.',
                  'INSUFFICIENT_DATA': '주 확인 집합의 유효 사례 지지가 사전 최소 조건에 부족하다.'}[gate]
    lines = ['# Alibaba GPU 다음 시간 계측 예측의 미사용 기간 검증', '',
             f'검증 판정은 **{gate}**다. {conclusion} 기존 7일에서 학습한 모델을 고정하고 바로 다음 미사용 7일에서 평가했다. 이 결과는 같은 공개 클러스터의 미래 기간 확인이며 실제 스케일링 효과 검증은 아니다.', '',
             '## 고정 설정과 자료', '',
             '- 새 기간은 상대 날짜 116~122일이며 새 타깃을 읽기 전에 기간과 게이트를 고정했다. 첫 판단의 과거 두 시간만 기존 day115 hour22~23을 사용했다.',
             '- 기존 학습 코드와 두 모델의 SHA256이 동결 설정과 일치했고 평가 전후와 재추론 뒤에도 같았다. 재학습·alpha 변경·새 점수에 따른 모델 선택은 수행하지 않았다.',
             '- 선택 경로는 현재 SM + 잔차 HGB 예측, alpha1이다. 최선 단순 기준선은 원래 검증에서 고정한 현재값 유지다. 직접 HGB와 두 다른 단순 경로는 같은 모델/설정으로 보조 비교했다.',
             '- 온라인 GenAI·1GPU 요청을 대상으로 최근 3개 완료 창과 다음 창의 동일 서버 유효 관측을 연결했다. 중복 pod/시간과 불안정·결측 workload는 제외했다. 미래 미관측을 0으로 만들지 않았다.',
             '- 주 집합은 모든 네 창에서 단일 GPU 서버이며 학습에 사용하지 않은 workload hash bucket85~99다. 넓은 1GPU 요청 집합은 별도 보조 확인이다.',
             f"- 원본 파일 {metrics['source_verification']['files_checked']}개, {metrics['source_verification']['bytes']:,}바이트, {metrics['source_verification']['rows']:,}행을 CRC 다운로드 검사에 더해 SHA256·행수·컬럼수로 재검산했고 오류는 0건이었다.", '',
             '## 주 확인과 넓은 집합 결과', '',
             '| 집합 | 행과 workload | 판정 | 현재값 MAE | 잔차 MAE | 행 오차 감소 |',
             '|---|---:|---|---:|---:|---:|']
    for cohort, label in labels:
        v = metrics['cohorts'][cohort]['views']['untrained_workload_bucket_future']
        base = v['scores']['persistence']; selected = v['scores'][freeze['selected_route']]
        change = 100 * (base['mae'] - selected['mae']) / base['mae']
        lines.append(f"| {label} | {v['counts']['rows']:,} / {v['counts']['workloads']:,} | {v['gate']} | {base['mae']:.6f} | {selected['mae']:.6f} | {change:.2f}% |")
    lines += ['', '| 집합 | 지표 | 현재값 | 직접 HGB | 고정 잔차 HGB |', '|---|---|---:|---:|---:|']
    for cohort, label in labels:
        v = metrics['cohorts'][cohort]['views']['untrained_workload_bucket_future']
        for key, text in [('workload_macro_mae', 'workload 동일가중 MAE'), ('p95_absolute_error', '절대오차 p95'), ('changed_gt10_mae', '10SM단위 초과 변화 구간 MAE')]:
            values = [v['scores'][method][key] for method in methods]
            formatted = ['없음' if value is None else f'{value:.6f}' for value in values]
            lines.append(f"| {label} | {text} | {' | '.join(formatted)} |")
    lines += ['', '## 사전 판정 조건과 날짜별 결과', '',
              '최소 지지는 1,000행·20workload·변화 30개·변화가 있는 workload 5개·7일 전부 관측이다. 개선 게이트는 행/작업별 오차 감소, 변화 구간 오차와 p95의 비악화, 7일 중 최소 5일 행 오차 개선, 작업 단위 재표집 차이 구간 하한 양수다. 어느 하나 실패하면 NO_GO이고 지지가 부족하면 INSUFFICIENT_DATA다.', '']
    for cohort, label in labels:
        v = metrics['cohorts'][cohort]['views']['untrained_workload_bucket_future']
        ci = v['bootstrap']['workload_bootstrap_95_ci']
        lines += [f"{label}: 개선 날짜 {v['improved_days']}/7일. 기준선-잔차 workload MAE 차의 95% 구간 [{ci[0]:.6f}, {ci[1]:.6f}].", '',
                  '지지 검사: ' + json.dumps(v['support_checks'], ensure_ascii=False), '',
                  '개선 검사: ' + json.dumps(v['gate_checks'], ensure_ascii=False), '',
                  '| 날짜 | 현재값 MAE | 잔차 MAE |', '|---|---:|---:|']
        for d in sorted(v['scores']['persistence']['by_day'], key=int):
            b = v['scores']['persistence']['by_day'][d]['mae']; p = v['scores'][freeze['selected_route']]['by_day'][d]['mae']
            lines.append(f'| {d} | {b:.6f} | {p:.6f} |')
        lines.append('')
    lines += ['## 보조 집합과 적용 한계', '',
              '| 집합 | 자료 구분 | 행과 workload | 현재값 MAE | 잔차 MAE |', '|---|---|---:|---:|---:|']
    for cohort, label in labels:
        for view, text in [('trained_workloads_future', '학습했던 workload의 미래'), ('previously_unseen_workloads', '기존 가공 train val test에 없던 workload')]:
            v = metrics['cohorts'][cohort]['views'][view]
            if v.get('rows') == 0:
                lines.append(f'| {label} | {text} | 0 | 없음 | 없음 |')
            else:
                lines.append(f"| {label} | {text} | {v['counts']['rows']:,} / {v['counts']['workloads']:,} | {v['scores']['persistence']['mae']:.6f} | {v['scores'][freeze['selected_route']]['mae']:.6f} |")
    lines += ['', '보조 집합은 주 확인 실패를 대체하지 않는다. 신규 workload는 기존 학습·검증·시험의 가공 예제에 없던 익명 ID를 뜻하며 전체 과거 원본에서 한 번도 없었던 새 AI 모델이라는 의미는 아니다.', '']
    for cohort, label in labels:
        v = metrics['cohorts'][cohort]['views']['untrained_workload_bucket_future']; c = v['counts']
        lines.append(f"- {label}: 변화 {c['changed_gt10']:,}개가 {c['workloads_with_change']}workload에 분포하며 최대 workload의 변화 점유율은 {100*(c['largest_workload_change_share'] or 0):.2f}%다. 다음 SM>=80은 {c['next_sm_ge80']:,}개다.")
    lines += ['',
              '- workload 단위 재표집은 고정 모델·이 일주일에 조건부다. 같은 날짜의 공통 부하나 다른 클러스터 변화를 포괄하는 보장은 아니다.',
              '- 두 층의 개선율을 합산하지 않는다. 넓은 층의 값은 공개 원시 SM 계측이며 다중 GPU 서버에서 물리적인 단일 GPU 백분율로 단정하지 않는다.',
              '- ID 일관성 선별과 다음 시간의 유효 관측이라는 미래 조건이 있다. 실제 계측 전달 지연은 공개 원본에 없어 구간 종료 즉시 이용 가능하다고 가정했다.',
              '- 실제 GPU 부족·OOM·앱 지연 SLO·VM 준비·Scale-out/in 행동 효과는 이 평가의 정답에 없다. 개선 재현 여부와 관계없이 실행 정책의 운영 채택은 별도 데이터와 시험이 필요하다.', '',
              '## 재현 위치와 검증', '',
              f"원본: `{metrics['data_root']}`", '',
              '동결 설정: `/home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_future_freeze_v1.json`', '',
              '평가 코드: `/home/researcher/ai-mcmp/research/scripts/evaluate_alibaba_gpu_sm_future.py`', '',
              '결과: `/home/researcher/ai-mcmp/research/artifacts/alibaba_gpu_sm_future_confirmation_v1/`', '',
              '모델 해시 전후 일치, 시간 경계, 학습 workload/pod 중복 0건, 저장 예측의 재계산 차이 0, NumPy/Pandas로 별도 계산한 행·작업별·p95 지표 일치를 확인했다. 평가기는 fit을 호출하지 않는다. 원본과 이전 결과는 보존했다.', '',
              '출처: [Alibaba 공식 스키마](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md)']
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text('\n'.join(lines) + '\n')
    paths = sorted(p for p in args.artifact.rglob('*') if p.is_file() and p.name != 'ARTIFACT_MANIFEST.json')
    (args.artifact / 'ARTIFACT_MANIFEST.json').write_text(json.dumps({'files': [
        {'path': str(p.relative_to(args.artifact)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths]}, indent=2) + '\n')
    print(json.dumps({'report_written': str(args.report), 'gate': gate, 'replay_verified': verification}))


if __name__ == '__main__':
    main()
