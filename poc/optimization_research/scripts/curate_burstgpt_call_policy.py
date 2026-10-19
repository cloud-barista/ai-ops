"""All-scenario report, numerical constraints and reproducible new-only release."""
import argparse,hashlib,json,shutil
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path('/home/researcher/ai-mcmp/research')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def fmt(x):return '미정의' if x is None else f'{x:.6f}'
def main(s):
 out=s/'artifacts/burstgpt_call_policy_v1';result=json.loads((out/'results.json').read_text());v=json.loads((out/'verification.json').read_text());inp=json.loads((out/'INPUT.json').read_text())
 assert v['status']=='VERIFIED' and v['policy_scenarios_checked']==36
 scenarios=result['scenarios'];passed=sum(r['gate_vs_selected_threshold']=='RETROSPECTIVE_SIMULATION_PASS' for r in scenarios.values())
 isolated_pass=sum(r['gate_vs_same_forecast_threshold']=='RETROSPECTIVE_SIMULATION_PASS' for r in scenarios.values())
 primary={k:r for k,r in scenarios.items() if r['selection']['factor']==1.}
 primary_pass=sum(r['gate_vs_selected_threshold']=='RETROSPECTIVE_SIMULATION_PASS' for r in primary.values())
 lines=['AI-MCMP: 수요 예측을 준비 지연·대기·용량 비용 판단에 연결한 연구',
 '2026-10-06 | BurstGPT 기록 호출의 시간순 유체 시뮬레이션; 실제 배포·스케일 효과 아님','',
 '1. 결론과 경희대 담당 항목',
 f'전체36조건 중 {passed}조건이 비용·부족·대기·방향 전환의 공동 기준을 통과했다. 주 용량 조건12개 중 통과는{primary_pass}개다.',
 f'전체 조건 통과={result["overall_policy_pass"]}, 운영 채택=false다. 유리한 민감도 조건으로 실패한 주 조건을 대체하지 않는다.',
 f'같은 수요 예측을 사용하는 threshold와 비교하면 새 판단 규칙의 통과는 {isolated_pass}/36이다. 따라서 이번 규칙 자체의 개선은 확인하지 못했다.',
 '민감도 조건의 통과는 비교 예측기와 제어 규칙이 함께 다른 결과다. 이를 새 제어 규칙의 고유 기여로 표시하지 않는다.',
 '이번 연구는 경희대의 배포·스케일 판단 메커니즘과 운영 피드백 연구에 대응한다.',
 '새 학습 모델은0개다. 기존에 학습·검증한 호출 예측기를 고정하고 그 예측을 조치 판단 규칙에 연결했다.',
 '실제 DEPLOY/UPDATE, VM 생성·삭제, 기관 Adapter나 사용자 SLO의 개선을 주장하지 않는다.','',
 '2. 왜 이 연구를 했는가',
 'BurstGPT 상대 수요 학습에서는12조건 중3조건의 예측 손실이 개선됐지만, 예측이 좋아졌다고 스케일 정책의 비용과 대기가 함께 좋아지는 것은 아니다.',
 '기존 정책은 예측과 현재 대기량이 용량보다 크면 증설한다. 준비 지연·추가 용량 비용을 같은 기준으로 비교하는 별도 판단 단계가 필요했다.',
 '이번 규칙은 현재 예측으로 앞선 몇 구간을 가정해 KEEP/증설/허용된 축소의 비용을 계산한 뒤 가장 작은 값을 선택한다.',
 '유한 구간의 용량·대기 비용 평가와 같은 원리는 기존 제어 연구에도 있다. 새 모델 구조나 최초의 제어 알고리즘을 발명했다고 주장하지 않는다.',
 '우리 연구 구성은 고정 수요 예측, 완료 피드백, 준비 지연·pending 상태, 비용·대기 목적함수와 동등한 Guard를 연결하고 모든 조건을 비교한 절차다.','',
 '3. 자료·모델·시간순 분할',f'기존 BurstGPT_3.csv에서 상대일225~314의 {inp["selected_rows"]:,}개 기록을90일·25,920개5분 구간으로 집계했다.',
 'ChatGPT/GPT-4와 API/Conversation 로그의4집단을 분리했다. 실패·취소·토큰 결측 여부로 호출 기록을 제외하지 않았다.',
 '첫 timestamp가 있는 부분 구간은NaN이다. 그 외 빈 구간은0개 기록이라는 뜻이며, 실제 무수요나 모니터링 연속성을 증명하지 않는다.',
 'Timestamp는 상대 초다. 절대 UTC날짜·요일을 부여하지 않았다. 원본은 삭제·변경하지 않았고 추가 다운로드도 없다.',
 '모델은 burstgpt_sparse_calls_v1의24개 HGB다. 학습은225~244이며 현재 연구에서 재적합하거나 교체하지 않았다.',
 '정책 설정 선택은245~254의10일이다. 각 후보를 같은 학습 기반 초기 용량·대기0 조건에서 독립 재생했다.',
 '선택을 저장한 뒤, 최종 정책은245~254에 threshold를 사용하고255부터 선택한 lookahead 규칙을 적용했다.',
 '255~284는 상태·피드백을 이어가는30일 구간이다. 설정 선택에 사용하지 않았다.',
 '시험은285~314의 다음30일이다. 시작할 때 앞선 구간의 대기량·active/pending 용량을 초기화하지 않았다.',
 '원본 전체는 다른 토큰·지연 연구에서 이미 열람됐고255~284의 호출 예측 결과도 앞서 봤다. 독립 블라인드 일반화 시험으로 부르지 않는다.','',
 '4. 가상 처리 용량과 예측의 의미',
 '각 집단의 학습5분 호출 수q90을v=max(q90,1)로 두고, 한 가상slot의 처리 용량은C=(v/4)×factor다. factor는0.5/1/2다.',
 '초기slot 수는ceil(v/C)를1~8로 제한했다. 시작 대기0은 연구 가정이다.',
 '이 처리 용량은 같은 크기의 호출이라는 가정 아래 정의한 유체 단위다. 분수 호출 처리를 허용하며 실제 요청 완료를 복원하지 않는다.',
 'slot은GPU·VM·CPU코어가 아니다. 호출마다 토큰·이미지·처리 시간이 다를 수 있어 실제 자원 용량으로 환산할 수 없다.',
 '준비 지연은1/2/6구간(5/10/30분), 한 번에pending1개, active최소1·최대8이다. pending은 요청 직후부터 비용에 포함한다.',
 '원점t의 예측은t~t+h까지 최대5분 호출 수다. 원시 모델의 안정화 상대q90에 완료된 양수 잔차q90을 더한다.',
 '잔차는245 이후의 최대288원점 중96개 이상을 쓰고, i+h+1에서 끝난 정답만 사용한다. 즉시 전달은 가정이며 실제available_at은 없다.',
 '동일 특징의 절대HGB, 학습 가중 상대q90, 과거1시간 최대/q90/평균에도 동일한 피드백을 적용했다.','',
 '5. 판단 규칙과 우리의 구현',
 '공통Guard: pending이면KEEP, cooldown2구간, 축소는낮은 수요3회 연속·0.8여유·실제 용량 변화 후6구간 유지가 필요하다.',
 '기준 threshold: 예측+현재 대기량이active×C보다 크면SCALE_OUT, 공통 축소Guard가 성립하면SCALE_IN이다.',
 '새 판단 규칙: KEEP, 가능한SCALE_OUT, 축소Guard가 허용한SCALE_IN을 비교한다.',
 'L=h+6구간의 가상 미래를 계산하며 현재 최대수요 예측p가 매 구간 도착한다고 가정한다. 이 반복은 보수적 대리 가정이며 실제 미래 수요 정답이 아니다.',
 '증설한slot은h구간 뒤 활성화된다. 각 단계의대기는 Q_next=max(0,Q+p-active×C)다.',
 'J=Σrequested_slots + lambda×(ΣQ/C + L×마지막Q/C). 유한 구간 뒤로 대기 부채를 미루지 않도록 마지막 대기 벌점을 넣었다.',
 '가장 작은J의 행동을 반환한다. 동률이면KEEP를 우선한다. 실제 명령을 전송하지 않는다.',
 'lambda 후보0.25/1/4/16은 검증의 비용/5+대기면적/C로 선택했다. 동일 점수면 작은lambda를 선택했다.',
 '강한 threshold 비교기는6개 예측 방법 중 같은 검증 정책 목적값이 가장 작은 방법이다. 기준선도 예측·상태에 동일한 단위를 사용한다.',
 '주 모델 예측+threshold 비교도 남겨, 예측 방법 변경과 제어 규칙 변경의 영향을 구분한다.','',
 '6. 정책 채택 기준',
 '검증에서 선택한threshold와 비교해 비용은+1% 이내이고, 즉시 부족 비율·대기면적·최종 대기·방향 전환은 더 나쁘지 않아야 한다.',
 '동시에 비용5% 이상 감소 또는 기준선 부족이 양수일 때 부족5% 이상 감소가 있어야 한다.',
 '전체 성과 판정은36조건 모두 통과해야 한다. 주 용량factor1의12조건과 나머지 민감도24조건을 모두 보고한다.',
 '방향 전환은 채점 구간에 발생한증설/축소 행동 사이의 변경 횟수다. 채점 시작 전 행동과의 경계 전환은 이 지표에 포함되지 않는다.',
 '시험 종료 뒤pending의 추가 비용·실제 준비 완료는 평가하지 않았다. 종료 active/pending 수를별도 end_state에 보존했다.','',
 '7. 모든 조건의 결과',
 '집단 | 준비분 | factor | 선택비교기 | lambda | 비용 기준선→우리 | 부족 기준선→우리 | 대기면적 기준선→우리 | 전환 기준선→우리 | 판정']
 rows=[];daily=[]
 for key,r in scenarios.items():
  sel=r['selection'];a=r['metrics']['lookahead_primary'];b=r['metrics']['selected_threshold']
  lines.append(f"{sel['cohort']} | {sel['horizon']*5} | {sel['factor']} | {sel['baseline_forecast']} | {sel['queue_weight']} | {b['billed_slot_minutes']:.0f}→{a['billed_slot_minutes']:.0f} | {b['instant_shortfall_fraction']:.3%}→{a['instant_shortfall_fraction']:.3%} | {b['queue_area_calls_bins']:.3f}→{a['queue_area_calls_bins']:.3f} | {b['direction_reversals']}→{a['direction_reversals']} | {r['gate_vs_selected_threshold']}")
  failures=[]
  if a['billed_slot_minutes']>1.01*b['billed_slot_minutes']+1e-9:failures.append('cost>+1%')
  if a['instant_shortfall_fraction']>b['instant_shortfall_fraction']+1e-12:failures.append('shortfall_worse')
  if a['queue_area_calls_bins']>b['queue_area_calls_bins']+1e-6:failures.append('queue_area_worse')
  if a['final_queue_calls']>b['final_queue_calls']+1e-6:failures.append('final_queue_worse')
  if a['direction_reversals']>b['direction_reversals']:failures.append('reversals_worse')
  benefit=a['billed_slot_minutes']<=.95*b['billed_slot_minutes'] or b['instant_shortfall_fraction']>0 and a['instant_shortfall_fraction']<=.95*b['instant_shortfall_fraction']
  if not benefit:failures.append('gain<5%')
  for method,m in r['metrics'].items():rows.append({'scenario':key,'cohort':sel['cohort'],'delay_minutes':sel['horizon']*5,'capacity_factor':sel['factor'],'method':method,**m})
  rows[-3]['gate_vs_selected']=r['gate_vs_selected_threshold'];rows[-3]['failure_reasons']=';'.join(failures)
  for day,methods in r['daily'].items():
   for method,m in methods.items():daily.append({'scenario':key,'relative_day':int(day),'method':method,**m})
 lines+=['','8. 시험 시작·종료 상태',
 'carry_in에는 첫 시험 행동 전의 대기·active/pending 상태를 저장했다. end_state에는 시험 마지막 행동 이후의 상태를 저장했다.',
 '중간 30일의 준비 완료·증설/축소·대기 처리는 다음 시험 상태에 이어진다. 시험 시작 때 대기를 0으로 초기화하지 않았다.',
 '모든 상태는 results.json에 남겼다. 일별 파일의 최종 대기는 당일 끝에 남은 대기이며, 다음 날에 이어진다.','',
 '9. 같은 고정 모델의 다음 30일 수요 점수','집단 | 준비분 | 모델 pinball | 원래 검증 선택 비교기 | 비교 pinball | 손실 감소 | 모델 피복']
 parent=json.loads((ROOT/'artifacts/burstgpt_sparse_calls_v1/SELECTION.json').read_text())['validation_only']
 for key,m in result['forecast_test_metrics'].items():
  name,h=key.rsplit(':',1);chosen=parent[key]['baseline'];a=m['stabilized'];b=m[chosen]
  gain=1-a['pinball']/b['pinball']
  lines.append(f"{name} | {int(h)*5} | {a['pinball']:.6f} | {chosen} | {b['pinball']:.6f} | {gain:.2%} | {a['coverage']:.2%}")
 lines+=['원래 호출 예측의 검증 선택을 그대로 대조했다. 정책 검증 목적값으로 선택한 비교기와는 다를 수 있다.',
 '이 표는 모든 방법을 보존한 추가 시간순 점수다. 예측 손실이 낮다고 정책 효과가 좋다고 해석하지 않는다.',
 '앞선 30일에서 개선됐던 GPT-4 API의 준비 5·10분은 다음 30일에서도 원래 비교기 대비 손실이 낮았다. ChatGPT API의 준비 30분은 감소율이 5%에 못 미쳤다.',
 '앞선 성과가 모든 조건에서 재현됐다는 결론은 없으며, 두 기간의 개선율을 합산하지 않는다.','',
 '10. 검증',f"원본 {v['raw_records_recounted']:,}기록을 Decimal timestamp로 독립 재집계했다.",
 f"수동 과거 특징·목표·완료 피드백으로 {v['forecast_rows_checked']:,}예측 행을 확인했다. 앞선 60일의 원시·보정 예측은 기존 아티팩트와 정확히 같다.",
 '수동 특징 계산은 완전한 과거 이력에서 1e-13 정밀도로 검사했다. 모델에는 고정된 원래 특징 계산을 사용한다. 이전에 확인한 부동소수점·트리 경계 문제를 고려했다.',
 f"독립 정책 상태·목적값 구현으로 36개 선택과 {v['policy_test_rows_checked']:,}정책 행, {v['daily_policy_metrics_checked']:,}일별 방법 지표를 검산했다.",
 '가상 처리량·대기량의 보존, 준비 시각·pending 비용·1~8 용량 경계, 시험 시작·종료 상태, 선택·판정을 검사했다.',
 '예측 12조건과 정책 108재생에서 미래 수요를 바꿔도 첫 시험 행동까지의 과거 특징·예측·상태·행동이 유지되는지 확인했다.',
 '정책 테스트 9개가 통과했다. 신규 모델 재적합은 0개이며 기존 24모델과 원본·helper 해시는 같다.','',
 '11. 최대 용량으로도 피할 수 없는 부족: 사후 진단',
 '각 조건에서 시험 시작 대기를 0으로 두고 최대 8slot을 처음부터 계속 제공하는 유리한 대조를 계산했다.',
 '모든 유효 정책은 용량이 8slot 이하이고 초기 대기가 음수가 아니므로, 이 대조의 유체 대기는 정책이 달성할 수 있는 하한이다.',
 '즉시 부족의 하한은 sum(max(기록 도착량-8*C,0))/sum(기록 도착량)이다. 대기 요청과 사용자 SLO를 측정한 값은 아니다.',
 '아래 하한은 시험 미래 수요를 사용한 사후 진단이다. 온라인 입력·모델 학습·설정 선택·채택 기준에는 넣지 않았다.',
 '집단 | 준비분 | factor | 최대8slot 부족 하한 | 우리 부족 | 정책이 하한을 넘는 정도(pp)']
 ceiling=json.loads((out/'capacity_ceiling_diagnosis.json').read_text())
 for key,d in ceiling['scenarios'].items():
  sel=scenarios[key]['selection']
  if sel['factor']!=1.:continue
  a=scenarios[key]['metrics']['lookahead_primary']
  lines.append(f"{sel['cohort']} | {sel['horizon']*5} | 1 | {d['unavoidable_instant_shortfall_fraction_at_always_eight_slots']:.2%} | {a['instant_shortfall_fraction']:.2%} | {d['policy_comparisons']['lookahead_primary']['excess_shortfall_percentage_points_over_bound']:.3f}")
 lines+=['GPT-4 API 주 용량은 최대8slot을 유지해도 즉시 부족 하한이 약71.3%다. 이 가정에서는 판단 규칙만 고쳐 부족을 없앨 수 없다.',
 '민감도에서 통과한 GPT-4 API 준비30분·factor2도 실제 기록을 기준으로 부족 비율이 약60.3%이고 최종 대기18,799호출이 남았다.',
 '같은 예측을 쓰는 threshold와 비교하면 그 조건도 공동 기준에 실패했다. 상대 개선·시뮬레이션 PASS를 적절한 용량이나 사용자 SLO 달성으로 바꾸지 않는다.',
 '추정된 단일 최고 구간을 처리할 slot 수는 사후 가상 단위다. 실제 최소 GPU·CPU·VM 사양 라벨로 사용하지 않는다.',
 f'반복식과 별도 누적합 식으로 하한을 확인하고 {ceiling["policy_rows_bound_checked"]:,}정책 행의 대기·부족이 하한보다 작지 않음을 확인했다.',
 '이 진단은 capacity_ceiling_diagnosis.json에 입력 해시와 함께 저장했다. 원래 정책 판정·모델·설정은 바꾸지 않았다.','',
 '12. 한계와 다음 연구',
 '이 자료에는 실제로 접수된 scale 명령·VM/앱 READY·트래픽 전환·사용자 SLO·가격이 없다. 시뮬레이션 PASS도 실제 실행 효과의 증거가 아니다.',
 '모든 호출의 작업량이 같다는 가정과 한 slot의 고정 용량을 사용했다. 호출 길이·GPU 메모리·배치·캐시·이미지·오류 비용을 반영하지 못한다.',
 '최대 수요 예측을 매 미래 구간에 반복하는 가정은 실제 도착 패턴과 다를 수 있다. 이 가정으로 정책을 선택하며 실제 시간대별 미래 수요는 보지 않는다.',
 '현재 수준은 연구 판단 규칙과 시뮬레이션이다. 자연어 요구 분석, CPU/RAM/GPU/노드 형상 전체, DEPLOY/UPDATE, 기관 연동의 완료를 대신하지 않는다.',
 '다음에는 실측 앱 처리 용량·준비 지연·사용자 SLO에 연결된 조치 결과, 또는 분포 예측을 이용한 여러 도착 시나리오가 필요하다.',
 '기존 정적 추천·위험 보정·수요 예측의 개선 범위는 유지하고 이번 공동 정책 판정과 합산하지 않는다.','',
 '13. 위치','판단규칙: /home/researcher/ai-mcmp/research/scripts/call_capacity_control.py',
 '평가: /home/researcher/ai-mcmp/research/scripts/evaluate_burstgpt_call_policy.py',
 '검증: /home/researcher/ai-mcmp/research/scripts/verify_burstgpt_call_policy.py',
 '원본: /home/researcher/ai-mcmp/research/data/external/burstgpt_v2/BurstGPT_3.csv',
 '모델: /home/researcher/ai-mcmp/research/artifacts/burstgpt_sparse_calls_v1/',
 '집계·예측·선택·정책·점수·검증: /home/researcher/ai-mcmp/research/artifacts/burstgpt_call_policy_v1/',
 '공식원본: https://github.com/HPMLL/BurstGPT/releases/tag/v2.0; Wang etal.,KDD2025,DOI10.1145/3711896.3737413,CC BY4.0']
 (s/'reports/burstgpt_call_policy_findings_2026_10_06.txt').write_text('\n'.join(lines)+'\n')
 pd.DataFrame(rows).to_csv(out/'scenario_method_metrics.csv',index=False);pd.DataFrame(daily).to_csv(out/'daily_policy_metrics.csv',index=False)
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 cohorts=['ChatGPT|API log','ChatGPT|Conversation log','GPT-4|API log','GPT-4|Conversation log'];labels=[n.replace('|',' / ').replace(' log','')+f' / {h*5}min' for n in cohorts for h in (1,2,6)]
 cost=np.empty((12,3));short=np.empty((12,3));gates=np.empty((12,3))
 for i,(name,h) in enumerate((n,h) for n in cohorts for h in (1,2,6)):
  for j,factor in enumerate((.5,1.,2.)):
   r=scenarios[f'{name}:{h}:{factor}'];a=r['metrics']['lookahead_primary'];b=r['metrics']['selected_threshold']
   cost[i,j]=100*(a['billed_slot_minutes']/b['billed_slot_minutes']-1);short[i,j]=100*(a['instant_shortfall_fraction']-b['instant_shortfall_fraction']);gates[i,j]=r['gate_vs_selected_threshold']=='RETROSPECTIVE_SIMULATION_PASS'
 fig,axes=plt.subplots(1,3,figsize=(13,7),sharey=True,gridspec_kw={'width_ratios':[1.2,1.2,1]})
 for ax,values,title in zip(axes,[cost,short,gates],['Billed proxy change (%)','Shortfall change (percentage points)','Joint policy gate']):
  bound=max(float(np.abs(values).max()),.01)
  ax.imshow(values,aspect='auto',cmap='RdYlGn_r' if title!='Joint policy gate' else 'RdYlGn',vmin=-bound if title!='Joint policy gate' else 0,vmax=bound if title!='Joint policy gate' else 1)
  ax.set_xticks(range(3),['0.5','1 (primary)','2']);ax.set_xlabel('Hypothetical capacity factor');ax.set_title(title,fontsize=10)
  for i in range(12):
   for j in range(3):ax.text(j,i,('PASS' if values[i,j] else 'NO') if title=='Joint policy gate' else f'{values[i,j]:+.2f}',ha='center',va='center',fontsize=9)
 axes[0].set_yticks(range(12),labels);fig.suptitle(f'BurstGPT recorded calls: lookahead vs validation-selected threshold\nRelative days285-314; controller-only passes {isolated_pass}/36; primary scenarios {primary_pass}/12\nHomogeneous fluid capacity; no operational adoption',fontsize=10)
 fig.tight_layout();fig.savefig(out/'comparison.png',dpi=150);fig.savefig(out/'comparison.pdf');plt.close(fig)
 addendum=['AI-MCMP: 스케일 판단 연구 진행 보강 | 2026-10-06',
 '이전상태보고서·수요모델결과는변경하지않고이번판단규칙연구를추가한다.',
 '기존24모델을그대로사용해준비지연·대기·추상용량비용을비교하는KEEP/SCALE_OUT/SCALE_IN규칙을구현했다.',
 f'4집단×3준비지연×3가상용량의36조건중{passed}조건이공동기준통과. 주용량12조건의통과는{primary_pass}개. 전체통과={result["overall_policy_pass"]}.',
 f'같은 수요 예측을 쓰는 threshold 대비 새 규칙의 공동 기준 통과는 {isolated_pass}/36이다. 제어 규칙 자체의 개선을 확인한 결과는 아니다.',
 'GPT-4 API 주 용량은 최대8slot을 처음부터 제공해도 즉시 부족 하한이 약71.3%다. 실제 앱 처리 용량·허용 자원 범위가 필요한 상태다.',
 '연구수준은호출유체시뮬레이션이다.실제action/READY/트래픽전환/사용자결과·가격·기관연동은미검증이며운영채택은false다.',
 '상세결과:reports/burstgpt_call_policy_findings_2026_10_06.txt']
 (s/'reports/ai_mcmp_rr_addendum_call_policy_2026_10_06.txt').write_text('\n'.join(addendum)+'\n')
 snap=out/'source_snapshot';snap.mkdir()
 for folder in ('scripts','tests','reports'):
  for p in sorted((s/folder).glob('*')):
   if p.is_file() and p.suffix in ('.py','.json'):
    dest=snap/folder/p.name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(p,dest)
 files=[p for folder in ('scripts','tests','reports','artifacts') for p in sorted((s/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
 manifest={'files':{str(p.relative_to(s)):sha(p) for p in files},'new_only':True,'raw_data_preserved':True,'operational_adoption':False,
  'policy_scenarios':36,'policy_passed_scenarios':passed,'primary_passed_scenarios':primary_pass,'isolated_controller_passed_scenarios':isolated_pass,'new_models_trained':0}
 with (s/'reports/burstgpt_call_policy_publication_v1.json').open('x') as f:json.dump(manifest,f,indent=2)
 print('CURATED',len(files),'files;',passed,'pass;',primary_pass,'primary pass')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);main(p.parse_args().stage)
