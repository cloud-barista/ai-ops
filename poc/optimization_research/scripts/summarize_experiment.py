"""Create shareable plots, example input and a compact Korean results report."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from train_performance import ROOT, FEATURES


def main():
    artifact = ROOT/'artifacts/performance_v1'
    report_dir = ROOT/'reports'
    report_dir.mkdir(exist_ok=True)
    metrics = json.loads((artifact/'metrics.json').read_text())
    predictions = pd.read_parquet(artifact/'predictions.parquet')
    primary = predictions[predictions.evaluation=='test']
    fig, axes = plt.subplots(1, 2, figsize=(10,4), constrained_layout=True)
    labels = ['Batch p95 latency (ms)', 'Throughput (examples/s)']
    for ax, target, label in zip(axes,metrics['target_columns'],labels):
        for device,color in [('cpu','#d97706'),('cuda','#2563eb')]:
            subset=primary[primary.device==device]
            ax.scatter(subset[target],subset['predicted_'+target],s=12,alpha=.45,label=device,color=color)
        low=min(primary[target].min(),primary['predicted_'+target].min())
        high=max(primary[target].max(),primary['predicted_'+target].max())
        ax.plot([low,high],[low,high],color='black',linestyle='--',linewidth=1)
        ax.set(xscale='log',yscale='log',xlabel='Measured '+label,ylabel='Predicted '+label)
        ax.legend()
    fig.suptitle('Held-out workload groups: same host, known models')
    fig.savefig(report_dir/'performance_v1.png',dpi=180)
    plt.close(fig)
    data=pd.read_parquet(ROOT/'data/audit_v1/measurements.parquet')
    example=data[data.execution_id==primary.iloc[0].execution_id][FEATURES+['host','device_name']].iloc[:1]
    (artifact/'example_input.json').write_text(example.to_json(orient='records',indent=2)+'\n')
    lines=['# 데이터 진단 및 첫 연구 결과','',
      '## 선정 과제','',
      '**같은 호스트에서 기존 모델의 입력·자원 구성이 달라질 때 배치 지연과 처리량을 예측하는 지도학습**을 진행했다.',
      '인프라 후보별 성능 예측의 기초 모델이며, 실제 VM 배포·스케일링 정책을 학습한 결과는 아니다.','',
      '## 데이터 진단 결론','',
      '- 원본 7개 DB 9,591건에서 학습 후보 9,445건 확보. 같은 host/execution_id 중복은 0건.',
      '- 제외 146건: 중단 회차 136건과 실패 회차 10건. 실패 유형은 OSError이며 자원 부족 정답으로 사용하지 않았다.',
      '- 모든 실행에 before 관측이 있다. 노드 telemetry는 이번 예측 입력에서 제외했다.',
      '- local-exhaustive는 4,856건/575 workload 그룹/9개 모델로 첫 실험에 가장 적합하다.',
      '- 주 데이터의 CPU는 256건, GPU는 4,600건이다. CPU 학습 범위와 표본 수가 부족하다.',
      '- 반복 수집 live DB의 2,640건은 10개 workload 그룹에 집중된다. 행 수를 독립 조건 수로 해석하지 않았다.',
      '- 원본 수집 기간은 2026-09-10~11로 짧다. 장기 운영 부하나 실제 scaling 전환에 대한 학습 근거는 부족하다.',
      '- 자연어 정답 Profile, 실제 CSP 가격, UPDATE 전후 결과는 이번 실측 회귀 데이터의 대상이 아니다.','',
      '## 학습·평가','',
      f'- 검증셋으로 선택한 모델: {metrics["selected_model"]}. RF/Extra Trees 각 200 trees, seed=42, CPU worker 2개.',
      '- train 3,348건/396그룹, validation 748건/89그룹, test 760건/90그룹.',
      '- 모델별 그룹 분할로 동일 입력 조건의 CPU/GPU·replica 후보와 반복 실험이 분할을 넘지 않도록 했다.',
      '- 타깃은 배치 p95(ms)와 examples/sec. 실행 이후 성과값을 입력으로 사용하지 않았다.',
      '- 테스트 결과를 보고 모델을 재선정하거나 튜닝하지 않았다. 지원 범위 밖 모델/장치 조합은 추론 시 ABSTAIN한다.','',
      '| 평가셋 | 사용/후보 행 | 지연 중앙 APE | 처리량 중앙 APE |',
      '| --- | ---: | ---: | ---: |']
    for name,value in metrics['evaluations'].items():
        total=metrics['external_coverage'].get(name,{}).get('eligible_rows',value['rows'])
        a=value['model']['latency_p95_ms']['median_absolute_percentage_error']
        b=value['model']['examples_per_second']['median_absolute_percentage_error']
        lines.append(f'| {name} | {value["rows"]}/{total} | {a:.2f}% | {b:.2f}% |')
    lines+=['','APE는 절대백분율오차다. 외부 평가셋은 학습된 모델/revision·장치 조합과 수치 범위 안에 있는 행만 사용했다. 제외 행을 포함한 전체 데이터 성능으로 해석하지 않는다.','',
      '주 test에서 모델·device별 중앙값 기준선의 중앙 APE는 지연 73.71%, 처리량 66.01%다. 학습 모델은 각각 16.99%, 10.41%다.',
      '다만 CPU의 두 타깃 평균 절대 로그오차는 0.614, GPU는 0.198로 차이가 크다. 외부 novel 조건에서는 CPU 0.859, GPU 0.319다. 전체 평균만으로 CPU/GPU 모두 충분하다고 판단하지 않는다.','',
      '![측정값과 예측값](performance_v1.png)','',
      '## 다음 개발 우선순위','',
      '1. GPU에서 검증된 응용·형상 범위부터 예측 구간을 보정하고 SLO 기반 후보 선택을 평가한다.',
      '2. CPU는 기존 다른 캠페인의 프로토콜·중복 조건을 검증한 뒤 데이터 보강을 검토한다. 추가 수집이 필요하면 CPU 조건을 우선한다.',
      '3. replica 1/2 구성의 정적 비교와 실제 scale 전환 평가는 분리한다. 전환 지연·실패·부하 trace를 확보한 뒤 제어 연구로 확장한다.',
      '', '이번 결과는 오프라인 기준선 실험 완료다. 운영 적용, VM 실행, 자동 스케일링은 수행하지 않았다.','']
    (report_dir/'diagnosis_and_first_experiment.md').write_text('\n'.join(lines))


if __name__=='__main__':
    main()
