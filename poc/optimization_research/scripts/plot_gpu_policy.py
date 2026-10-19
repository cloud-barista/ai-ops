"""Create a standalone figure for the GPU policy experiment."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from train_performance import ROOT


def main():
    source=ROOT/'artifacts/gpu_policy_v1/metrics.json'
    metrics=json.loads(source.read_text())
    policies=['cheapest','point','row_conformal_90','group_conformal_90','adaptive_group_90','oracle']
    labels=['Cheapest','Point','Row 90%','Group 90%','Adaptive 90%','Measured oracle']
    datasets=[('Original grid',metrics['test_policies'])]
    for name,value in metrics['external_stress_tests'].items():
        datasets.append((name.replace('khu-hf-local-',''),value['policies']))
    fig,axes=plt.subplots(1,3,figsize=(13,4),sharey=False,constrained_layout=True)
    for ax,(name,results) in zip(axes,datasets):
        xs=np.arange(len(policies)); selected=[results[k]['selected'] for k in policies]
        fails=[results[k]['violations'] for k in policies]
        ax.bar(xs,selected,color=['#94a3b8','#2563eb','#60a5fa','#047857','#10b981','#a855f7'])
        ax.bar(xs,fails,color='#dc2626',label='Observed violation')
        ax.set_title(name)
        ax.set_xticks(xs,labels,rotation=38,ha='right')
        ax.set_ylim(0,results['cheapest']['groups']*1.10)
        ax.grid(axis='y',alpha=.25)
        ax.text(.03,.97,f"n={results['point']['groups']}, oracle feasible={results['oracle']['selected']}",
                transform=ax.transAxes,va='top',fontsize=9)
        ax.set_ylabel('Selected workload groups')
    axes[0].legend(loc='upper right',fontsize=8)
    fig.suptitle('GPU configuration selection, observed SLO outcomes')
    fig.savefig(ROOT/'reports/gpu_policy_v1.png',dpi=180)
    plt.close(fig)


if __name__=='__main__':
    main()
