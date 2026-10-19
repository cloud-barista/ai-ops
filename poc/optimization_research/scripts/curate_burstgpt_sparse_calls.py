"""Complete all-condition report with subgroup/daily diagnostics; no selection changes."""
import argparse,hashlib,json,shutil
from pathlib import Path
import pandas as pd
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(s):
 out=s/'artifacts/burstgpt_sparse_calls_v1';results=json.loads((out/'results.json').read_text());verify=json.loads((out/'verification.json').read_text())
 assert verify['status']=='VERIFIED' and verify['models_refitted']==24 and verify['condition_gates_checked']==12
 inp=json.loads((out/'INPUT.json').read_text());selection=json.loads((out/'SELECTION.json').read_text())['validation_only']
 lines=['AI-MCMP: 간헐적인 기록 호출 수요를 위한 서비스별 학습','2026-10-06 | 시간순 후향 연구; 실제 VM·배포 효과 검증 아님','',
 '1. 문제와 경희대 담당 항목',
 '기존 Copilot 외부 전이는 BurstGPT 12조건 모두 지원 기준에 미달했다. 이전1시간 평균<1호출이면 보류하는 설계가 저수요 기록에서 적용되지 않았다.',
 '기존 전이 판정과 지원 기준은 유지했다. 이 연구에서는 별도 호출 수요 문제를 정의하고 BurstGPT에서 서비스별 모델을 새로 학습했다.',
 '출력은 배포·스케일 판단에 공급할 수요 입력 신호다. 실제 GPU/VM 수나 조치 자체를 학습하지 않았다.','',
 '2. 자료와 분할',f"기존 원본 BurstGPT_3.csv의 상대일225~284, {inp['selected_rows']:,}개 요청 기록을4코호트로 집계했다.",
 'ChatGPT/GPT-4와 API/Conversation 로그를 구분했다. 원본 Timestamp는 상대 초이므로 절대 UTC날짜·요일을 부여하지 않았다.',
 '선택한60일을5분17,280구간으로 집계했다. 범위 첫 부분 구간은NaN으로 처리하고, 최초 완전한1시간 이력 뒤부터 학습한다.',
 '빈 구간의0은0개 기록이라는 의미다. 원본 heartbeat가 없어 실제 무수요나 모니터링 연속성의 증거로 부르지 않는다.',
 '학습225~244(20일), 검증245~254(10일), 시험255~284(30일). 학습·검증 목표가 다음 분할 경계를 넘지 않게 제외했다.',
 '기존 원본은 출력토큰·지연 등 다른 연구에서 이미 열람했다. 새 독립 블라인드 시험이 아닌 시간순 후향 실험이다.',
 '원본 추가 다운로드·삭제·변경 없이 기존 자료에서 기간을 골랐다.','',
 '3. 우리의 연구 구성과 모델',
 '주 방법은 s_t=max(직전완료12구간의 기록 호출 평균,1)로 안정화한 상대 최대 수요q90이다.',
 'u_t=max(y_t,...,y_(t+h)), h=1/2/6. 준비시간5/10/30분에 타깃 관측창은10/15/35분이다.',
 'u_t/s_t를HGB 분위수 회귀로 학습하고 예측 후s_t를 곱한다. 표본 가중치s_t/학습평균(s)는 절대 호출pinball과 상대 손실을 대응시킨다.',
 '0·저수요 기록에도s>=1을 정의하지만, 미관측 입력의NaN을0으로 대체하지 않는다.',
 '특징10개: 직전1/2/3/12구간 비율, 과거1시간 최대/q90비율, 최근30분/안정화 이전30분 비율, log1p(1시간 평균),1시간0기록 비율, 마지막 양수 기록 이후 구간 수(최대288).',
 '시각·요일 특징은 쓰지 않는다. 마지막 양수 기록 이력은 시점 전에 관측한 값만 사용한다.',
 'HGB설정:loss=quantile,q=.9,max_iter150,lr=.05,max_leaf_nodes7,min_samples_leaf20,L2=1,earlystop=False,seed42.',
 '4코호트×3준비시간마다 상대 모델과 동일 특징의 절대 호출HGB를 각각 학습해 총24개 모델을 저장했다.',
 '절대HGB는 학습 비교기다. 추가 단순 비교기는 학습 가중 상대q90, 과거1시간 최대/q90/평균이다.',
 '각 방법 모두 같은 양수 잔차q90 피드백을 적용한다. 최근288원점 중96개 이상, 최초 비학습 원점부터 완료된 정답만 사용한다.',
 '정답은i+h+1에서 완료되며 즉시 전달된다고 가정했다. 실제available_at 계측은 없다.',
 '주 방법은 안정화 상대HGB로 사전 고정했다. 비교기는 검증의 보정pinball이 최저인 방법을 선택하고 모델 해시·선택을 저장한 뒤 시험 점수를 계산했다.',
 '새 학습기 아키텍처를 발명했다는 주장은 없다. 저수요를 지원하는 문제·정규화·과거 이력과 완료 피드백을 연결한 연구 구성이다.','',
 '4. 모든12조건의 시험 결과',
 '채택 기준: 선택된 동일 보정 비교기 대비pinball5% 이상 감소와피복85~95%를 동시에 요구한다.',
 '호출 수가 이산값이고0의 질량이 크면85~95% 기준을 만족하기 어려울 수 있다. 점수를 본 뒤 기준을 바꾸지 않았다.',
 '코호트 | 준비분 | 우리손실 | 비교기 | 비교손실 | 감소율 | 우리피복 | 판정']
 dailyrows=[];group_rows=[]
 for key,v in results['comparisons'].items():
  name,h=key.rsplit(':',1);a=v['strata']['all']['stabilized'];b=v['strata']['all'][v['selected_baseline']];gain=v['gain_vs_selected_equally_corrected_baseline']
  lines.append(f"{name} | {int(h)*5} | {a['pinball']:.6f} | {v['selected_baseline']} | {b['pinball']:.6f} | {gain:.2%} | {a['coverage']:.2%} | {v['gate']}")
  for day,methods in v['daily'].items():
   for method,m in methods.items():dailyrows.append({'cohort':name,'horizon':int(h),'relative_day':int(day),'method':method,**m})
  for group,methods in v['strata'].items():
   for method,m in methods.items():group_rows.append({'cohort':name,'horizon':int(h),'stratum':group,'method':method,**(m or {'rows':0})})
 passed=sum(v['gate']=='RETROSPECTIVE_SPARSE_PASS' for v in results['comparisons'].values())
 lines+=['',f'전체12조건 중 {passed}조건이 사전 기준을 통과했다. 전체 통과 판정은 '+str(results['all_conditions_pass'])+'.',
 '서로 다른 코호트의 개선율을 더하지 않는다. 유리한 조건으로 실패한 주 조건을 대체하지 않는다.',
 '통과 조건도 매일 또는 모든 집단에서 우세한 것은 아니다. 다음 사후 진단으로 검증 선택을 바꾸지 않는다.']
 for key,v in results['comparisons'].items():
  if v['gate']!='RETROSPECTIVE_SPARSE_PASS':continue
  scores=v['strata']['all'];best=min((n for n in scores if n!='stabilized'),key=lambda n:scores[n]['pinball'])
  gain=1-scores['stabilized']['pinball']/scores[best]['pinball']
  worse=sum(day['stabilized']['pinball']>day[v['selected_baseline']]['pinball'] for day in v['daily'].values())
  low=v['strata']['low_history'];a=low['stabilized'];b=low[v['selected_baseline']]
  lines.append(f"{key}: 시험 사후 최선 비교기={best}, 대비감소={gain:.2%}; 선택 비교기보다 손실이 높은 날={worse}/30.")
  if a and b and a['pinball']>b['pinball']:lines.append(f"주의: {key}의저수요 집단은 우리손실{a['pinball']:.6f}>비교손실{b['pinball']:.6f}. 전체 개선을저수요 개선으로 바꾸어 주장하지 않는다.")
 lines+=['',
 '5. 저수요·무기록·재도착 진단','코호트 | 준비분 | 집단 | 행 | 우리손실 | 비교손실 | 우리피복']
 for key,v in results['comparisons'].items():
  name,h=key.rsplit(':',1)
  for group in ('low_history','zero_history','zero_history_with_arrival','active_history'):
   a=v['strata'][group]['stabilized'];b=v['strata'][group][v['selected_baseline']]
   lines.append(f"{name} | {int(h)*5} | {group} | "+(f"{a['rows']} | {a['pinball']:.6f} | {b['pinball']:.6f} | {a['coverage']:.2%}" if a else '0 | 미채점'))
 lines+=['low_history는 판단 전1시간 평균<1,zero_history는그 평균=0,active_history는평균>=1이다.',
 'zero_history_with_arrival은평균=0인데 미래 목표>0인 집단이다. 미래 결과를 보고 만든 사후 진단 집단이며 온라인 정책 선택에 사용할 수 없다.',
 '모든 방법의 원시·보정 점수, 매일 점수와 각 집단의 점수는results.json 및daily_method_metrics.csv/stratum_method_metrics.csv에 보존했다.','',
 '6. 검증',f"독립 Decimal 집계 {verify['independent_decimal_counts']:,}기록, 수동 과거 특징·목표·경계,24모델 재적합을 확인했다.",
 f"독립 정렬 창으로 완료 잔차를 다시 계산해 {verify['replay_rows_checked']:,}행,12조건의 선택·판정,{verify['daily_method_metrics_checked']:,}일별 방법 지표를 검산했다.",
 '12미래 변이 검사에서 시험 이후 수요를 바꿔도 첫 시험 판단까지의 특징·보정이 유지됐다.',
 '테스트6개와 별도 정렬 창/배열 참조의3준비시간 결측 검사가 통과했다. 원본·helper 해시는 변경되지 않았다.',
 '첫 재적합 검사는수동math.log1p와원래numpy.log1p의최대약1.8e-15차이로 일부트리분할이달라 실패했다. 최초검증기·실패로그·규약은그대로 보존했다.',
 '수정 검증기는완전한과거이력의수동특징과원래특징을1e-13정밀도로비교하고,재적합에는고정된원래특징계산을사용해저장모델과정확히동일한예측을요구한다.',
 '따라서모델재적합은정확한원래입력에서의재현성확인이다.다른수치구현에서도모든트리분기가같다는주장은하지않는다.',
 '원시집계·목표·피드백·지표의독립계산은유지했다.모델·점수·선택·문턱은변경하지않았다.변경근거는VERIFIER_REFINEMENT.json에기록했다.',
 '재현 검사는 구현·자료의 계산 일치를 확인하며, 공개 trace의 완전성이나 실제 운영 효과를 증명하지 않는다.','',
 '7. 한계와 다음 단계',
 '이 연구는 같은 공개 서비스의 시간순 기록 예측이다. 서비스 간 전이, 실제 요청처리 용량·GPU 종류·VM 수,사용자SLO·비용은 정답에 없다.',
 '준비5/10/30분은 실측VM준비시간이 아니라 연구 가정이다. 모델의 수요 손실 개선만으로 증설 정책이 좋아졌다고 말하지 않는다.',
 '피드백은 계측 즉시 전달을 가정하고 실제 지연을 검증하지 않았다. 인접 목표가 겹쳐 원점을 독립 시행으로 셀 수 없다.',
 '추가 개선은 다른 미사용 서비스/기간,0의 이산 질량을 고려한 확률적 목표,실제 계측 가용 시각과앱 처리 용량의 연결이 필요하다.',
 '실제 배포·스케일 효과에는 별도action→READY→트래픽전환→사용자결과 기록이 필요하다. 운영 채택은false다.','',
 '8. 위치','원본:/home/researcher/ai-mcmp/research/data/external/burstgpt_v2/BurstGPT_3.csv',
 '학습·평가:/home/researcher/ai-mcmp/research/scripts/burstgpt_sparse_calls.py',
 '검증:/home/researcher/ai-mcmp/research/scripts/verify_burstgpt_sparse_calls_precision.py',
 '모델·재생·점수·검증:/home/researcher/ai-mcmp/research/artifacts/burstgpt_sparse_calls_v1/',
 '공식:https://github.com/HPMLL/BurstGPT/releases/tag/v2.0; Wang etal.,KDD2025,DOI10.1145/3711896.3737413,CC BY4.0']
 (s/'reports/burstgpt_sparse_calls_findings_2026_10_06.txt').write_text('\n'.join(lines)+'\n')
 pd.DataFrame(dailyrows).to_csv(out/'daily_method_metrics.csv',index=False);pd.DataFrame(group_rows).to_csv(out/'stratum_method_metrics.csv',index=False)
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 keys=list(results['comparisons']);gains=[100*results['comparisons'][k]['gain_vs_selected_equally_corrected_baseline'] for k in keys]
 cov=[100*results['comparisons'][k]['strata']['all']['stabilized']['coverage'] for k in keys]
 labels=[k.replace('|',' / ').replace(' log','')+' bins' for k in keys]
 fig,axes=plt.subplots(1,2,figsize=(12,7),sharey=True);positions=list(range(len(keys)))
 axes[0].barh(positions,gains,color=['#167d8d' if v>=5 else '#ac5555' for v in gains]);axes[0].axvline(5,color='black',ls='--');axes[0].axvline(0,color='gray')
 axes[0].set_yticks(positions,labels);axes[0].invert_yaxis();axes[0].set_xlabel('Pinball reduction vs validation-selected corrected comparator (%)')
 axes[1].barh(positions,cov,color='#4872a5');axes[1].axvspan(85,95,color='#5c9970',alpha=.2);axes[1].set_xlim(0,105);axes[1].set_xlabel('Empirical target coverage (%)')
 fig.suptitle('BurstGPT recorded arrivals: stabilized q90 + completed residual feedback\nRetrospective days255-284; horizon1/2/6 bins; no operational adoption',fontsize=11)
 fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);fig.savefig(out/'comparison.pdf');plt.close(fig)
 addendum=['AI-MCMP 담당 항목 진행 보강 | 2026-10-06',
 '기존 ai_mcmp_rr_status_2026_10_06.txt를 변경하지 않고 추가 연구 상태를 기록한다.',
 'Copilot 고정 모델의BurstGPT전이는12조건 모두지원 기준 미달로외부 성능 채점을 보류했다. 판정·원시 집계를독립 검증했다.',
 f'이어BurstGPT60일에서20일학습·10일검증·30일시험으로간헐 호출 모델24개를학습했다. 사전 고정된 주방법은12조건 중{passed}조건 통과,전체 통과={results["all_conditions_pass"]}.',
 '이 추가 연구는수요 입력 신호와완료 피드백 보정에해당한다. 요구분석전체·VM형상전체·실제DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP효과·기관연동완료는아니다.',
 '이전 정적추천·Philly위험보정·Copilot피드백·추론최적화의범위는그대로 유지한다.',
 '상세결과:reports/burstgpt_call_transfer_findings_2026_10_06.txt,reports/burstgpt_sparse_calls_findings_2026_10_06.txt']
 (s/'reports/ai_mcmp_rr_addendum_burstgpt_2026_10_06.txt').write_text('\n'.join(addendum)+'\n')
 snap=out/'source_snapshot';snap.mkdir()
 for folder in ('scripts','tests','reports'):
  for p in sorted((s/folder).glob('*')):
   if p.is_file() and p.suffix in ('.py','.json'):
    dest=snap/folder/p.name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(p,dest)
 files=[p for folder in ('scripts','tests','reports','artifacts') for p in sorted((s/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
 manifest={'files':{str(p.relative_to(s)):sha(p) for p in files},'new_only':True,'raw_data_preserved':True,'operational_adoption':False,'models_trained':24,'conditions_passed':passed}
 with (s/'reports/burstgpt_sparse_calls_publication_v1.json').open('x') as f:json.dump(manifest,f,indent=2)
 print('CURATED',len(files),'files',passed,'passed conditions')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);main(p.parse_args().stage)
