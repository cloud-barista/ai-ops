"""Delay-aligned peak forecast feedback and bounded abstract capacity control."""
from dataclasses import dataclass
import math
import numpy as np
from demand_capacity_policy import Config, State, decide


def peak_targets(y, horizon):
    """Origin t covers y[t:t+h+1]; label is available at t+h+1."""
    y = np.asarray(y, float)
    if (y.ndim != 1 or not len(y) or not np.isfinite(y).all() or (y < 0).any()
            or isinstance(horizon,bool) or not isinstance(horizon,int)
            or not 0 <= horizon < len(y)):
        raise ValueError('invalid target input')
    result = np.full(len(y), np.nan)
    for t in range(len(y)-horizon): result[t] = np.max(y[t:t+horizon+1])
    return result


def mature_feedback(target, prediction, scale, horizon, first_origin=864,
                    window=288, minimum=96):
    """Only prior origin i with i+h+1 <= current origin is eligible."""
    target,p,m = (np.asarray(v,float) for v in (target,prediction,scale))
    if (target.ndim != 1 or len(target)!=len(p) or len(p)!=len(m)
            or not 0 <= horizon < len(target) or not 0 <= first_origin < len(target)):
        raise ValueError('incompatible feedback input')
    corrected=p.copy()
    for t in range(first_origin,len(p)):
        end=t-horizon
        left=max(first_origin,end-window)
        if end <= left: continue
        valid=(np.isfinite(target[left:end]) & np.isfinite(p[left:end])
               & np.isfinite(m[left:end]) & (m[left:end]>=1000))
        if valid.sum()>=minimum and np.isfinite(p[t]) and m[t]>=1000:
            residual=(target[left:end][valid]-p[left:end][valid])/m[left:end][valid]
            corrected[t]+=m[t]*max(0.,float(np.quantile(residual,.9)))
    return corrected


@dataclass(frozen=True)
class Hold:
    dwell_bins: int = 0
    confirmations: int = 3
    headroom: float = .8

    def __post_init__(self):
        if (isinstance(self.dwell_bins,bool) or not isinstance(self.dwell_bins,int)
                or self.dwell_bins < 0 or isinstance(self.confirmations,bool)
                or not isinstance(self.confirmations,int) or self.confirmations < 1
                or not math.isfinite(self.headroom) or not 0 < self.headroom < 1):
            raise ValueError('invalid hold settings')


def replay_delay(y, forecasts, capacity, delay, initial_slots, hold=Hold(), hold_start=0):
    y,p = np.asarray(y,float),np.asarray(forecasts,float)
    if (len(y)!=len(p) or not len(y) or not np.isfinite(y).all() or (y<0).any()
            or isinstance(initial_slots,bool) or not isinstance(initial_slots,int)
            or not 1 <= initial_slots <= 8 or not 0 <= hold_start <= len(y)):
        raise ValueError('invalid replay input')
    cfg=Config(capacity,startup_bins=delay,low_confirmations=hold.confirmations,
               scale_in_headroom=hold.headroom)
    base_cfg=Config(capacity,startup_bins=delay)
    state=State(initial_slots)
    last_capacity_change=-10**9
    rows=[]
    for t,incoming in enumerate(y):
        if state.pending_ready_bin is not None and state.pending_ready_bin <= t:
            state.active_slots+=1
            state.pending_ready_bin=None
            last_capacity_change=t
        before=state.active_slots
        prior=state.backlog_tokens
        active_hold=hold if t>=hold_start else Hold()
        in_dwell=t-last_capacity_change < active_hold.dwell_bins
        action,reason=decide(state,float(p[t]),t,cfg if t>=hold_start else base_cfg)
        if action=='SCALE_IN' and in_dwell:
            # Undo only the proposed shrink; no action was issued or billed.
            state.active_slots=before
            state.last_action_bin=rows[-1]['last_action_bin'] if rows else -10**9
            state.low_streak=0
            action,reason='KEEP','minimum_capacity_dwell'
        elif action=='SCALE_IN':last_capacity_change=t
        actual=state.active_slots*capacity
        state.backlog_tokens=max(0.,prior+incoming-actual)
        rows.append({'bin':t,'forecast_tokens':float(p[t]) if np.isfinite(p[t]) else None,
                     'observed_tokens':float(incoming),'action':action,'reason':reason,
                     'slots_before':before,'active_slots':state.active_slots,
                     'pending_ready_bin':state.pending_ready_bin,
                     'last_action_bin':state.last_action_bin,'last_capacity_change_bin':last_capacity_change,
                     'requested_slots':state.active_slots+int(state.pending_ready_bin is not None),
                     'prior_backlog_tokens':prior,'backlog_tokens':state.backlog_tokens,
                     'instant_shortfall_tokens':max(0.,incoming-actual),
                     'idle_tokens':max(0.,actual-prior-incoming),'capacity_tokens':actual})
    return rows


def choose_hold(records):
    """Validation only: prioritize fewer reversals within preset resource constraints."""
    default={'dwell_bins':0,'confirmations':3,'headroom':.8}
    reference=next(r for r in records if r['hold']==default)
    ref=reference['metrics']
    eligible=[]
    for r in records:
        m=r['metrics']
        if (m['billed_slot_minutes'] <= 1.01*ref['billed_slot_minutes']
                and m['shortfall_fraction'] <= ref['shortfall_fraction']+1e-12
                and m['backlog_area_over_total_tokens'] <= ref['backlog_area_over_total_tokens']+1e-12
                and m['final_backlog_tokens'] <= ref['final_backlog_tokens']+1e-6):
            eligible.append(r)
    return min(eligible,key=lambda r:(r['metrics']['direction_reversals'],r['metrics']['billed_slot_minutes'],
                                       r['metrics']['backlog_area_over_total_tokens'],
                                       r['hold']['dwell_bins'],r['hold']['confirmations'],r['hold']['headroom']))
