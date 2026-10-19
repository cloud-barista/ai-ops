"""Fresh Copilot metadata trace: audit and frozen peak-demand model transfer."""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import orjson
import sklearn
from delay_capacity_policy import peak_targets,mature_feedback
from train_evaluate_delay_capacity import FEATURES,sha,pinball

DATES=('2026-06-05','2026-06-06','2026-06-07')
START=dt.datetime(2026,6,5,tzinfo=dt.timezone.utc)
N=864


def save(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def time_bin(value):
    if not isinstance(value,str):raise ValueError('timestamp must be ISO string')
    # Python 3.9 accepts microseconds; truncate finer precision without rounding
    # across the five-minute boundary, and pad short fractional seconds.
    normalized=re.sub(r'([T ][0-9]{2}:[0-9]{2}:[0-9]{2})\.(\d+)',
                      lambda m:m.group(1)+'.'+(m.group(2)+'000000')[:6],value)
    moment=dt.datetime.fromisoformat(normalized.replace('Z','+00:00'))
    naive=moment.tzinfo is None
    if naive:moment=moment.replace(tzinfo=dt.timezone.utc)
    delta=moment-START
    return (delta.days*86400+delta.seconds)//300,naive


def features(y):
    a=pd.Series(np.asarray(y,float));past=a.shift(1);m=past.rolling(12,min_periods=12).mean()
    ratio=a/m.where(m>=1000)
    stamps=pd.date_range(START,periods=len(a),freq='5min')
    clock=stamps.hour.to_numpy()*12+stamps.minute.to_numpy()//5
    hourq=past.rolling(12,min_periods=12).quantile(.9)
    hourmax=past.rolling(12,min_periods=12).max()
    older=a.shift(7).rolling(6,min_periods=6).mean()
    frame=pd.DataFrame({'scale':m,'lag1_over_scale':past/m,'lag2_over_scale':a.shift(2)/m,
                        'lag3_over_scale':a.shift(3)/m,'lag12_over_scale':a.shift(12)/m,
                        'previous_day_ratio':ratio.shift(288),'past_hour_max_over_scale':hourmax/m,
                        'past_hour_q90_over_scale':hourq/m,
                        'recent_six_over_previous_six':past.rolling(6,min_periods=6).mean()/older.where(older>0),
                        'clock_sin':np.sin(2*np.pi*clock/288),'clock_cos':np.cos(2*np.pi*clock/288),
                        'weekend':(stamps.dayofweek.to_numpy()>=5).astype(float),
                        'hour_max':hourmax,'hour_q90':hourq,'hour_mean':m})
    return frame.replace([np.inf,-np.inf],np.nan)


def aggregate(paths):
    tokens=np.zeros(N,dtype=np.int64);counts=np.zeros(N,dtype=np.int64)
    identities=set();sessions=set();audit={'sessions':0,'llm_calls':0,'eligible_window_calls':0,
       'out_of_window_calls':0,'invalid_prompt_calls':0,'naive_timestamp_calls':0,'zero_prompt_calls':0,
       'repeated_grouping_ids':0,'exact_repeated_call_records':0,'partitions':{},'cached_larger_than_prompt_calls':0}
    for path in paths:
        partition=path.name[len('date.'):-len('.tar.gz')];part={'sessions':0,'llm_calls':0,'shards':0}
        with tarfile.open(path,'r|gz') as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith('.jsonl.gz'):continue
                if f'date={partition}/' not in member.name:raise ValueError('unexpected archive partition')
                part['shards']+=1
                with archive.extractfile(member) as compressed:
                    with gzip.GzipFile(fileobj=compressed) as stream:
                        for line in stream:
                            row=orjson.loads(line);sid=row['session_id']
                            if row['date']!=partition:raise ValueError('session date does not match partition')
                            audit['sessions']+=1;part['sessions']+=1;sessions.add(sid)
                            record_hashes=set()
                            for turn in row['turns']:
                                for call in turn['llm_calls']:
                                    part['llm_calls']+=1;audit['llm_calls']+=1
                                    identity=orjson.dumps([sid,turn['turn_id'],call['message_id']])
                                    identity=hashlib.sha256(identity).digest()
                                    if identity in identities:
                                        audit['repeated_grouping_ids']+=1
                                    identities.add(identity)
                                    fingerprint=hashlib.sha256(orjson.dumps([sid,turn['turn_id'],call],option=orjson.OPT_SORT_KEYS)).digest()
                                    if fingerprint in record_hashes:audit['exact_repeated_call_records']+=1
                                    record_hashes.add(fingerprint)
                                    slot,naive=time_bin(call['timestamp'])
                                    audit['naive_timestamp_calls']+=int(naive)
                                    if not 0<=slot<N:
                                        audit['out_of_window_calls']+=1;continue
                                    prompt=call.get('tokens',{}).get('prompt')
                                    if isinstance(prompt,bool) or not isinstance(prompt,int) or prompt<0:
                                        audit['invalid_prompt_calls']+=1;continue
                                    cached=call.get('tokens',{}).get('cached')
                                    if isinstance(cached,int) and cached>prompt:audit['cached_larger_than_prompt_calls']+=1
                                    audit['zero_prompt_calls']+=int(prompt==0)
                                    tokens[slot]+=prompt;counts[slot]+=1;audit['eligible_window_calls']+=1
        audit['partitions'][partition]=part
        print(json.dumps({'completed_partition':partition,**part}),flush=True)
    audit['distinct_session_ids']=len(sessions)
    audit['grouping_id_method']='SHA256 of [session_id,turn_id,message_id]; reused values are NOT call identities'
    audit['exact_record_method']='SHA256 canonical [session_id,turn_id,full_call_payload] within session; all occurrences counted'
    audit['prompt_sum']=int(tokens.sum());audit['nonempty_bins']=int((counts>0).sum())
    if int(counts.sum())!=audit['eligible_window_calls']:raise ValueError('aggregation count mismatch')
    return tokens,counts,audit


def prepare(root,stage):
    stage=Path(stage);data=stage/'data';out=stage/'artifacts/copilot_transfer_v1_1'
    metadata=json.loads(Path('/tmp/ghcp_release_metadata.json').read_text())
    assets={a['name']:a for a in metadata['assets']}
    models=json.loads((root/'artifacts/delay_capacity_v2/SELECTION_FREEZE.json').read_text())['model_hashes']
    for name,digest in models.items():
        if sha(root/'artifacts/delay_capacity_v2'/name)!=digest:raise ValueError('source model changed')
    selected=[]
    for date in DATES:
        asset=assets[f'date.{date}.tar.gz']
        selected.append({k:asset[k] for k in ('name','size','digest','browser_download_url')})
        if (data/asset['name']).exists():raise ValueError('raw data already present before freeze')
    if sum(a['size'] for a in selected)>900_000_000:raise ValueError('download budget exceeded')
    out.mkdir(parents=True,exist_ok=False)
    protocol=stage/'reports/copilot_transfer_protocol_v1.json'
    save(out/'PRE_SCORE_FREEZE.json',{'assets':selected,'release_id':metadata['id'],
        'frozen_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'release_metadata_sha256':sha('/tmp/ghcp_release_metadata.json'),'source_model_hashes':models,
        'helper_hashes':{name:sha(root/'scripts'/name) for name in ('delay_capacity_policy.py','train_evaluate_delay_capacity.py','demand_capacity_policy.py')},
        'training_forecast_hashes':{name:sha(root/'artifacts/delay_capacity_v2'/f'{name}_delay1_forecasts.parquet') for name in ('coding','lmm')},
        'code_hash':sha(Path(__file__)),'test_hash':sha(stage/'tests/test_copilot_demand_transfer.py'),
        'verifier_hash':sha(stage/'scripts/verify_copilot_demand_transfer.py'),
        'protocol':json.loads(protocol.read_text()),'protocol_sha256':sha(protocol),
        'frozen_before_any_raw_download':False,'frozen_before_any_forecast_score':True,
        'v1_schema_failure_previously_inspected':True,
        'original_pre_download_freeze_sha256':sha('/tmp/ai_mcmp_copilot_transfer_v1/artifacts/copilot_transfer_v1/PRE_DOWNLOAD_FREEZE.json'),
        'original_schema_audit_sha256':sha('/tmp/ai_mcmp_copilot_transfer_v1/artifacts/copilot_transfer_v1/data_audit.json'),'v1_source_id_assumption_rejected':True,'python':sys.version,'numpy':np.__version__,'sklearn':sklearn.__version__})
    print(json.dumps({'frozen':True,'download_bytes':sum(a['size'] for a in selected),'assets':[a['name'] for a in selected]}))


def score(root,stage):
    stage=Path(stage);out=stage/'artifacts/copilot_transfer_v1_1'
    freeze=json.loads((out/'PRE_SCORE_FREEZE.json').read_text())
    if sha(Path(__file__))!=freeze['code_hash']:raise ValueError('code changed after freeze')
    verify_dependencies(root,stage,freeze)
    if sklearn.__version__!='1.6.1' or np.__version__!='2.0.2':raise ValueError('use pinned environment')
    paths=[]
    for asset in freeze['assets']:
        path=stage/'data'/asset['name']
        if path.stat().st_size!=asset['size'] or sha(path)!=asset['digest'].split(':')[1]:raise ValueError('raw archive hash mismatch')
        paths.append(path)
    y,counts,audit=aggregate(paths)
    save(out/'data_audit.json',audit)
    pd.DataFrame({'prompt_tokens':y,'llm_calls':counts}).to_parquet(out/'five_minute_counts.parquet',index=False)
    quality=(audit['exact_repeated_call_records']==0 and audit['invalid_prompt_calls']<=.01*max(1,audit['llm_calls']))
    frame=features(y);m=frame.scale.to_numpy(float)
    daily_support={str(day):int(np.sum(m[day*288:(day+1)*288]>=1000)) for day in (1,2)}
    if not quality or any(v<274 for v in daily_support.values()):
        save(out/'support_no_go.json',{'status':'SUPPORT_NO_GO_NO_SCORE','quality':quality,'daily_support':daily_support})
        print('SUPPORT_NO_GO_NO_SCORE');return
    all_results={};newforecasts={'y':y,'scale':m};selection={};packs={}
    for delay in (1,2,6):
        target=peak_targets(y,delay);baselines={
            'past_hour_max':frame.hour_max.to_numpy(float),
            'past_hour_q90':frame.hour_q90.to_numpy(float),'past_hour_mean':m.copy()}
        for source in ('coding','lmm'):
            path=root/'artifacts/delay_capacity_v2'/f'{source}_delay{delay}_peak_q90.joblib'
            if sha(path)!=freeze['source_model_hashes'][path.name]:raise ValueError('source model changed')
            bundle=joblib.load(path)
            if bundle['features']!=FEATURES or bundle['delay']!=delay:raise ValueError('model interface mismatch')
            raw=np.maximum(0.,bundle['model'].predict(frame[list(FEATURES)].to_numpy(float)))*m
            newforecasts[f'{source}_d{delay}_raw']=raw
            newforecasts[f'{source}_d{delay}_feedback']=mature_feedback(target,raw,m,delay,first_origin=12)
            sourceframe=pd.read_parquet(root/'artifacts/delay_capacity_v2'/f'{source}_delay1_forecasts.parquet')
            origins=np.arange(12,864-delay)
            sy=sourceframe.y.to_numpy(float);sm=sourceframe.scale.to_numpy(float)
            ratio=peak_targets(sy,delay)[origins]/sm[origins]
            order=np.argsort(ratio,kind='mergesort');w=sm[origins][order]
            q=ratio[order[min(int(np.searchsorted(np.cumsum(w),.9*w.sum(),side='left')),len(w)-1)]]
            baselines[source+'_source_peak_q90']=q*m
        calibration=np.arange(12,288-delay)
        calibration=calibration[m[calibration]>=1000]
        chosen=min(baselines,key=lambda k:float(pinball(target[calibration],baselines[k][calibration]).mean()))
        reference=mature_feedback(target,baselines[chosen],m,delay,first_origin=12)
        newforecasts[f'baseline_d{delay}_feedback']=reference
        selection[str(delay)]={'baseline':chosen,'calibration_targets':len(calibration),'origin_end_exclusive':288-delay}
        packs[delay]=(target,reference,chosen)
    # Persist the calibration-only selection before computing evaluation scores.
    save(out/'BASELINE_SELECTION.json',selection)
    for delay in (1,2,6):
        target,reference,chosen=packs[delay]
        for source in ('coding','lmm'):
            key=f'{source}_d{delay}';p=newforecasts[key+'_feedback'];raw=newforecasts[key+'_raw']
            perday=[]
            for day in (1,2):
                indices=np.arange(day*288,min((day+1)*288,N-delay))
                indices=indices[m[indices]>=1000]
                a=float(pinball(target[indices],p[indices]).mean());b=float(pinball(target[indices],reference[indices]).mean())
                perday.append({'day':day,'bins':len(indices),'pinball':a,'baseline_pinball':b,
                               'raw_pinball':float(pinball(target[indices],raw[indices]).mean()),
                               'coverage':float(np.mean(target[indices]<=p[indices])),
                               'baseline_coverage':float(np.mean(target[indices]<=reference[indices]))})
            gain=1-np.mean([d['pinball'] for d in perday])/np.mean([d['baseline_pinball'] for d in perday])
            passed=(gain>=.05 and all(d['pinball']<d['baseline_pinball'] and .85<=d['coverage']<=.95 for d in perday))
            all_results[key]={'gate':'EXTERNAL_SAMPLE_PASS' if passed else 'NO_GO','relative_pinball_reduction':float(gain),
                              'days':perday,'baseline':chosen,'source_model_unchanged':True}
    pd.DataFrame(newforecasts).to_parquet(out/'predictions.parquet',index=False)
    result={'status':'EXTERNAL_SERVICE_SAMPLE_EVALUATION','source':'GHCP June5-7 selected session partitions',
            'models_retrained':False,'calibration_day':'2026-06-05','evaluation_days':['2026-06-06','2026-06-07'],
            'support':daily_support,'methods':all_results,'actual_scale_action_or_user_slo_measured':False,
            'label_availability_assumed_immediate_at_bin_close':True,'operational_adoption':False}
    save(out/'results.json',result)
    text=['Copilot 외부 표본에서 지연 대응 수요 모델 검증','',
          '공식 일별 자료 중 2026-06-05~07만 받았다. 원본 tar.gz는 압축 상태로 보존했다.',
          '각 archive 내부 JSONL gzip을 순서대로 읽고 5분 prompt 토큰 합계를 집계했다.',
          '대상은 선택한 날짜 파티션의 세션 표본이다. 전체 Copilot 서비스 수요나 사용자 SLO가 아니다.',
          'cached 토큰을 포함한 prompt 수요다. 캐시를 제외한 GPU 연산량이나 메모리 요구량으로 해석하지 않는다.',
          '6개 기존 모델은 재학습하지 않았다. 5일만 기준선 선택에 쓰고 6~7일을 평가했다.',
          '평가 중 이전 완료 구간의 정답으로 온라인 잔차 보정을 계속했다. 모델 가중치는 고정했다.',
          '기록된 timestamp를 호출 시작 시각으로 해석하고 prompt를 시작 시점에 알 수 있다고 가정했다.',
          '실제 available_at은 없으므로 즉시 가용 가정을 사용했다.',
          '파티션을 일부만 받아 다른 날짜 파티션의 세션에 속한 호출은 포함하지 못한다.',
          'PASS 기준: 평균 pinball 5% 이상 감소, 두 날짜 모두 기준선보다 낮은 손실, 날짜별 피복률 85~95%.','']
    for key,r in all_results.items():
        text.append(f"{key}: {r['gate']}; 상대 손실 감소 {r['relative_pinball_reduction']*100:.2f}%")
        text.append('일별 수치: '+str(r['days']))
    text.extend(['','독립 서비스의 새 표본 확인이다. 두 인접 날짜와 6개 비교로 통계적 유의성·범용 일반화를 주장하지 않는다.',
                 '운영 채택은 false다. VM·GPU 수와 실제 Scale-out/in 효과는 검증하지 않았다.'])
    (out/'findings.txt').write_text('\n'.join(text)+'\n',encoding='utf8')
    verify_dependencies(root,stage,freeze)
    save(out/'manifest.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps({'methods':all_results,'data_audit':audit},ensure_ascii=False))


def verify_dependencies(root,stage,freeze):
    for name,digest in freeze['helper_hashes'].items():
        if sha(root/'scripts'/name)!=digest:raise ValueError('source helper changed '+name)
    for name,digest in freeze['training_forecast_hashes'].items():
        if sha(root/'artifacts/delay_capacity_v2'/f'{name}_delay1_forecasts.parquet')!=digest:
            raise ValueError('source training forecast changed '+name)
    for name,digest in freeze['source_model_hashes'].items():
        if sha(root/'artifacts/delay_capacity_v2'/name)!=digest:raise ValueError('source model changed '+name)
    for path,key in ((stage/'tests/test_copilot_demand_transfer.py','test_hash'),
                     (stage/'scripts/verify_copilot_demand_transfer.py','verifier_hash'),
                     (stage/'reports/copilot_transfer_protocol_v1.json','protocol_sha256')):
        if sha(path)!=freeze[key]:raise ValueError('frozen input changed '+str(path))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','score']);ap.add_argument('--research-root',type=Path,required=True);ap.add_argument('--stage',type=Path,required=True)
    args=ap.parse_args();(prepare if args.mode=='prepare' else score)(args.research_root,args.stage)
