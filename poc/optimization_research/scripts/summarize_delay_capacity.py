"""Create a source-linked text report and figures without changing scored artifacts."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def write_reports(out, figures_only=False, refresh=False):
    result=json.loads((out/'results.json').read_text())
    audit=json.loads((out/'audit.json').read_text())
    if audit['status']!='VERIFIED_EXPLORATORY_TRAINING_AND_REPLAY':raise ValueError('audit required')
    lines=['AI-MCMP 지연 대응 수요 예측과 스케일링 판단: 상세 연구 결과','2026-10-06 | 탐색 연구', '',
           '1. 연구 문제',
           '기존 모델은 다음 5분 수요만 예측해 준비에 5·10·30분이 걸리는 증설의 도착 시점과 어긋났다.',
           '또 예측을 정책에 넣으면 용량 부족 감소와 증설·축소 반복 증가가 함께 나타났다.', '',
           '2. 데이터와 학습',
           'Azure Coding 16,803,695요청과 LMM 1,000,000요청을 각각 5분 합계 2,016개로 집계한 고정 v1 자료를 사용했다.',
           '서비스 자료를 합치지 않았다. 원시 요청과 기존 모델은 수정하지 않았다.',
           '학습은 첫 3일, 검증은 다음 1일, 평가는 마지막 3일이다. Coding과 LMM의 실제 UTC 시각을 각각 반영했다.',
           '모델은 scikit-learn HistGradientBoostingRegressor다. quantile=0.9, 반복 150회, learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=20, L2=1, seed=42다.',
           '서비스별 지연 1·2·6구간을 각각 학습해 6개의 별도 모델과 joblib 파일을 만들었다.', '',
           '3. 우리 연구 구성',
           '타깃은 u_t=max(y_t,...,y_{t+d})다. 준비 완료 시각은 t+d의 시작이고, 타깃은 READY 직후 5분 구간까지 포함한다.',
           '따라서 준비 지연 5·10·30분의 타깃 관측 구간 길이는 10·15·35분이다. 각 5분 합계 중 최댓값을 예측한다.',
           '직전 1시간 평균 m_t로 u_t를 정규화하고 u_t/m_t의 q90을 학습했다. 가중치는 m_t/학습평균(m)이다.',
           '입력 11개는 과거 토큰량, 전날 비율, 과거 1시간 최대·q90, 최근 추세, UTC 시각, 주말 표시다.',
           'LMM 새 모델은 토큰 특징만 사용했다. 이 실험은 이미지 특징을 추가한 효과를 검증하지 않았다.',
           '정답 u_i는 i+d+1에 끝난다. 현재 t에서 i+d+1<=t인, 검증 기간 이후의 완료된 예측만 피드백으로 사용했다.',
           '직전 최대 288개 완료 오차의 양수 정규화 q90을 더하고, 96개 미만이면 보정하지 않는다. 피복률 보장은 없다.',
           '학습의 마지막 예측 시점은 863-d로, 정답이 학습 기간 안에서 끝난다. 지연별 학습 행 수는 851·850·846이다.',
           '미래 정답이 끝나지 않은 평가 마지막 d개 시점은 예측 오차 평가에서 제외하되, 정책 재생 864구간은 모두 보존했다.',
           'q90 HGB 자체나 잔차 보정 원리를 새로 발명한 주장은 없다. 지연에 맞춘 정답, 완료 시점 통제, 정책 비교가 연구 구성이다.', '',
           '4. 지연별 최대수요 예측 결과',
           '아래 비교기는 검증 구간 pinball로 선택한 단순 예측에 동일한 완료 오차 보정을 적용한 것이다.',
           '서비스 | 준비지연 | 우리pinball | 기준선pinball | 상대손실감소 | 우리피복 | 기준선피복']
    for name in ('coding','lmm'):
        for delay in (1,2,6):
            f=result['forecasts'][f'{name}_delay{delay}'];a=f['methods']['peak_model_feedback'];b=f['methods']['peak_baseline_feedback']
            gain=(1-a['pinball']/b['pinball'])*100
            lines.append(f"{name} | {delay*5}분 | {a['pinball']:.3f} | {b['pinball']:.3f} | {gain:.2f}% | {a['coverage']*100:.2f}% | {b['coverage']*100:.2f}%")
        lines.append('')
    lines.extend(['이 성능은 이미 노출된 각 서비스 한 주의 후향 평가다. 별도 기간·다른 서비스로 일반화한 결과는 없다.',
                  '5. 정책과제와결과',
                  '가상 slot의 유체 대기열, 고정 처리 용량, 준비 지연, 최대 8slot, 한 번에 +1/-1을 가정했다.',
                  '최소 유지 0·30·60분, 연속 확인 3·6회, 축소 여유 0.6·0.8의 12후보를 각 방법에 동일하게 제공했다.',
                  '검증 기본 정책보다 부족량·대기량·최종 대기량이 나빠지지 않고 비용 대리값 +1% 이내인 후보 중 방향 전환을 먼저 줄였다.',
                  '선택된 유지 조건은 평가 시작에만 적용했다. 검증 상태 준비에는 기본 조건을 사용해 소급 선택을 막았다.',
                  '용량 3가지 × 준비 지연 3가지 × 예측 방법 5가지 × 유지 조건 2가지 × 서비스 2가지 = 180정책 비교.',''])
    for name,p in result['primary'].items():
        a,b=p['candidate'],p['strong_baseline'];old=result['ablations'][name]['v1_same_model_default']
        lines.extend([name.upper()+' 주조건(용량 factor=1, 준비 5분): '+p['gate'],
                      f"강한 기준선→후보의 비용 대리값(slot-minutes): {b['billed_slot_minutes']}→{a['billed_slot_minutes']}",
                      f"즉시 부족 입력량 비율: {b['shortfall_fraction']*100:.3f}%→{a['shortfall_fraction']*100:.3f}%",
                      f"방향 전환 횟수: {b['direction_reversals']}→{a['direction_reversals']}",
                      f"v1 같은 서비스 모델과 비교한 부족 입력량 비율: {old['shortfall_fraction']*100:.3f}%→{a['shortfall_fraction']*100:.3f}%",
                      f"v1 같은 서비스 모델과 비교한 비용 대리값: {old['billed_slot_minutes']}→{a['billed_slot_minutes']}",''])
    rows=result['scenarios']
    lmm_pair=[r for r in rows if r['dataset']=='lmm' and r['delay']==2 and r['capacity_factor']==1 and r['method']=='peak_model_feedback']
    if len(lmm_pair)==2:
        a=next(r for r in lmm_pair if r['variant']=='default_hold');b=next(r for r in lmm_pair if r['variant']=='validation_selected_hold')
        lines.extend(['보조 조건 LMM 준비 10분에서는 유지 30분이 검증에서 선택됐다.',
                      f"평가 방향 전환 {a['direction_reversals']}→{b['direction_reversals']}, 비용 {a['billed_slot_minutes']}→{b['billed_slot_minutes']}({(b['billed_slot_minutes']/a['billed_slot_minutes']-1)*100:.2f}% 증가).",
                      '검증의 +1% 비용 범위가 평가에서 그대로 유지되지 않았다. 방향 전환 감소만 떼어 최종 개선으로 표현하지 않는다.',''])
    lines.extend(['6. 검증',
                  '저장된 6모델을 명시한 학습 행으로 재적합해 예측값이 정확히 같음을 확인했다.',
                  '90개 검증 선택, 1,080개 검증 후보, 180개 정책, 155,520개 평가 행을 재검산했다.',
                  '평가 구간 수요 변경이 학습·검증에 사용한 특징·정답과 현재 판단을 바꾸지 않는 것, 피드백의 완료 경계를 검사했다.',
                  '정책 테스트 10개 통과. 원시 자료와 v1 아티팩트는 변경하지 않았다.','',
                  '7. 결론과다음필요사항',
                  '준비 지연에 맞춘 최대 수요 예측은 단순 비교기보다 예측 손실을 줄였다.',
                  '하지만 비용·부족·진동의 공동 조건을 만족한 정책 개선은 확인하지 못해 운영 채택은 false다.',
                  '단기 예측 정확도만으로 정책의 효과를 주장할 수 없고 최대 용량과 준비 지연·조치 비용을 함께 고려해야 한다.',
                  '실제 앱 용량과 가격·준비시간, 새 기간의 데이터, action→READY→트래픽 전환→사용자 결과의 연결이 필요하다.',
                  '성능 측정 대상은 입력 토큰 상한과 가상 유체 모형이다. GPU·VM 수, 사용자 SLO, 실제 스케일 인과 효과는 미검증이다.'])
    if not figures_only:
        with (out/'detailed_findings.txt').open('w' if refresh else 'x',encoding='utf8') as f:f.write('\n'.join(lines)+'\n')
    fig,axes=plt.subplots(2,2,figsize=(11,7),layout='constrained')
    for col,name in enumerate(('coding','lmm')):
        ax=axes[0,col];xx=np.arange(3);ratios=[]
        for delay in (1,2,6):
            f=result['forecasts'][f'{name}_delay{delay}']['methods']
            ratios.append(100*f['peak_model_feedback']['pinball']/f['peak_baseline_feedback']['pinball'])
        ax.bar(xx,ratios,color='#3b77a7',width=.6)
        ax.axhline(100,color='#777777',linestyle='--',linewidth=1,label='Validation-selected baseline')
        for x,ratio in zip(xx,ratios):ax.text(x,ratio+2,f'{ratio:.1f}%',ha='center',fontsize=9)
        ax.set(xticks=xx,xticklabels=['5 min','10 min','30 min'],ylim=(0,120),
               title=f'{name.title()}: peak demand forecast',xlabel='Startup delay',ylabel='Pinball / baseline (%)')
        ax=axes[1,col];primary=result['primary'][name];base=primary['strong_baseline']
        data=[('One-step ML',result['ablations'][name]['v1_same_model_default']),
              ('Peak ML',primary['candidate']),('Peak baseline',base)]
        colors=['#777777','#3b77a7','#d47732']
        for (label,row),color in zip(data,colors):
            x=100*row['billed_slot_minutes']/base['billed_slot_minutes']
            y=100*row['shortfall_fraction']/base['shortfall_fraction']
            ax.scatter(x,y,color=color,s=65,label=label)
            ax.annotate(label,(x,y),xytext=(-5,5) if label=='Peak ML' else (5,-12) if label=='Peak baseline' else (5,5),
                        ha='right' if label=='Peak ML' else 'left',textcoords='offset points',fontsize=8)
        ax.axvline(100,color='#bbbbbb',linestyle='--',linewidth=1)
        ax.axhline(100,color='#bbbbbb',linestyle='--',linewidth=1)
        ax.set(title=f'{name.title()}: hypothetical capacity tradeoff',xlabel='Billed slot-minutes / baseline (%)',
               ylabel='Instant shortfall / baseline (%)')
        ax.margins(x=.20,y=.20)
    fig.suptitle('Delay-aligned demand learning: forecast gains do not establish policy gains',fontsize=12)
    for ext in ('png','pdf'):fig.savefig(out/f'delay_capacity_tradeoff.{ext}',dpi=180)
    plt.close(fig)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--figures-only',action='store_true');ap.add_argument('--refresh',action='store_true')
    args=ap.parse_args();write_reports(args.output,args.figures_only,args.refresh)
