"""Reproducible comparison of the exploratory anchor-conditioned studies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from conditional_response import ROOT

PRIMARY='khu-hf-local-exhaustive'
COLORS={
    'Static + anchor shift':'#767c86',
    'Anchor-conditioned ExtraTrees':'#2274a5',
    'Anchor-conditioned RandomForest':'#39a275',
    'Direct ExtraTrees':'#c76d3e',
}


def paired_model_comparison(conditional: pd.DataFrame, strong: pd.DataFrame, seed=42):
    source=pd.concat([conditional,strong],ignore_index=True)
    source=source[source.campaign==PRIMARY]
    methods={
        'extra_trees_static_anchor_shift':'Static + anchor shift',
        'anchor_conditioned':'Anchor-conditioned ExtraTrees',
        'random_forest_residual':'Anchor-conditioned RandomForest',
        'extra_trees_direct':'Direct ExtraTrees',
    }
    source=source[source.method.isin(methods)].copy()
    source['label']=source.method.map(methods)
    assert source.groupby('label').model_id.nunique().eq(9).all()
    result={}
    for metric in ('latency_mae_log','throughput_mae_log'):
        table=source.pivot(index='model_id',columns='label',values=metric)
        if table.isna().any().any() or len(table)!=9:
            raise ValueError('Each method requires the same nine held-out models')
        reference=table['Anchor-conditioned ExtraTrees']
        for label in methods.values():
            delta=(table[label]-reference).to_numpy()
            rng=np.random.default_rng(seed)
            samples=delta[rng.integers(0,len(delta),size=(20000,len(delta)))].mean(axis=1)
            result[f'{metric}/{label}']={
                'macro_mae_log':float(table[label].mean()),
                'baseline_minus_conditional':float(delta.mean()),
                'model_bootstrap_95_percent_ci_exploratory':np.quantile(samples,[.025,.975]).tolist(),
                'conditional_better_models':int((delta>0).sum()),
                'heldout_models':len(delta),
            }
    return result


def main():
    output=ROOT/'artifacts/novel_method_summary_v1'
    output.mkdir(parents=True,exist_ok=True)
    conditional_path=ROOT/'artifacts/conditional_response_v1/per_model_metrics.csv'
    strong_path=ROOT/'artifacts/strong_conditional_baseline_v1/per_model_metrics.csv'
    conditional=pd.read_csv(conditional_path)
    strong=pd.read_csv(strong_path)
    comparisons=paired_model_comparison(conditional,strong)
    decision=json.loads((ROOT/'artifacts/novel_decision_v1/metrics.json').read_text())
    strong_result=json.loads((ROOT/'artifacts/strong_conditional_baseline_v1/metrics.json').read_text())
    classifier=json.loads((ROOT/'artifacts/probe_classifier_v1/metrics.json').read_text())
    crossfit=json.loads((ROOT/'artifacts/crossfit_anchor_calibration_v1/metrics.json').read_text())
    budgets=[25,50,100,150,200]
    curves={
        'Static + anchor shift':[strong_result['verified_second_probe_recoveries']
                                 ['extra_trees_static_anchor_shift'][str(k)] for k in budgets],
        'Anchor-conditioned ExtraTrees':[decision['probe_budget']['ranked']
                                         ['anchor_conditioned']['fixed_budget'][str(k)]
                                         ['verified_recoveries'] for k in budgets],
        'Anchor-conditioned RandomForest':[strong_result['verified_second_probe_recoveries']
                                           ['random_forest_residual'][str(k)] for k in budgets],
        'Direct ExtraTrees':[strong_result['verified_second_probe_recoveries']
                             ['extra_trees_direct'][str(k)] for k in budgets],
        'Direct feasibility classifier':[classifier['curves'][str(k)]
                                         ['verified_recoveries'] for k in budgets],
    }
    fig,axes=plt.subplots(1,3,figsize=(16,4.4),layout='constrained')
    labels=list(COLORS)
    for axis,metric,title in zip(axes[:2],['latency_mae_log','throughput_mae_log'],
                                 ['Latency prediction','Requests/s prediction']):
        values=[comparisons[f'{metric}/{label}']['macro_mae_log'] for label in labels]
        axis.barh(labels,values,color=[COLORS[label] for label in labels])
        axis.invert_yaxis()
        axis.set_xlabel('Mean absolute log error; lower is better')
        axis.set_title(title)
        axis.set_xlim(0,max(values)*1.15)
        for i,value in enumerate(values):
            axis.text(value+.004,i,f'{value:.3f}',va='center',fontsize=9)
    for label,values in curves.items():
        axes[2].plot(budgets,values,marker='o',label=label,
                     color=COLORS.get(label,'#9b65a5'))
    axes[2].axhline(108,color='#888888',linestyle=':',linewidth=1)
    axes[2].set_xlabel('Additional GPU-2 measurements')
    axes[2].set_ylabel('Verified feasible workloads of 108')
    axes[2].set_title('Second-probe budget')
    axes[2].legend(fontsize=7,loc='lower right')
    fig.suptitle('Held-out AI models on one local GPU host (exploratory)')
    fig.savefig(output/'comparison.png',dpi=180)
    fig.savefig(output/'comparison.pdf')
    plt.close(fig)
    source=ROOT/'data/audit_v1/measurements.parquet'
    result={
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'paired_prediction':comparisons,
        'probe_budgets':budgets,
        'verified_recoveries':curves,
        'crossfit_rejected':{
            'latency_mae_log':crossfit['prediction'][f'{PRIMARY}/crossfit_anchor_error']
                               ['latency_macro_mae_log'],
            'throughput_mae_log':crossfit['prediction'][f'{PRIMARY}/crossfit_anchor_error']
                                  ['throughput_macro_mae_log'],
            'top_100_recoveries':crossfit['ranking'][f'{PRIMARY}/crossfit_anchor_error']
                                 ['top_k_recoveries']['100'],
        },
        'notes':'Model bootstrap is descriptive after exploratory method selection; plotted probe outcomes are measured offline.'
    }
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
