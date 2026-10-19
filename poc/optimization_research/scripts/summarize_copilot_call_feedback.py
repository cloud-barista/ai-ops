"""Write a descriptive report and shareable plots after replay verification."""
from pathlib import Path
import argparse,json,hashlib,shutil
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main(stage):
 out=stage/'artifacts/copilot_call_feedback_v1'
 assert json.loads((out/'verification.json').read_text())['status']=='VERIFIED'
 r=json.loads((out/'results.json').read_text())['comparisons']
 lines=['AI-MCMP Copilot 호출 수요: 완료된 오차 피드백 보정 연구',
 '2026-10-06 | 기존 시험을 재사용한 후향 탐색; 운영 채택 아님','',
 '1. 문제와 경희대 담당 항목',
 '기존 HGB q90은 3일을 학습해도 시험 피복률이 54.96~69.69%였다. q90 모델이라고 해서 실제 90% 피복이 보장되지는 않았다.',
 '운영 결과 피드백을 활용하는 경희대 연구 항목에 맞춰, 종료된 수요 정답과 원시 예측의 차이를 이용한 보정을 시험했다.',
 '연구 출력은 기록된 LLM 호출 수 상한 신호다. 배포 명령이나 실제 Scale-out/in 효과의 검증은 아니다.','',
 '2. 자료·모델·분할',
 '검산된 Copilot June3~7 세션 표본의 호출 수 1,440개 5분 구간을 사용했다.',
 '기존 v2 HGB q90 모델 3개와 그 저장 예측을 고정했다. 새 예측 모델 학습, 파라미터 검색, 원본 재다운로드는 하지 않았다.',
 '모델 학습은 June3~5, 보정 피드백 시작은 June6의 첫 구간이다. 학습 행의 적합 오차는 보정에 넣지 않았다.',
 'June6의 보정된 기준선 손실로 비교기를 선택하고, 그 선택을 저장한 뒤 June7 점수를 계산했다.',
 'June7은 앞선 실험에서 이미 열람한 시험이다. 평가 설정을 이번 점수 전에 고정했어도 새 독립 확인이 되지는 않는다.','',
 '3. 보정 방법',
 'y_i는 5분 호출 수이며, 준비 지연 h=1/2/6구간에 대한 타깃은 u_i=max(y_i,...,y_(i+h))다.',
 '해당 타깃은 i+h+1에서 관측 구간이 끝난다. 피드백 추가 지연 l을 가정하면 i+h+1+l <= t인 결과만 판단 t에서 사용한다.',
 'm_i는 i 직전 완료된 12구간 호출 수 평균이다. m_i >= 1이고 예측·정답이 유효한 기록만 쓴다.',
 '현재 t의 후보는 June6 이후 원점 중 [max(864,t-h-l-288),t-h-l)의 기록이다. 최근 288개 원점 중 적격 기록이 최소96개여야 보정한다.',
 'e_i=(u_i - raw_prediction_i)/m_i',
 'delta_t=max(0, empirical_q90(e_i))',
 'corrected_prediction_t=raw_prediction_t+m_t*delta_t',
 '경험 q90은 선형 보간으로 계산한다. 원시 예측의 잔차를 쓰며 이전 보정값을 재귀적으로 정답에 넣지 않는다.',
 '학습 상대 최대 수요의 가중 q90, 지난1시간 최대/q90/평균 기준선4개에도 동일한 규칙을 적용한다.',
 '주조건은 결과 완료 즉시 피드백이 가용하다는 가정이다. 추가5분/30분 지연은 사전 정의한 민감도 조건이다.',
 '규칙은 기존 잔차 분위수 보정의 호출 단위 적용이다. 새로운 학습기 구조나 피복 보장 정리를 제안했다고 주장하지 않는다.','',
 '4. 주조건 결과: 동일 보정을 적용한 검증 선택 기준선과 비교',
 '준비분 | 모델 원시 pinball | 모델 보정 pinball | 기준선 보정 pinball | 기준선 대비 손실 감소 | 모델 원시 피복 | 모델 보정 피복 | 판정']
 for h in (1,2,6):
  v=r[f'{h}:0'];a=v['scores']['hgb'];b=v['scores'][v['chosen_baseline']];raw=v['raw_scores']['hgb']
  lines.append(f"{h*5} | {raw['pinball']:.6f} | {a['pinball']:.6f} | {b['pinball']:.6f} | {100*v['model_gain_vs_corrected_baseline']:.2f}% | {100*raw['coverage']:.2f}% | {100*a['coverage']:.2f}% | {v['gate']}")
  lines.append(f"검증 선택 기준선: {v['chosen_baseline']}; 완전한 시험 정답 {a['rows']}개")
 lines+=['','채택 기준은 검증 선택·동일 보정 기준선 대비 손실5% 이상 감소와 모델 피복85~95%를 동시에 요구했다. 주조건3개 모두 탐색 기준을 통과했다.',
 '보정 전 모델 자체 대비 손실 감소는 28.81/40.19/54.45%다. 이 수치를 기준선 대비 개선율로 바꾸어 표시하지 않는다.',
 '시험에서 가장 낮은 손실의 단순 기준선을 사후 확인한 대조도 아래에 남긴다. 이 비교기로 검증 선택을 바꾸지 않는다.',
 '준비분 | 시험 최선 보정 기준선 | 해당 손실 | 그 기준선 대비 모델 손실 감소']
 for h in (1,2,6):
  v=r[f'{h}:0'];name=min((n for n in v['scores'] if n!='hgb'),key=lambda n:v['scores'][n]['pinball']);b=v['scores'][name]['pinball']
  lines.append(f"{h*5} | {name} | {b:.6f} | {100*(1-v['scores']['hgb']['pinball']/b):.2f}%")
 lines+=['','5. 모든 사전 정의 피드백 지연 조건',
 '준비분 | 추가 피드백 지연분 | 모델 pinball | 선택 기준선 pinball | 손실 감소 | 모델 피복 | 탐색 판정']
 for h in (1,2,6):
  for lag in (0,1,6):
   v=r[f'{h}:{lag}'];a=v['scores']['hgb'];b=v['scores'][v['chosen_baseline']]
   lines.append(f"{h*5} | {lag*5} | {a['pinball']:.6f} | {b['pinball']:.6f} | {100*v['model_gain_vs_corrected_baseline']:.2f}% | {100*a['coverage']:.2f}% | {v['gate']}")
 lines+=['','6. 검증',
 '모듈·평가기·검증기·테스트·평가 규약과 원본 입력/모델 파일 해시를 이번 점수 전에 고정했다.',
 '시점 경계, 추가 전달 지연, 학습 오차 제외, 호출 단위 지원, 최근창과 결측, 미래 수요 변이, 음수 보정 제한·입력 불변 등의 테스트7개가 통과했다.',
 '별도 검증기는 원점별 적격 ID를 직접 만들고 정렬·선형 보간으로 분위수를 계산했다. 수동 타깃·기준선·선택·모든 지표·판정을 확인했다.',
 '45개 예측/보정 조합과64,800행을 재검산했고 모든 준비 지연×전달 지연에서 미래 수요 변경이 현재까지의 예측을 바꾸지 않았다.',
 '부모 모델과 원시 자료는 변경하지 않았다. 그래프는 점수의 기술적 표시이며 독립 통계적 유의성 증거가 아니다.','',
 '7. 적용 경계와 다음 단계',
 '기존 시험 하루를 여러 연구에서 열람했다. 향상은 이 후향 재생의 조건부 결과이며 새 기간·새 서비스·운영 안전성 증거가 아니다.',
 '인접 정답은 준비 지연 때문에 겹친다. 287/286/282개 시험 원점을 독립 시행으로 취급하지 않는다.',
 '계측 available_at은 공개 자료에 없다. 즉시 또는 고정5/30분 전달은 가정이며, 지연 민감도를 실제 전달시간 검증이라고 말하지 않는다.',
 '양수 잔차 보정은 과소예측을 줄일 수 있지만 과잉 예산과 적응 지연도 생길 수 있다. 보정 전 모델이 모든 서비스에서 개선된다는 보장은 없다.',
 'prompt null을0으로 대체하지 않았고 기존 토큰 외부 검증 SOURCE_LABEL_NO_GO는 유지한다.',
 '호출 횟수는 호출 길이·캐시·이미지·GPU 계산량을 나타내지 않는다. GPU/VM 수 또는 사용자SLO에 직접 환산하지 않는다.',
 '실제 정책 채택은 false다. 다음 확인은 새 기간/서비스와 계측 전달 시각을 확보하고, 동일 보정 기준선으로 다시 비교하는 것이다.',
 '판단 정책 효과에는 별도로 action→READY→트래픽 전환→사용자 결과가 필요하다. 이번 예측 신호 개선으로 스케일 효과를 주장하지 않는다.','',
 '8. 재현 위치',
 '모듈: /home/researcher/ai-mcmp/research/scripts/copilot_call_feedback.py',
 '평가: /home/researcher/ai-mcmp/research/scripts/evaluate_copilot_call_feedback.py',
 '검증: /home/researcher/ai-mcmp/research/scripts/verify_copilot_call_feedback.py',
 '자료·고정값·재생: /home/researcher/ai-mcmp/research/artifacts/copilot_call_feedback_v1/',
 '부모 학습 모델: /home/researcher/ai-mcmp/research/artifacts/copilot_call_demand_v2/',
 '새 stage에 scripts/tests/reports를 배치한 뒤 prepare→run→verify 순서로 실행한다. 기존 산출물은 덮어쓰지 않는다.',
 '공식 원본 출처: https://github.com/Azure/AzurePublicDataset/releases/tag/ghcp-coding-agent-2026']
 (stage/'reports/copilot_call_feedback_findings_2026_10_06.txt').write_text('\n'.join(lines)+'\n',encoding='utf8')
 fig,axes=plt.subplots(1,2,figsize=(11,4.4));x=np.arange(3);width=.25
 for j,(name,label,color) in enumerate([('raw','Raw HGB','#9b9b9b'),('corrected','HGB + feedback','#2166ac'),('baseline','Selected baseline + feedback','#d98d32')]):
  metrics=[]
  for h in (1,2,6):
   v=r[f'{h}:0'];metrics.append(v['raw_scores']['hgb'] if name=='raw' else v['scores']['hgb' if name=='corrected' else v['chosen_baseline']])
  axes[0].bar(x+(j-1)*width,[v['pinball'] for v in metrics],width,label=label,color=color)
  axes[1].plot(x,[100*v['coverage'] for v in metrics],marker='o',label=label,color=color)
 for ax in axes:
  ax.set_xticks(x,['5 min','10 min','30 min']);ax.set_xlabel('Assumed readiness delay');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
 axes[0].set_ylabel('q90 pinball loss (recorded calls)');axes[0].set_title('Lower loss is better')
 axes[1].axhspan(85,95,color='#4daf4a',alpha=.12);axes[1].axhline(90,color='black',ls='--',lw=.8);axes[1].set_ylim(45,100);axes[1].set_ylabel('Empirical coverage (%)');axes[1].set_title('Prespecified acceptable range: 85-95%')
 axes[0].legend(fontsize=8)
 fig.suptitle('Copilot June 7: retrospective completed-feedback replay')
 fig.text(.5,.01,'Previously examined test day; assumed immediate feedback; sampled calls; no VM/SLO effect.',ha='center',fontsize=8)
 fig.tight_layout(rect=(0,.045,1,.94))
 fig.savefig(out/'comparison.png',dpi=180);fig.savefig(out/'comparison.pdf');plt.close(fig)
 snap=out/'source_snapshot';snap.mkdir(exist_ok=True)
 for folder in ('scripts','tests','reports'):
  (snap/folder).mkdir(exist_ok=True)
  for p in (stage/folder).iterdir():
   if p.is_file():shutil.copy2(p,snap/folder/p.name)
 def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
 (out/'release_manifest.json').write_text(json.dumps({str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p != out/'release_manifest.json'},indent=2)+'\n')
 files={}
 for folder in ('scripts','tests','reports','artifacts'):
  for p in (stage/folder).rglob('*'):
   if p.is_file():files[str(p.relative_to(stage))]={'sha256':sha(p)}
 root=Path('/home/researcher/ai-mcmp/research')
 for n in files:assert not (root/n).exists(),n
 (stage/'publication.json').write_text(json.dumps({'files':files,'all_verified':True,'test_previously_seen':True,'operational_adoption':False},indent=2)+'\n')
 print('PREPARED',len(files),'new files')


if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--stage',type=Path,required=True);args=ap.parse_args();main(args.stage)
