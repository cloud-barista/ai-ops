"""Train six peak q90 models; freeze validation-only policy selection; assess separately."""
import argparse
from dataclasses import asdict
import csv
import gzip
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from delay_capacity_policy import peak_targets,mature_feedback,Hold,replay_delay,choose_hold
from demand_capacity_policy import summarize

PARAMS=dict(loss='quantile',quantile=.9,max_iter=150,learning_rate=.05,
            max_leaf_nodes=7,min_samples_leaf=20,l2_regularization=1.,
            early_stopping=False,random_state=42)
FEATURES=('lag1_over_scale','lag2_over_scale','lag3_over_scale','lag12_over_scale',
          'previous_day_ratio','past_hour_max_over_scale','past_hour_q90_over_scale',
          'recent_six_over_previous_six','clock_sin','clock_cos','weekend')
BASELINES=('train_weighted_peak_ratio_q90','past_hour_max','past_hour_empirical_q90','past_hour_mean')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def save_json(path,value):
    with Path(path).open('x',encoding='utf8') as f:
        json.dump(value,f,indent=2,allow_nan=False,ensure_ascii=False);f.write('\n')


def pinball(y,p):
    e=y-p
    return np.maximum(.9*e,-.1*e)


def training_origins(horizon):
    return np.arange(12,864-horizon)


def design(y,name,coding):
    start=pd.Timestamp('2024-05-10T00:00:00Z' if name=='coding' else '2024-10-15T12:00:00Z')
    frame=coding.design_from_aggregates(y,start=start)
    timestamps=pd.date_range(start,periods=len(y),freq='5min')
    clock=timestamps.hour.to_numpy()*12+timestamps.minute.to_numpy()//5
    frame['clock_sin']=np.sin(2*np.pi*clock/288)
    frame['clock_cos']=np.cos(2*np.pi*clock/288)
    frame['weekend']=(timestamps.dayofweek.to_numpy()>=5).astype(float)
    return frame


def verify_inputs(root,out):
    if sklearn.__version__!='1.6.1' or np.__version__!='2.0.2':raise ValueError('pinned environment required')
    art=root/'artifacts/demand_capacity_v1'
    release=json.loads((art/'release_manifest.json').read_text())
    input_files=[art/'coding_aggregate_and_forecasts.parquet',art/'lmm_aggregate_and_forecasts.parquet',art/'results.json']
    for p in input_files:
        if sha(p)!=release[p.name]:raise ValueError('v1 input changed '+str(p))
    oldfreeze=json.loads((art/'freeze.json').read_text())
    for name in ('demand_capacity_policy.py','evaluate_azure_code_input_demand.py'):
        expected=(oldfreeze['code'][name] if name in oldfreeze['code']
                  else oldfreeze['inputs'][str(root/'scripts'/name)])
        if sha(root/'scripts'/name)!=expected:raise ValueError('imported helper changed')
        input_files.append(root/'scripts'/name)
    protocol=Path(__file__).parents[1]/'reports/delay_capacity_protocol_v2.json'
    code_files=[Path(__file__),Path(__file__).with_name('delay_capacity_policy.py'),
                Path(__file__).parents[1]/'tests/test_delay_capacity_policy.py',protocol]
    out.mkdir(parents=True,exist_ok=False)
    save_json(out/'CODE_FREEZE.json',{
        'status':'EXPLORATORY_SIMULATION','inputs':{str(p):sha(p) for p in input_files},
        'code':{str(p):sha(p) for p in code_files},'protocol':json.loads(protocol.read_text()),
        'python':sys.version,'numpy':np.__version__,'sklearn':sklearn.__version__,
        'frozen_before_new_fit_and_policy_selection':True,
        'same_historical_assessment_already_exposed':True})
    return art


def forecast_metrics(target,p,delay):
    indices=np.arange(1152,2016-delay)
    return {'complete_peak_targets':len(indices),
            'pinball':float(pinball(target[indices],p[indices]).mean()),
            'coverage':float(np.mean(target[indices]<=p[indices])),
            'daily_coverage':[float(np.mean(target[t:min(t+288,2016-delay)]<=p[t:min(t+288,2016-delay)]))
                              for t in (1152,1440,1728)]}


def train_and_select(root,out,art):
    sys.path.insert(0,str(root/'scripts'))
    import evaluate_azure_code_input_demand as coding
    old=json.loads((art/'results.json').read_text())
    selections=[];packs={};model_files={}
    for name in ('coding','lmm'):
        previous=pd.read_parquet(art/(name+'_aggregate_and_forecasts.parquet'))
        y=previous.y.to_numpy(float)
        frame=design(y,name,coding);m=frame.scale.to_numpy(float)
        X=frame[list(FEATURES)].to_numpy(float)
        oldbaseline=old['primary'][name]['baseline']
        for delay in (1,2,6):
            target=peak_targets(y,delay)
            train=training_origins(delay)
            if (not np.isfinite(target[train]).all() or not (m[train]>=1000).all()
                    or train[-1]+delay>=864):raise ValueError('training target support')
            model=HistGradientBoostingRegressor(**PARAMS)
            weights=m[train]/m[train].mean()
            model.fit(X[train],target[train]/m[train],sample_weight=weights)
            file=out/f'{name}_delay{delay}_peak_q90.joblib'
            joblib.dump({'model':model,'features':FEATURES,'delay':delay,'parameters':PARAMS,
                         'train_origins':train,'last_training_label_available_bin':864},file,compress=3)
            model_files[file.name]=sha(file)
            raw=np.maximum(0.,model.predict(X))*m
            weighted=coding.weighted_quantile(target[train]/m[train],m[train])
            baseline_arrays={'train_weighted_peak_ratio_q90':weighted*m,
                             'past_hour_max':frame.past_hour_max_over_scale.to_numpy(float)*m,
                             'past_hour_empirical_q90':frame.past_hour_empirical_q90.to_numpy(float),
                             'past_hour_mean':m.copy()}
            val=np.arange(864,1152-delay)
            selected=min(BASELINES,key=lambda k:float(pinball(target[val],baseline_arrays[k][val]).mean()))
            forecasts={'v1_model_feedback':previous.model_feedback.to_numpy(float),
                       'v1_selected_baseline_feedback':previous[oldbaseline].to_numpy(float),
                       'peak_model_raw':raw,
                       'peak_model_feedback':mature_feedback(target,raw,m,delay),
                       'peak_baseline_feedback':mature_feedback(target,baseline_arrays[selected],m,delay)}
            for p in forecasts.values():
                if not np.isfinite(p[864:]).all() or (p[864:]<0).any():raise ValueError('invalid supported forecast')
            packs[(name,delay)]={'y':y,'scale':m,'target':target,'forecasts':forecasts,
                                'baseline':selected,'training_rows':len(train),'X':X}
            pd.DataFrame({'y':y,'scale':m,'peak_target':target,**forecasts}).to_parquet(out/f'{name}_delay{delay}_forecasts.parquet',index=False)
            for factor in (.5,1.,2.):
                capacity=float(np.median(y[12:864])/2*factor)
                initial=min(8,max(1,math.ceil(np.median(y[12:864])/capacity)))
                for method,p in forecasts.items():
                    candidates=[]
                    for dwell,confirm,headroom in itertools.product((0,6,12),(3,6),(.6,.8)):
                        hold=Hold(dwell,confirm,headroom)
                        rows=replay_delay(y[864:1152],p[864:1152],capacity,delay,initial,hold)
                        candidates.append({'hold':asdict(hold),'metrics':summarize(rows)})
                    choice=choose_hold(candidates)
                    selections.append({'dataset':name,'delay':delay,'capacity_factor':factor,'method':method,
                                       'baseline_forecast':selected,'selected_hold':choice['hold'],
                                       'validation_metrics':choice['metrics'],'candidates':candidates})
    save_json(out/'SELECTION_FREEZE.json',{'model_hashes':model_files,'policy_selections':selections,
                                         'selection_uses_only_training_and_validation':True,
                                         'assessment_policy_scores_computed':False})
    return packs,selections


def run(root,out):
    art=verify_inputs(root,out)
    packs,selections=train_and_select(root,out,art)
    selected={(s['dataset'],s['delay'],s['capacity_factor'],s['method']):s for s in selections}
    summaries=[];forecasts={};primaries={};ablations={}
    with gzip.open(out/'decision_replay.csv.gz','wt',newline='') as stream:
        writer=None
        for (name,delay),pack in packs.items():
            y=pack['y'];forecasts[f'{name}_delay{delay}']={
                'baseline':pack['baseline'],'training_rows':pack['training_rows'],
                'methods':{k:forecast_metrics(pack['target'],p,delay) for k,p in pack['forecasts'].items()}}
            for factor in (.5,1.,2.):
                capacity=float(np.median(y[12:864])/2*factor)
                initial=min(8,max(1,math.ceil(np.median(y[12:864])/capacity)))
                for method,p in pack['forecasts'].items():
                    sel=selected[(name,delay,factor,method)]
                    for variant in ('default_hold','validation_selected_hold'):
                        hold=Hold() if variant=='default_hold' else Hold(**sel['selected_hold'])
                        rows=replay_delay(y[864:],p[864:],capacity,delay,initial,hold,hold_start=288)
                        summary={'dataset':name,'delay':delay,'capacity_factor':factor,'method':method,
                                 'variant':variant,'hold':asdict(hold),'capacity_tokens_per_bin':capacity,
                                 **summarize(rows[288:])}
                        summaries.append(summary)
                        for r in rows[288:]:
                            item={'dataset':name,'delay':delay,'capacity_factor':factor,'method':method,
                                  'variant':variant,'absolute_bin':r['bin']+864,**r}
                            if writer is None:
                                writer=csv.DictWriter(stream,fieldnames=list(item));writer.writeheader()
                            writer.writerow(item)
    for name in ('coding','lmm'):
        subset={(r['method'],r['variant']):r for r in summaries if r['dataset']==name and r['delay']==1 and r['capacity_factor']==1}
        candidate=subset[('peak_model_feedback','validation_selected_hold')]
        baseline=subset[('peak_baseline_feedback','validation_selected_hold')]
        oldmodel=subset[('v1_model_feedback','default_hold')]
        default=subset[('peak_model_feedback','default_hold')]
        primary=(candidate['billed_slot_minutes']<baseline['billed_slot_minutes'] and
                 all(candidate[k]<=baseline[k]+1e-12 for k in ('shortfall_fraction','backlog_area_over_total_tokens','final_backlog_tokens','direction_reversals')))
        stability=(candidate['direction_reversals']<default['direction_reversals'] and
                   candidate['billed_slot_minutes']<=1.01*default['billed_slot_minutes'] and
                   all(candidate[k]<=default[k]+1e-12 for k in ('shortfall_fraction','backlog_area_over_total_tokens','final_backlog_tokens')))
        primaries[name]={'gate':'EXPLORATORY_PROXY_PASS' if primary else 'NO_GO',
                         'candidate':candidate,'strong_baseline':baseline,
                         'stability_gate':'EXPLORATORY_STABILITY_PASS' if stability else 'NO_GO',
                         'operational_adoption':False}
        ablations[name]={'v1_same_model_default':oldmodel,'new_peak_model_default':default,'new_peak_model_selected_hold':candidate}
    save_json(out/'results.json',{'status':'EXPLORATORY_SIMULATION','trained_models':6,'policy_comparisons':len(summaries),
                                 'primary':primaries,'ablations':ablations,'forecasts':forecasts,'scenarios':summaries,
                                 'evaluation_period_previously_exposed':True,'actual_vm_or_gpu_control':False,'actual_user_slo':False})
    pd.DataFrame([{k:v for k,v in r.items() if k!='hold'}|{f'hold_{k}':v for k,v in r['hold'].items()} for r in summaries]).to_csv(out/'scenario_summary.csv',index=False)
    lines=['AI-MCMP 준비 지연 대응 수요 학습과 축소 안정화 v2','',
           'Coding/LMM 각각 지연1/2/6구간의 최대 수요 q90 HGB를 새로 학습했다. 총6개 모델.',
           '학습852-delay행, 검증288-delay개 완전 미래 정답. 학습 정답은 첫3일 안에서 끝난다.',
           '1구간=5분. 타깃은 현재 구간부터 준비 완료 구간까지의 최대 입력 토큰이며 총합이 아니다.',
           '새 학습기는 과거 토큰 특징11개만 사용한다. LMM 이미지 특징 추가 이득을 검증한 실험이 아니다.',
           '피드백은 origin+delay+1까지 결과가 완료된 이전 validation 이후 예측만 사용한다.',
           '직전 최대288개 적격 창의 양수 정규화 잔차 q90 보정. 시간 지연 전달은 없다고 가정한다.',
           '유지시간0/30/60분, 연속 확인3/6회, 축소 여유0.6/0.8를 validation에서만 비교했다.',
           'validation 기본 정책 대비 비용대리값+1% 이내·부족/대기 비악화 안에서 방향 전환을 우선 줄인다.',
           '최종 hold는 평가 시작 때부터 적용한다. validation warmup을 선택된 hold로 소급 적용하지 않는다.',
           '정책은 +1/-1, 최소1 최대8 가상slot, cooldown10분이며 준비중 중복 증설을 막는다.',
           '용량/대기열/비용은 v1의 가상 토큰 유체 모형이다. 실제 GPU·VM·가격·사용자SLO 측정은 없다.',
           '같은 이미 노출된 평가기간을 이용했다. 모델과 정책을 고정한 후 비교했지만 독립 blind시험은 아니다.','']
    for name,p in primaries.items():
        a,b=p['candidate'],p['strong_baseline']
        lines.extend([name.upper()+': '+p['gate']+'; 안정화 '+p['stability_gate'],
                      '선택 hold: '+str(a['hold']),
                      f"강한 단순 기준선→우리 모델: slot-minutes {b['billed_slot_minutes']}→{a['billed_slot_minutes']}",
                      f"즉시 부족량/총 입력량 {b['shortfall_fraction']:.6f}→{a['shortfall_fraction']:.6f}",
                      f"누적 대기량/총 입력량 {b['backlog_area_over_total_tokens']:.6f}→{a['backlog_area_over_total_tokens']:.6f}",
                      f"방향 전환 {b['direction_reversals']}→{a['direction_reversals']}",
                      '예측 결과 및 전체9시나리오·분리된 절제 비교는 results.json/scenario_summary.csv를 참조한다.',''])
    lines.extend(['통과하지 못한 경우 기본 운영 정책으로 채택하지 않는다. PASS도 가상 모형의 탐색 결과다.',
                  '다음 단계는 새 기간에서 지연 대응 예측을 검증하고 실제 앱 처리용량·READY·사용자 결과를 연결하는 것이다.'])
    (out/'findings.txt').write_text('\n'.join(lines)+'\n',encoding='utf8')
    save_json(out/'manifest.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps({'trained_models':6,'policy_comparisons':len(summaries),'primary':primaries},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--research-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();run(args.research_root,args.output)
