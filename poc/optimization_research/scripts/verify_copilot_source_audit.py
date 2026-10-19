"""Verify a stopped token-transfer experiment; retain complete call-arrival labels."""
import argparse
import gzip
import hashlib
import json
import tarfile
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
from copilot_demand_transfer import verify_dependencies
from train_evaluate_delay_capacity import sha


def aggregate_all_occurrences(paths):
    known_tokens=np.zeros(864,dtype=np.int64);known_calls=known_tokens.copy();all_calls=known_tokens.copy()
    types=Counter();statuses=Counter();first_audit={};exact=0;groups=set();reused=0;outside=0;calls=0
    start=pd.Timestamp('2026-06-05T00:00:00Z').value
    for path in paths:
        with tarfile.open(path,'r|gz') as archive:
            for entry in archive:
                if not entry.isfile() or not entry.name.endswith('.jsonl.gz'):continue
                timestamps=[];prompts=[];results=[]
                with archive.extractfile(entry) as raw,gzip.GzipFile(fileobj=raw) as stream:
                    for line in stream:
                        row=json.loads(line);seen=set()
                        for turn in row['turns']:
                            for call in turn['llm_calls']:
                                calls+=1;timestamps.append(call['timestamp']);prompts.append(call.get('tokens',{}).get('prompt'))
                                results.append(str(call.get('result')))
                                group=(row['session_id'],turn['turn_id'],call['message_id'])
                                reused+=int(group in groups);groups.add(group)
                                payload=json.dumps([row['session_id'],turn['turn_id'],call],sort_keys=True,separators=(',',':'))
                                digest=hashlib.sha256(payload.encode()).digest();exact+=int(digest in seen);seen.add(digest)
                if not timestamps:continue
                stamps=pd.to_datetime(timestamps,format='mixed',utc=True,errors='raise')
                bins=((stamps.asi8-start)//(300*10**9)).astype(np.int64)
                valid_time=(bins>=0)&(bins<864);outside+=int((~valid_time).sum())
                valid_prompt=np.array([isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in prompts])
                use=valid_time&valid_prompt
                amounts=np.array([v if ok else 0 for v,ok in zip(prompts,valid_prompt)],dtype=np.int64)
                np.add.at(known_tokens,bins[use],amounts[use]);np.add.at(known_calls,bins[use],1)
                np.add.at(all_calls,bins[valid_time],1)
                for p,status,bad in zip(prompts,results,valid_time&~valid_prompt):
                    if bad:types[type(p).__name__]+=1;statuses[status]+=1
        print('Independent source audit completed '+path.name,flush=True)
    return known_tokens,known_calls,all_calls,{'llm_calls':calls,'out_of_window_calls':outside,
        'repeated_grouping_ids':reused,'exact_repeated_call_records':exact,
        'invalid_prompt_calls':sum(types.values()),'invalid_prompt_types':dict(types),
        'invalid_prompt_status':dict(statuses)}


def verify(root,stage):
    out=stage/'artifacts/copilot_transfer_v1_1';freeze=json.loads((out/'PRE_SCORE_FREEZE.json').read_text())
    verify_dependencies(root,stage,freeze)
    assert sha(stage/'scripts/copilot_demand_transfer.py')==freeze['code_hash']
    paths=[stage/'data'/a['name'] for a in freeze['assets']]
    for p,a in zip(paths,freeze['assets']):
        assert p.stat().st_size==a['size'] and sha(p)==a['digest'].split(':')[1]
    y,c,allc,a=aggregate_all_occurrences(paths)
    primary=json.loads((out/'data_audit.json').read_text());saved=pd.read_parquet(out/'five_minute_counts.parquet')
    np.testing.assert_array_equal(y,saved.prompt_tokens);np.testing.assert_array_equal(c,saved.llm_calls)
    for k in ('llm_calls','out_of_window_calls','repeated_grouping_ids','exact_repeated_call_records','invalid_prompt_calls'):
        assert a[k]==primary[k],(k,a[k],primary[k])
    assert allc.sum()==a['llm_calls']-a['out_of_window_calls']
    assert allc.sum()-c.sum()==a['invalid_prompt_calls']
    quality=a['exact_repeated_call_records']==0 and a['invalid_prompt_calls']<=.01*a['llm_calls']
    m=pd.Series(y).shift(1).rolling(12,min_periods=12).mean().to_numpy()
    support={str(d):int(np.sum(m[d*288:(d+1)*288]>=1000)) for d in (1,2)}
    stopped=json.loads((out/'support_no_go.json').read_text())
    assert stopped['quality']==quality and stopped['daily_support']==support
    assert not quality or any(v<274 for v in support.values())
    assert not (out/'results.json').exists() and not (out/'BASELINE_SELECTION.json').exists()
    pd.DataFrame({'timestamp_utc':pd.date_range('2026-06-05',periods=864,freq='5min',tz='UTC'),
        'recorded_llm_calls':allc,'calls_with_known_prompt':c,'calls_with_unknown_or_invalid_prompt':allc-c,
        'known_prompt_tokens_lower_bound':y}).to_parquet(out/'complete_call_counts_and_known_tokens.parquet',index=False)
    result={'status':'VERIFIED_SOURCE_LABEL_NO_GO','raw_sha256_and_sizes_match_provider':True,
        'all_864_known_token_and_call_bins_exact':True,'complete_call_count_labels_preserved':True,
        'audit':a,'unknown_prompt_fraction_of_all_calls':a['invalid_prompt_calls']/a['llm_calls'],
        'unmodified_original_1percent_quality_gate_failed':not quality,
        'no_external_forecast_or_performance_score_computed':True,
        'known_tokens_are_lower_bound_not_true_full_prompt_demand':True,
        'available_tasks':['recorded LLM call arrival forecasting','prompt count missingness audit'],
        'unsupported_tasks':['complete prompt-token demand transfer under original gate','GPU/VM requirement or scaling effect'],
        'verifier_sha256':sha(Path(__file__))}
    with (out/'independent_source_audit.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--research-root',type=Path,required=True);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();verify(a.research_root,a.stage)
