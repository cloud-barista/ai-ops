"""Summarize fixed experiments, paired confirmation and exact-output adoption."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import sklearn

ROOT = Path('/home/researcher/ai-mcmp/research')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    names = ['ordered_forest_v1', 'row_partition_forest_v1', 'row_partition_forest_confirmation_v1']
    data = {name: json.loads((ROOT / 'artifacts' / name / 'results.json').read_text()) for name in names}
    out = ROOT / 'artifacts/row_partition_forest_confirmation_v1'
    confirmed = data[names[-1]]
    if data['row_partition_forest_v1']['gates']['row2']['status'] != 'GO' or confirmed['gates']['row2']['status'] != 'GO':
        raise ValueError('Adoption requires both exploratory and confirmation gates')
    rows = []
    for name, result in data.items():
        for scenario, values in result['scenarios'].items():
            for mode, times in values['timings'].items():
                for index, (wall, cpu) in enumerate(zip(times['wall_ms'], times['cpu_ms'])):
                    rows.append({'experiment': name, 'scenario': scenario, 'mode': mode,
                                 'pair': index, 'wall_ms': wall, 'cpu_ms': cpu})
    pd.DataFrame(rows).to_csv(out / 'all_experiments_timings.csv', index=False)
    scenarios = ['shared_slo', 'copied_slo', 'unique_grids']
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    x = list(range(3))
    baseline = [confirmed['scenarios'][n]['median_wall_ms']['v6'] for n in scenarios]
    candidate = [confirmed['scenarios'][n]['median_wall_ms']['row2'] for n in scenarios]
    axes[0].bar([i - .18 for i in x], baseline, width=.36, label='v6 serial forest')
    axes[0].bar([i + .18 for i in x], candidate, width=.36, label='v7 row partitions')
    axes[0].set_xticks(x, ['Shared SLO', 'Copied SLO', '575 distinct grids'])
    axes[0].set_ylabel('Whole assessment wall time, median ms')
    axes[0].legend()
    speeds = confirmed['scenarios']['unique_grids']['pair_speedups']['row2']
    axes[1].plot(range(1, 10), speeds, 'o-', label='Paired v6 / v7')
    axes[1].axhline(1, color='gray', linestyle='--', label='Equal time')
    axes[1].axhline(statistics.median(speeds), color='tab:orange', linestyle=':', label='Median')
    axes[1].set_xlabel('Confirmation pair')
    axes[1].set_ylabel('Wall speed ratio, higher is faster')
    axes[1].legend()
    fig.suptitle('Same exact JSON; same-host warm offline decision calculation')
    fig.savefig(out / 'comparison.png', dpi=180)
    fig.savefig(out / 'comparison.pdf')
    plt.close(fig)
    for filename, target in [('/tmp/ordered_forest_benchmark.log', 'ordered_forest_v1'),
                             ('/tmp/row_partition_benchmark.log', 'row_partition_forest_v1'),
                             ('/tmp/row_partition_confirmation.log', 'row_partition_forest_confirmation_v1')]:
        shutil.copy2(filename, ROOT / 'artifacts' / target / 'benchmark.log')
    for filename in ['ordered_forest_tests', 'row_partition_tests', 'shape_microbatch_regression_v7', 'row_partition_output_tests']:
        shutil.copy2('/tmp/' + filename + '.log', out / (filename + '.log'))
    shutil.copy2('/tmp/shape_v6_profile.txt', out / 'v6_exploratory_profile.txt')
    adoption = {'status': 'GO_FIXED_ROW2', 'api': 'assess_gpu_shape_microbatch_v7',
                'workers': 2, 'batch_grids': 256, 'parallel_min_supported_rows': 1024,
                'scope': confirmed['scope'], 'python': platform.python_version(),
                'sklearn': sklearn.__version__, 'v7_sha256': sha(ROOT / 'scripts/assess_gpu_shape_microbatch_v7.py'),
                'model_unchanged': confirmed['model_and_source_unchanged'],
                'stages_passed': ['row_partition_forest_v1', 'row_partition_forest_confirmation_v1']}
    (out / 'ADOPTION.json').write_text(json.dumps(adoption, indent=2)+'\n')
    unique = confirmed['scenarios']['unique_grids']
    reduction = 100 * (1 - unique['median_wall_ms']['row2'] / unique['median_wall_ms']['v6'])
    faster = sum(value > 1 for value in speeds)
    lines = ['# AI-MCMP 추론 최적화 연구: 행 분할과 고정 순서 트리 합산', '',
        '2026-10-05 | 경희대 배포·제어 판단용 저장 성능 예측기의 오프라인 계산 최적화', '',
        '## 결론', '',
        '**새 연구 경로 v7은 후보 행을 2개 worker에 나누고, 각 행의 트리 합산 순서를 고정한다.** '
        '검증된 입력을 한 번만 검사하고, 개별 트리의 중복 입력 검증을 제거한다. '
        '기존 v6의 형상 판단·보류·출력 JSON을 유지했다.', '',
        f"575개 서로 다른 형상의 두 번째9쌍 확인에서 함수 전체 벽시계 중앙값은 "
        f"{unique['median_wall_ms']['v6']:.3f}→{unique['median_wall_ms']['row2']:.3f}ms "
        f"({reduction:.2f}% 감소), 짝별 속도비 중앙값은 **{unique['median_pair_speedup']['row2']:.3f}배**였다. "
        f"첫9쌍에서는 {data['row_partition_forest_v1']['scenarios']['unique_grids']['median_pair_speedup']['row2']:.3f}배였다.", '',
        '두 번 모두 사전 속도·출력·메모리 기준을 통과했다. '
        '트리 자체를 병렬화한 첫 후보와4 worker 행 분할은 속도 기준을 통과하지 못해 기본 경로로 채택하지 않았다.', '',
        '## 해결한 문제', '',
        '기존의 sklearn 병렬 forest는 트리 작업이 끝나는 순서에 따라 공유 결과에 더한다. '
        '이전 연구에서2/4worker는 최종 JSON의 마지막 자리 수치가 달라 정확 출력 계약을 통과하지 못했다. '
        '한편 로컬1.6.1 구현은 forest 수준 입력 검증 후 각 tree.predict에서 같은 입력 검증을 반복한다.', '',
        '먼저 트리를 병렬로 계산한 뒤 원래 순서로 더하는 방법을 시험했다. 출력 차이는 해결했지만 '
        '고유575형상에서1.02~1.06배로 속도 기준1.10배에 못 미쳤다. '
        '이 음성 결과를 보존한 뒤 후보 행을 나누는 별도 가설을 사전에 고정하고 시험했다.', '',
        '## 우리의 방법과 구성', '',
        '1. 기존 검증·지원 범위 검사와 후보 중복 제거를 유지하고, 최대256격자를 같은 모델 호출로 모은다.',
        '2. One-hot와 수치 전처리는 기존 Pipeline을 그대로 사용한다. 입력 행렬은 forest._validate_X_predict로 한 번 검증한다.',
        '3. 지원된 행이1024개 이상일 때2개 연속 행 구간으로 나눈다. 그보다 작으면 단일 경로로 실행한다.',
        '4. 각 구간에서200개 트리의 예측을 원래 estimator 순서대로 float64에 더한다. '
        '개별 트리에는 이미 검증된 입력만 check_input=False로 넘긴다.',
        '5. 트리 수로 나눈 결과를 원래 행 순서로 연결한 뒤 기존 exp 변환·SLO 비교·최소 형상 선택을 수행한다.',
        '6. thread pool은 완전한 판단 함수 한 호출 안에서 생성·종료한다. '
        '저장 모델과 caller Pipeline을 수정하지 않고, 합친 호출 실패·잘못된 길이 반환은 기존8후보씩 예측하는 경로로 재시도한다.', '',
        '새 학습이나 모델 구조 변경은 없다. 직접 구현한 연구 요소는 정확한 수치·지원범위·판단 의미를 '
        '보존하면서 행 병렬화와 검증 비용 제거를 연결한 계산 경로와 확인 실험이다. '
        '행 병렬화·고정 순서 덧셈을 학술적으로 최초 제안했다는 주장은 하지 않는다.', '',
        '## 데이터와 비교 모델', '',
        '- 기존 SQLite에서 추출한 local-exhaustive의575 workload GPU 격자를 재사용했다. '
        '각 격자는 replica1/2 × CPU affinity2/4/8/16의8후보다.',
        '- 저장 ExtraTrees200개·두 로그 출력(배치p95,examples/sec)을 그대로 사용했다. '
        '성능 측정 대상9개 앱 모델과 이 성능 예측기를 구분한다.',
        '- 공유 SLO:45격자×25생성SLO=1125질의. 복사 SLO:같은 조건을 별도 후보 객체로 만든1125질의. '
        '고유 격자:575개의 서로 다른 작업 질의.',
        '- 동일 모델·원본·입력 생성기·v6 해시를 고정했다. '
        '모델/source 해시는 모든 비교 전후 같았고 caller의 n_jobs·predict 속성도 변하지 않았다.', '',
        '## 확인 실험 결과', '',
        '| 입력 | v6 wall 중앙 ms | v7 wall 중앙 ms | 9쌍 속도비 중앙 | v6 CPU 중앙 ms | v7 CPU 중앙 ms |',
        '|---|---:|---:|---:|---:|---:|']
    for name in scenarios:
        s = confirmed['scenarios'][name]
        lines.append(f"| {name} | {s['median_wall_ms']['v6']:.3f} | {s['median_wall_ms']['row2']:.3f} | "
                     f"{s['median_pair_speedup']['row2']:.3f} | {statistics.median(s['timings']['v6']['cpu_ms']):.3f} | "
                     f"{statistics.median(s['timings']['row2']['cpu_ms']):.3f} |")
    cpu_old = statistics.median(unique['timings']['v6']['cpu_ms'])
    cpu_new = statistics.median(unique['timings']['row2']['cpu_ms'])
    lines.extend(['', f"고유 격자의 CPU 누적 시간은 {cpu_old:.3f}→{cpu_new:.3f}ms "
        f"({100 * (cpu_new / cpu_old - 1):.2f}% 증가)였다. 벽시계 시간을 줄이는 대신2worker의 추가 CPU 시간을 사용했다. "
        '반복 SLO 입력은 지원 행이1024미만이어서 pool을 생성하지 않았다. 그 입력의 이득은 병렬화 효과라고 말하지 않는다.', '',
        f"고유 격자의 확인9쌍 중 {faster}쌍에서 빨랐고, 속도비 범위는 {min(speeds):.3f}~{max(speeds):.3f}배였다. "
        '짝별 중앙값과 두 시간 중앙값의 몫은 다를 수 있다. 모든 호출이 빨라진다는 보장은 아니며 '
        '통계적 유의성·다른 머신 일반화를 주장하지 않는다.', '',
        '| 입력 | v6 peak RSS MiB | v7 peak RSS MiB | 차이 MiB |', '|---|---:|---:|---:|'])
    for name in scenarios:
        a, b = [confirmed['memory'][name][m]['peak_rss_mib'] for m in ['v6', 'row2']]
        lines.append(f'| {name} | {a:.3f} | {b:.3f} | {b-a:+.3f} |')
    lines.extend(['', 'RSS는 각각 별도 프로세스에서 입력·모델 적재와 완전한 함수 호출을 포함한 최대값이다. '
        '작은 차이나 음수 차이를 메모리 절약 성과로 해석하지 않는다. 모든 입력에서 사전20MiB 증가 한도 안이었다.', '',
        '## 정확성·채택 검사', '',
        '- 후보별 원시 예측뿐 아니라 모든 응답의 정규 JSON을 초기·9쌍 전체에서 비교했다. '
        '정확 동등 여부는 수치 허용오차를 쓰지 않고 판단했다.',
        '- 1,080개 합성 경계 질의: 예측 SLO 값과 정확히 같은 값 및 np.nextafter로 한 자리 위·아래의 값을 사용했다. '
        '두 단계 모두 판단 JSON이 같았다.',
        '- 큰 호출의 예외와 길이 오류를 주입해 기존 격자별 재시도 결과가 같은지 확인했다.',
        '- 개별 엔진 검사22개(두 방식 각각11개):단일/다중 출력,NaN,희소 행렬,Infinity·잘못된 차원·미학습 모델 거부,'
        '한 번 검증,불균등 행 분할,순서가 바뀐 완료,동시 호출,실패 후 pool 종료,보류 및 reason 리스트 독립성.',
        '- 기존v2~v6와 새v7의24개 테스트 통과. v7은 worker2와256격자 상한을 고정하고 기존 계약을 유지한다.',
        '- 재현 시 새 output 경로를 보존하는 검사 2개까지 총 48개 검사가 통과했다. '
        '실험 당시 확인 wrapper 소스는 artifact의 confirmation_wrapper_executed.py에 별도로 보존했다.',
        '- 채택 조건:정확 JSON,고유 형상 속도비≥1.10,공유/복사 입력 악화≤10%,추가peak RSS≤20MiB. '
        '2worker는 탐색과 새9쌍 확인 모두 GO다.', '',
        '## 적용 범위와 다음 연구', '',
        '성과는 같은 호스트의 warm 저장 성능 모델을 사용하는 오프라인 형상 판단 함수다. '
        '모델 적재·JSON/API 왕복·온라인 AI 앱 추론·VM 생성/배포/스케일 효과는 측정하지 않았다. '
        '기존20.13배·64→256개선 수치와 곱해 누적 속도비를 만들지 않는다.', '',
        'sklearn1.6.1의 실제 forest 입력 검사 구현에 의존한다. 다른 버전·모델이면 최적화가 실패할 수 있어 '
        '기존 per-grid 예측으로 돌아간다. 현재 테스트에서2worker가 낫다는 결과를 다른 장비에 그대로 적용하지 않는다.', '',
        '다음에는 실제 서비스 프로세스의 동시 요청수·CPU 할당량별 지연과 누적CPU 비용을 함께 확인하고, '
        '입력 검증 제거와 행 병렬화의 독립 기여를 같은 실행의 절제 실험으로 분리한다. '
        '현재 결과만으로 두 기여의 정확한 비율을 나누어 말하지 않는다.', '',
        '## 코드·근거·재현', '',
        '- 연구 기본 경로:`research/scripts/assess_gpu_shape_microbatch_v7.py`',
        '- 행 분할 계산:`research/scripts/row_partition_forest_predict.py`',
        '- 공통 support·Pipeline adapter:`research/scripts/ordered_forest_predict.py`',
        '- 세 요청 형태·후보 검증:`research/scripts/assess_gpu_shape_row_partition.py`',
        '- 비교/확인:`research/scripts/benchmark_row_partition_forest.py`, `research/scripts/confirm_row_partition_forest.py`',
        '- 첫 미채택 방법:`research/artifacts/ordered_forest_v1/results.json`',
        '- 선택 단계:`research/artifacts/row_partition_forest_v1/results.json`',
        '- 확인·CSV·그림·검사 로그:`research/artifacts/row_partition_forest_confirmation_v1/`',
        '- 사전 규약:`research/reports/decision_inference_row_partition_protocol_v1.json`, '
        '`research/reports/decision_inference_row_partition_confirmation_protocol_v1.json`', '',
        '작업 경로는 `/home/researcher/ai-mcmp`다. 기존 결과를 보존하려면 새 output 디렉터리를 지정한다.', '',
        '```bash',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python research/scripts/benchmark_row_partition_forest.py --output /tmp/row_partition_rerun',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python research/scripts/confirm_row_partition_forest.py --output /tmp/row_partition_confirmation_rerun',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python -m unittest discover -s research/tests -p "test_*forest*.py" -v',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python -m unittest discover -s research/tests -p "test_assess_gpu_shape_microbatch_v*.py" -v',
        '```', '',
        'v7 호출 예시(모델/source 검증과 입력 적재는 타이머 밖의 준비 단계):', '',
        '```python',
        'from assess_gpu_shape import load_verified_bundle',
        'from assess_gpu_shape_microbatch_v7 import assess_gpu_shape_microbatch_v7',
        'bundle = load_verified_bundle(model_path, source_path)',
        'outputs = assess_gpu_shape_microbatch_v7(bundle, requests)',
        '```', ''])
    report = ROOT / 'reports/decision_inference_row_partition_findings_2026_10_05.md'
    report.write_text('\n'.join(lines))
    for name in names:
        root = ROOT / 'artifacts' / name
        files = [{'path': str(p.relative_to(root)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                 for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'ARTIFACT_MANIFEST.json']
        (root / 'ARTIFACT_MANIFEST.json').write_text(json.dumps({'files': files}, indent=2)+'\n')
    print(json.dumps({'report': str(report), 'status': adoption['status']}))


if __name__ == '__main__':
    main()
