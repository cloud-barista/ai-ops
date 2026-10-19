"""Publishable negative transfer result and sparse-arrival diagnosis."""
import argparse, hashlib, json, shutil
from pathlib import Path
import numpy as np
import pandas as pd

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def main(s):
 out=s/'artifacts/burstgpt_call_transfer_v1'
 verification=json.loads((out/'verification.json').read_text())
 assert verification['status']=='VERIFIED' and verification['scored_conditions']==0
 result=json.loads((out/'results.json').read_text());source=json.loads((out/'source_audit.json').read_text())
 assert len(result['comparisons'])==12 and all(v['gate']=='SOURCE_SUPPORT_NO_GO' for v in result['comparisons'].values())
 frame=pd.read_parquet(out/'counts.parquet');frame=frame[frame.within_recorded_extent]
 diagnosis={}
 for name in source['cohort_rows']:
  y=frame[name].to_numpy();m=pd.Series(y).shift(1).rolling(12,min_periods=12).mean().dropna()
  diagnosis[name]={'complete_bins':len(y),'zero_recorded_fraction':float((y==0).mean()),
   'past_hour_mean_lt_one_fraction':float((m<1).mean()),'past_hour_mean_zero_fraction':float((m==0).mean()),
   'past_hour_mean_between_zero_and_one_fraction':float(((m>0)&(m<1)).mean()),'positive_bins':int((y>0).sum())}
 with (out/'sparse_arrival_diagnosis.json').open('x') as f:json.dump(diagnosis,f,indent=2)
 # The frozen verifier's daily_metrics_checked flag is vacuous when no condition is scored.
 scope={'scored_forecast_conditions':0,'daily_forecast_metrics_recomputed':0,'raw_count_and_support_checks':12,
  'replay_rows_checked':verification['replay_rows'],'future_target_mutation_checks':verification['future_mutation_checks'],
  'daily_metrics_checked_in_original_verifier_is_vacuous':True,'forecast_accuracy_verified':False}
 with (out/'verification_scope.json').open('x') as f:json.dump(scope,f,indent=2)
 lines=['AI-MCMP: 固定 Copilot 호출 모델의 BurstGPT 적용 범위 검사'.replace('固定','고정'),
 '2026-10-06 | 12조건 모두 지원 기준 미달; 외부 예측 성능 채점 없음','',
 '1. 결론','기존 Copilot 모델을 BurstGPT에 그대로 적용한 12조건은 모두 SOURCE_SUPPORT_NO_GO다.',
 '이는 예측 손실이 나쁘다는 판정이 아니라, 사전에 정한 동일 지원 집합의 양과 비율이 부족해 채점을 보류한 판정이다.',
 '새 모델 학습은 0개, 운영 채택은 false다. 비교기·모델·지원 문턱을 점수에 맞춰 바꾸지 않았다.','',
 '2. 원본과 범위',f"기존 BurstGPT_3.csv 중 상대일225~254의 {source['selected_rows']:,}개 기록을 집계했다.",
 '4개 모델/로그 코호트를 분리했다. 실패·취소·토큰 결측 여부로 호출 기록을 제외하지 않았다.',
 '첫 원시 기록이 시작하는 부분 구간은 NaN으로 처리했다. 완전한 timestamp 범위의5분 구간은8,639개다.',
 '빈 구간의0은 기록된 요청이 없다는 뜻이며, 수집기 heartbeat가 없어 실제 무수요나 모니터링 연속성을 보장하지 않는다.',
 'Timestamp는 상대 초다. UTC날짜·요일을 생성하지 않았다. 원본은 변경하거나 삭제하지 않았다.','',
 '3. 고정 모델과 비교',
 'Copilot June3~5에서 학습한 no-calendar 7특징 HGB q90 모델3개를 고정했다. 외부 데이터로 다시 적합하지 않았다.',
 '이 모델은 앞서 Copilot에서 개선된11특징 피드백 실험의 모델과 다르다. 달력 정보를 확인할 수 없어 별도 학습한 호환 모델이다.',
 '5/10/30분 준비 지연에 대응하는 타깃은 현재부터 READY직후까지의 최대5분 호출 수이며, 관측창 길이는10/15/35분이다.',
 '이전1시간 평균으로 정규화한다. 현재 평균이1호출 미만이거나 입력 이력이 불완전하면 보류한다.',
 '양수 잔차q90 보정은 완료된 결과만 쓰며 최근288원점·최소96개 규칙이다. 결과 완료 즉시 전달을 가정했다.',
 '학습 상대q90, 직전1시간 최대/q90/평균의4개 단순 기준선에도 같은 보정을 적용한다.',
 '검증은 상대일225, 시험은226~254다. 각 분할의 공통 지원 원점144개 이상과 지원 비율80% 이상을 모두 요구했다.',
 '검증 목표창이 시험 시작을 넘지 않게 했다. 지원 조건을 통과한 경우에만 검증 보정 손실로 기준선을 선택하도록 고정했다.','',
 '4. 모든 조건의 지원 결과','코호트 | 준비분 | 검증 지원/가능 | 검증비율 | 시험 지원/가능 | 시험비율 | 판정']
 for key,v in result['comparisons'].items():
  name,h=key.rsplit(':',1);a=v['validation_support'];b=v['test_support']
  lines.append(f"{name} | {int(h)*5} | {a['supported_origins']}/{a['possible_origins']} | {a['supported_fraction']:.2%} | {b['supported_origins']}/{b['possible_origins']} | {b['supported_fraction']:.2%} | {v['gate']}")
 lines+=['','5. 저수요와 무기록 구간 진단','코호트 | 0기록5분 비율 | 직전1시간 평균<1 비율 | 평균=0 비율 | 0<평균<1 비율']
 for name,d in diagnosis.items():lines.append(f"{name} | {d['zero_recorded_fraction']:.2%} | {d['past_hour_mean_lt_one_fraction']:.2%} | {d['past_hour_mean_zero_fraction']:.2%} | {d['past_hour_mean_between_zero_and_one_fraction']:.2%}")
 lines+=['분모가 작은 구간을 보류하는 기존 정규화 설계가 이 기록의 간헐적 도착 패턴과 맞지 않는다.',
 '지원 조건을 완화해서 기존 전이가 성공했다고 주장하지 않는다.0·저수요를 지원하는 별도 문제·모델이 필요하다.','',
 '6. 독립 검증',
 '별도 CSV reader와 Decimal timestamp로 원시 호출 수를 다시 집계하고, 수동 목표·평균·분위수·적격 피드백ID로518,400재생 행을 확인했다.',
 '12지원 판정과12미래 목표 변이 검사를 통과했다. 원본·모델·고정 코드 해시는 동일하다.',
 '모듈 테스트5개가 통과했다. 채점 조건0개이므로 일별 예측 손실·피복률 검산도0개다.',
 'verification.json의 daily_metrics_checked=true는 채점 분기를 검사하는 코드가 종료됐다는 의미다. 실제 일별 수치 검산으로 해석하지 않는다.',
 'verification_scope.json에 실제 검증 범위를 별도로 기록했다. 자료·계산 검증 성공은 예측 성능 개선의 증거가 아니다.','',
 '7. 적용 경계와 다음 연구',
 '이 원본은 이전 토큰·지연 연구에서도 열람됐다. 새로운 독립 블라인드 확인이라고 부르지 않는다.',
 '원본 available_at이 없어 결과 전달 지연을 실측하지 않았다. 호출 수는 GPU 계산량·최소VM형상·사용자SLO가 아니다.',
 '다음 연구에서는 고정된 별도 시간순 학습/검증/시험으로0·저수요 기록을 포함하는 호출 예측기를 학습한다.',
 '안정된 분모와 무기록 이력 특징을 명시하고, 절대 수요 학습과 강한 단순 비교기를 함께 비교한다.',
 '전체 평균 외에 저수요·재도착 구간 결과를 보고한다. 이 연구도 기록된 호출 수 예측에 한정한다.','',
 '8. 위치','원본: /home/researcher/ai-mcmp/research/data/external/burstgpt_v2/BurstGPT_3.csv',
 '고정 모델: /home/researcher/ai-mcmp/research/artifacts/copilot_external2023_v1/',
 '평가: /home/researcher/ai-mcmp/research/scripts/burstgpt_call_transfer.py',
 '검증: /home/researcher/ai-mcmp/research/scripts/verify_burstgpt_call_transfer.py',
 '자료·판정·재생·검증: /home/researcher/ai-mcmp/research/artifacts/burstgpt_call_transfer_v1/',
 '공식 원본: https://github.com/HPMLL/BurstGPT/releases/tag/v2.0',
 '출처 표시: Wang et al., BurstGPT, KDD2025, DOI10.1145/3711896.3737413; CC BY4.0']
 (s/'reports/burstgpt_call_transfer_findings_2026_10_06.txt').write_text('\n'.join(lines)+'\n')
 snap=out/'source_snapshot';snap.mkdir()
 for folder in ('scripts','tests','reports'):
  for p in sorted((s/folder).glob('*')):
   if p.is_file() and p.suffix in ('.py','.json'):
    dest=snap/folder/p.name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(p,dest)
 files=[p for folder in ('scripts','tests','reports','artifacts') for p in sorted((s/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
 manifest={'files':{str(p.relative_to(s)):sha(p) for p in files},'new_only':True,'raw_data_preserved':True,
  'operational_adoption':False,'forecast_conditions_scored':0}
 with (s/'reports/burstgpt_call_transfer_publication_v1.json').open('x') as f:json.dump(manifest,f,indent=2)
 print('CURATED',len(files),'files')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);main(p.parse_args().stage)
