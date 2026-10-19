"""Choose a few whole-model calibration probes using only known inputs.

The design targets prediction uncertainty across *all* workload conditions.
Its decision-weighted variant emphasizes conditions close to a predicted
latency/requests-per-second SLO boundary. It never reads candidate outcomes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def workload_basis(groups: pd.DataFrame) -> np.ndarray:
    """Seven ridge features for a model's known input-condition grid."""
    raw=np.column_stack((groups.log_batch_size.to_numpy(dtype=float),
                         groups.log_max_length.to_numpy(dtype=float),
                         np.log1p(groups.request_concurrency.to_numpy(dtype=float))))
    if not np.isfinite(raw).all():
        raise ValueError('Nonfinite workload design inputs')
    mean=raw.mean(axis=0)
    std=raw.std(axis=0)
    z=(raw-mean)/np.where(std>1e-8,std,1.)
    b,l,c=z.T
    return np.column_stack((np.ones(len(groups)),b,l,c,b*l,b*c,l*c))


def slo_boundary_weights(predicted_log_latency: np.ndarray,
                         predicted_log_requests_per_second: np.ndarray,
                         latency_slo_ms: np.ndarray,
                         target_requests_per_second: np.ndarray,
                         strength: float=4., bandwidth: float=.2) -> np.ndarray:
    """Weight workloads by distance to the predicted joint-feasibility boundary.

    Prediction arrays have shape (workloads, resource candidates); each SLO
    vector has one value per workload. Inputs are pre-probe predictions only.
    """
    latency=np.asarray(predicted_log_latency,dtype=float)
    throughput=np.asarray(predicted_log_requests_per_second,dtype=float)
    latency_slo=np.asarray(latency_slo_ms,dtype=float)
    target=np.asarray(target_requests_per_second,dtype=float)
    if latency.shape!=throughput.shape or latency.ndim!=2:
        raise ValueError('Performance predictions must have matching 2D shapes')
    if latency.shape[0]!=len(latency_slo) or len(latency_slo)!=len(target):
        raise ValueError('One SLO per workload is required')
    if not np.isfinite(latency).all() or not np.isfinite(throughput).all():
        raise ValueError('Nonfinite predictions')
    if (latency_slo<=0).any() or (target<=0).any() or strength<0 or bandwidth<=0:
        raise ValueError('SLOs and bandwidth must be positive; strength nonnegative')
    latency_slack=np.log(latency_slo)[:,None]-latency
    throughput_slack=throughput-np.log(target)[:,None]
    joint_slack=np.minimum(latency_slack,throughput_slack)
    distance=np.min(np.abs(joint_slack),axis=1)
    return 1.+strength*np.exp(-distance/bandwidth)


def select_integrated_variance(features: np.ndarray,k: int,
                               importance: np.ndarray|None=None,
                               predicted_cost: np.ndarray|None=None,
                               ridge: float=1.) -> np.ndarray:
    """Greedily reduce weighted total ridge prediction variance per cost.

    At each step, the score for candidate i is
      sum_j weight_j * (x_j^T C x_i)^2 / (1 + x_i^T C x_i) / cost_i,
    where C=(ridge*I+sum_{selected} x_i*x_i^T)^-1. Only known workload
    features, predicted decision importance, and predicted probe cost enter.
    """
    x=np.asarray(features,dtype=float)
    if x.ndim!=2 or not np.isfinite(x).all():
        raise ValueError('Features must be finite and two-dimensional')
    n,p=x.shape
    if not 0<=k<=n or ridge<=0:
        raise ValueError('Invalid budget or ridge penalty')
    w=np.ones(n) if importance is None else np.asarray(importance,dtype=float)
    cost=np.ones(n) if predicted_cost is None else np.asarray(predicted_cost,dtype=float)
    if w.shape!=(n,) or cost.shape!=(n,) or not np.isfinite(w).all() or not np.isfinite(cost).all():
        raise ValueError('Importance and cost must have one finite value per workload')
    if (w<0).any() or (cost<=0).any():
        raise ValueError('Importance must be nonnegative and cost positive')
    covariance=np.eye(p)/ridge
    selected=[]
    available=np.ones(n,dtype=bool)
    for _ in range(k):
        cross=x @ covariance @ x.T
        weighted_reduction=(w[:,None]*cross**2).sum(axis=0)
        score=weighted_reduction/(1.+np.diag(cross))/cost
        score[~available]=-np.inf
        i=int(np.argmax(score))
        selected.append(i)
        available[i]=False
        v=covariance @ x[i]
        covariance-=np.outer(v,v)/(1.+x[i]@v)
    return np.asarray(selected,dtype=int)
