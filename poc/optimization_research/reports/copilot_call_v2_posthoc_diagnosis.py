"""Descriptive diagnostics after test exposure; no fitting or adoption changes."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
s=Path('/tmp/copilot_call_v2')
f=pd.read_parquet(s/'artifacts/copilot_call_demand_v2/forecasts.parquet')
result={}
for h in (1,2,6):
 rows={}
 for name,lo,hi in [('train',12,864),('validation',864,1152),('test',1152,1440)]:
  ids=np.arange(lo,hi-h)
  target=f[f'd{h}_target'].to_numpy()[ids]
  scale=f.scale.to_numpy()[ids]
  pred=f[f'd{h}_new'].to_numpy()[ids]
  base=f[f'd{h}_baseline'].to_numpy()[ids]
  ratio=target/scale
  e=target-pred
  rows[name]={'rows':len(ids),'relative_peak_quantiles':{str(q):float(np.quantile(ratio,q)) for q in (.5,.9,.95,.99)},
   'model_coverage':float(np.mean(target<=pred)),
   'selected_baseline_coverage':float(np.mean(target<=base)),
   'model_pinball':float(np.maximum(.9*e,-.1*e).mean()),
   'underpredicted_origins':int(np.sum(target>pred))}
 result[str(h*5)]=rows
out={'posthoc':True,'no_models_fitted':True,'test_previously_seen':True,'operational_adoption':False,'delays_minutes':result}
(s/'reports/copilot_call_v2_posthoc_diagnosis.json').write_text(json.dumps(out,indent=2)+'\n')
lines=['Copilot 학습 기간 확대 모델의 사후 분포·피복 진단','2026-10-06 | 시험 결과 열람 뒤의 기술 통계; 모델 선택·채택 근거가 아님','',
 '아래 학습 피복은 학습에 쓴 행의 적합 결과다. 독립 성능으로 해석하지 않는다.',
 '상대 최대 수요는 준비 완료 직후 구간까지의 최대 호출 수를 직전 1시간 평균으로 나눈 값이다.',
 '검증·시험에서 그 비율의 꼬리가 커지는지와 모델의 과소예측 빈도를 관찰한다.',
 '기준선은 기존 검증으로 선택된 방법을 그대로 사용했다. 새 모델을 적합하지 않았다.','',
 '준비분 | 구간 | 행 | 상대수요 q90 | 상대수요 q99 | 모델 피복 | 기준선 피복 | 과소예측 횟수']
for h,rows in result.items():
 for split,v in rows.items():
  lines.append(f"{h} | {split} | {v['rows']} | {v['relative_peak_quantiles']['0.9']:.3f} | {v['relative_peak_quantiles']['0.99']:.3f} | {100*v['model_coverage']:.2f}% | {100*v['selected_baseline_coverage']:.2f}% | {v['underpredicted_origins']}")
lines+=['','이 기술 통계는 시간대·요일·선택 세션 표본의 변화가 함께 있는 관측이다. 개별 원인의 인과 효과를 분리하지 않는다.',
 '모델의 q90 예측이 실제로 90%를 덮는다는 보장은 없고, 기존 시험 채택 결과는 그대로 NO_GO다.',
 '특정 보정법을 이 시험 점수로 고른 뒤 같은 시험을 독립 확인으로 제시하지 않는다.',
 '다음 방법을 비교하려면 개발 구간과 새 확인 구간을 구분하고, 완료된 정답만 이용한 적응·보정과 강한 단순 기준선을 함께 평가해야 한다.']
(s/'reports/copilot_call_v2_posthoc_diagnosis.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))

# Preserve the measured effect of adding prior session partitions.
a=pd.read_parquet('/home/researcher/ai-mcmp/research/artifacts/copilot_transfer_v1_1/complete_call_counts_and_known_tokens.parquet').recorded_llm_calls.to_numpy()
b=f.y.to_numpy()[576:]
d=b-a
assert np.all(d>=0)
change={'posthoc':True,'comparison':'June5-7 counts from new five-partition aggregate versus original three-partition aggregate',
 'changed_bins':int(np.count_nonzero(d)),'total_added_recorded_calls':int(d.sum()),'negative_differences':int(np.sum(d<0)),
 'by_date':{f'2026-06-{day:02}':{'changed_bins':int(np.count_nonzero(d[i*288:(i+1)*288])),'added_calls':int(d[i*288:(i+1)*288].sum())} for i,day in enumerate((5,6,7))}}
(s/'reports/copilot_call_v2_aggregate_change_audit.json').write_text(json.dumps(change,indent=2)+'\n')
