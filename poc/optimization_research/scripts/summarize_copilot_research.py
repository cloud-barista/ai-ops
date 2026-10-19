"""Report source quality failure and separately trained call-count models."""
import json
import argparse
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
s=parser.parse_args().stage;data=s/'artifacts/copilot_transfer_v1_1';models=s/'artifacts/copilot_call_demand_v1'
a=json.loads((data/'independent_source_audit.json').read_text());b=json.loads((models/'results.json').read_text())
assert json.loads((models/'independent_model_audit.json').read_text())['status']=='VERIFIED'
counts=a['audit'];p=100*a['unknown_prompt_fraction_of_all_calls']
lines=['AI-MCMP: Copilot 일부 기간 데이터 진단과 호출 도착량 모델 학습',
 '2026-10-06 | 토큰 외부 검증 보류, 별도 호출 수 과제 탐색', '',
 '1. 수행한 일과 담당 항목',
 '요청 수요를 예측해 경희대 배포·스케일 판단에 공급하는 신호를 연구했다.',
 '기존 Coding/LMM 수요 모델을 다른 서비스에서 확인하려고 Copilot 자료를 추가했다.',
 '토큰 정답 품질 기준에 실패해 이 검증은 채점하지 않았다. 대신 관측 가능한 호출 수로 별도 모델을 학습했다.',
 '두 과제의 정답·모델·평가를 구분한다. 호출 수 예측으로 토큰 외부 검증을 통과했다고 주장하지 않는다.', '',
 '2. 원본 수집과 데이터 진단',
 '공식 GitHub Copilot Coding Agent Traces 2026 중 6월 5~7일만 받았다.',
 '3개 tar.gz 합계 811,885,715바이트(약 812MB, 774.3MiB). 원본을 압축 상태로 보존했다.',
 '세션 69,666개, 총 LLM 호출 2,262,210개, 선택 시간 범위 밖 호출 3,816개.',
 f"기간 내 입력 토큰 누락/무효 {counts['invalid_prompt_calls']:,}건; 전체 호출 대비 {p:.3f}%.",
 '첫 집계는 session_id/turn_id/message_id를 호출 고유 ID로 잘못 사용해 다른 시각 호출을 제외했다.',
 '원본에서 이 ID가 재사용됨을 확인하고 공식 loader처럼 모든 기록된 호출을 세도록 수정했다.',
 f"재사용 그룹 ID {counts['repeated_grouping_ids']:,}건, 완전 동일 호출 기록 {counts['exact_repeated_call_records']:,}건.",
 '최초 실패 감사·코드와 수정본의 점수 전 고정 기록을 모두 보존했다.',
 '원본 SHA256/크기는 공식 배포 정보와 같고, 별도 나노초 파서·정수 집계로 864개 구간이 일치했다.',
 '입력 토큰 무효 유형: '+json.dumps(counts['invalid_prompt_types'],ensure_ascii=False),
 '입력 토큰 무효 호출 결과별: '+json.dumps(counts['invalid_prompt_status'],ensure_ascii=False), '',
 '3. 기존 6모델의 토큰 수요 외부 검증 판정',
 '기존 날짜·모델·예측 지표·성능 기준은 유지했고 품질 문턱 1%를 낮추지 않았다.',
 'null 토큰을 0으로 대체하지 않았다. 알려진 토큰 합은 전체 prompt 수요의 하한으로 표시했다.',
 '결론: SOURCE_LABEL_NO_GO. 기존 모델의 외부 예측 점수는 계산하지 않았으며 성공/실패 성능도 주장하지 않는다.',
 '기존 학습 모델과 이전 평가 결과는 변경하지 않았다.', '',
 '4. 새 과제: 준비 지연 동안의 기록 호출 수 상한',
 '호출 시각으로 세는 모든 기록된 LLM 호출 수는 prompt가 null이어도 만들 수 있다.',
 '5분 호출 수 y를 집계하고, 준비 완료 직후 구간까지 max(y_t,...,y_{t+d})를 q90으로 학습했다.',
 '준비 지연 5·10·30분에 대해 정답 구간 길이는 10·15·35분이다.',
 '이 라벨은 실패·취소를 포함한 기록 호출 수다. 전체 서비스 요청이나 prompt 토큰·GPU 비용이 아니다.',
 '서비스 전용 HGBq90 3개를 새로 학습해 joblib으로 저장했다. 기존 토큰 모델을 호출 수 모델로 재명명하지 않았다.',
 '설정: scikit-learn HistGradientBoostingRegressor, q=.9, 150회, lr=.05, 최대 잎7, 최소 잎20, L2=1, seed42.',
 '직전 1시간 평균으로 최대 호출 수를 정규화하고 동일 평균/학습 평균을 표본 가중치로 사용했다.',
 '과거 호출 수 비율·1시간 최대/q90·최근 추세·UTC 시각·주말·전날 비율 등11개 특징을 구성했다.',
 '학습에서는 평균1호출 이상을 지원 조건으로 둔다. 토큰 단위1000 문턱을 호출 수에 재사용하지 않았다.',
 '6월5일 학습, 6일 비교기 선택, 7일 시험. 지연별 학습275/274/270개와 시험287/286/282개다.',
 '이 실험은 사전 코드/평가 설정을 고정한 탐색이다. 기존 원본 구조·일부 잘못된 토큰 집계를 이미 봤다.',
 '사후 잔차 보정·주말 재학습·파라미터 검색을 하지 않았다.', '',
 '5. 새 모델 평가 결과',
 '준비분 | 우리 pinball | 선택 기준선 pinball | 상대 손실 감소 | 피복률 | 판정']
xs=[];ys=[];cov=[];col=[]
for h in ('1','2','6'):
 r=b['methods'][h]
 lines.append(f"{r['prepare_minutes']} | {r['pinball']:.6f} | {r['baseline_pinball']:.6f} | {100*r['relative_loss_reduction']:.2f}% | {100*r['coverage']:.2f}% | {r['gate']}")
 lines.append('  6일 선택 비교기: '+r['baseline']+'; 모든 비교기의 7일 손실: '+json.dumps(r['all_baseline_test_pinball']))
 xs.append(str(r['prepare_minutes'])+'min');ys.append(100*r['relative_loss_reduction']);cov.append(100*r['coverage'])
 col.append('#2f7d66' if r['gate']=='EXPLORATORY_CALL_FORECAST_PASS' else '#bc5b53')
passed=sum(r['gate']=='EXPLORATORY_CALL_FORECAST_PASS' for r in b['methods'].values())
lines.extend(['', f'사전 기준 통과 {passed}/3개. 기준은 선택 기준선 대비 pinball 5% 이상 감소와 피복률85~95%다.',
 '비교기: 학습 상대 최대 수요 가중q90, 지난1시간 최대/q90/평균의4개. 6일 손실만으로 선택했다.',
 '모델/비교기 선택을 저장한 뒤 7일 점수를 계산했으며 모든 비교기 시험 점수도 보존했다.',
 '해당 비교기는 검증에서 선택한 기준선이다. 시험 최선 기준선 대비 우위를 뜻하지 않는다.', '',
 '6. 검증과 적용 경계',
 '정확한 학습 행으로3모델을 다시 적합해 저장 모델의 모든 예측과 일치함을 확인했다.',
 '수동 최대 정답·분할 종료 시각·비교기 선택·모든 시험 지표·판정을 다시 계산했다.',
 '시험 미래 수요를 바꿔도 학습/검증과 첫 시험 시점의 과거 특징이 바뀌지 않는 것을 검사했다.',
 '집계·ID 재사용·결측/0·시각 경계·모델 입력·누출 관련 집중 테스트15개 통과.',
 '학습1일·검증1일·시험1일이고 금요일→주말 이동이다. 별도 미사용 장기 확인이 필요하다.',
 '학습 첫날은 전날 데이터가 없고 주말값이 항상0이다. 이 두 특징의 독립 효과를 학습·검증한 성과는 아니다.',
 '세션 표본의 일부 파티션이다. 다른 날짜 세션의 선택 기간 호출은 빠질 수 있다.',
 '호출 횟수는 토큰 길이·이미지·캐시 적중·GPU 계산량·대기열 복잡도를 반영하지 않는다.',
 '시각은 호출 시작으로, 집계 피드백은 종료 즉시 가용으로 가정했다. 실제 available_at은 없다.',
 '실제 GPU/VM 추천, 사용자 SLO, 스케일 조치 효과는 측정하지 않았고 운영 채택은false다.',
 '다음에는 더 긴 시간 범위의 호출 수 정답으로 검증하고, 토큰은 결측 발생/전달 조건을 먼저 확인한다.', '',
 '7. 위치와 출처',
 '원본: /home/researcher/ai-mcmp/research/data/external/ghcp_coding_agent_2026_days05_07/',
 '자료 감사: /home/researcher/ai-mcmp/research/artifacts/copilot_transfer_v1_1/',
 '새 모델: /home/researcher/ai-mcmp/research/artifacts/copilot_call_demand_v1/',
 '학습: /home/researcher/ai-mcmp/research/scripts/train_copilot_call_demand.py',
 '모델 재검산: /home/researcher/ai-mcmp/research/scripts/verify_copilot_call_demand.py',
 '원본 재검산: /home/researcher/ai-mcmp/research/scripts/verify_copilot_source_audit.py',
 '공식 설명: https://github.com/Azure/AzurePublicDataset/blob/master/GitHubCopilotCodingAgentDataset2026.md',
 '공식 배포: https://github.com/Azure/AzurePublicDataset/releases/tag/ghcp-coding-agent-2026',
 '논문: Liu et al., Agentic Coding in the Wild: Characterizing GitHub Copilot Traces at Production Scale, arXiv:2608.00101 (2026).',
 'CC-BY attribution과 배포 digest는 원본 폴더 SOURCE.txt/release_metadata.json에 보존했다.'])
text='\n'.join(lines)+'\n';(s/'reports/copilot_research_findings_2026_10_06.txt').write_text(text,encoding='utf8')
fig,axes=plt.subplots(1,2,figsize=(10,4.2),layout='constrained')
axes[0].bar(xs,ys,color=col);axes[0].axhline(5,color='#666',ls='--');axes[0].axhline(0,color='black',lw=.6)
axes[0].set_ylabel('Pinball reduction (%)');axes[0].set_title('Vs validation-selected baseline')
axes[1].bar(xs,cov,color=col);axes[1].axhspan(85,95,color='#2f7d66',alpha=.15);axes[1].set_ylim(0,100)
axes[1].set_ylabel('Empirical test coverage (%)');fig.suptitle('Copilot call count forecasts: one day exploratory test')
fig.savefig(models/'call_demand_results.png',dpi=180,bbox_inches='tight');fig.savefig(models/'call_demand_results.pdf',bbox_inches='tight');plt.close(fig)
print(text)
