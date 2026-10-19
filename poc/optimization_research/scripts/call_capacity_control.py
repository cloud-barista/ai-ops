"""Research-only call-unit fluid controller; no resource IDs or execution APIs."""
from dataclasses import dataclass
import math
import numpy as np

@dataclass(frozen=True)
class Config:
 capacity_calls_per_bin:float
 startup_bins:int
 queue_weight:float=1.
 look_after_ready:int=6
 cooldown_bins:int=2
 minimum_dwell_bins:int=6
 low_confirmations:int=3
 shrink_headroom:float=.8
 max_slots:int=8
 def __post_init__(self):
  if isinstance(self.capacity_calls_per_bin,bool) or not math.isfinite(self.capacity_calls_per_bin) or self.capacity_calls_per_bin<=0:raise ValueError('capacity')
  if isinstance(self.queue_weight,bool) or not math.isfinite(self.queue_weight) or self.queue_weight<=0:raise ValueError('queue_weight')
  for n in ('startup_bins','look_after_ready','cooldown_bins','minimum_dwell_bins','low_confirmations','max_slots'):
   x=getattr(self,n)
   if isinstance(x,bool) or not isinstance(x,int) or x<1:raise ValueError(n)
  if not math.isfinite(self.shrink_headroom) or not 0<self.shrink_headroom<1:raise ValueError('shrink_headroom')

@dataclass
class State:
 active_slots:int
 queue_calls:float=0.
 pending_ready:int=-1
 last_action:int=-10**9
 last_change:int=-10**9
 low_streak:int=0

def value(state,forecast,t,action,cfg):
 """Hypothetical constant forecast; evaluate one action and no future actions."""
 n=state.active_slots;ready=-1;q=state.queue_calls
 if action=='SCALE_OUT':ready=t+cfg.startup_bins
 elif action=='SCALE_IN':n-=1
 length=cfg.startup_bins+cfg.look_after_ready
 cost=0.;area=0.
 for k in range(length):
  if ready==t+k:n+=1;ready=-1
  cost+=n+int(ready>=0)
  q=max(0.,q+forecast-n*cfg.capacity_calls_per_bin)
  area+=q/cfg.capacity_calls_per_bin
 return cost+cfg.queue_weight*(area+length*q/cfg.capacity_calls_per_bin)

def decide(state,forecast,t,cfg,kind):
 if kind not in ('threshold','lookahead'):raise ValueError('policy kind')
 if not math.isfinite(forecast) or forecast<0:
  state.low_streak=0;return 'ABSTAIN','forecast_unknown'
 if state.pending_ready>=0:
  state.low_streak=0;return 'KEEP','capacity_pending'
 required=forecast+state.queue_calls
 low=state.active_slots>1 and required<=cfg.shrink_headroom*(state.active_slots-1)*cfg.capacity_calls_per_bin
 state.low_streak=state.low_streak+1 if low else 0
 if t-state.last_action<cfg.cooldown_bins:return 'KEEP','cooldown'
 shrink=low and state.low_streak>=cfg.low_confirmations and t-state.last_change>=cfg.minimum_dwell_bins
 if kind=='threshold':
  if required>state.active_slots*cfg.capacity_calls_per_bin and state.active_slots<cfg.max_slots:action='SCALE_OUT'
  elif shrink:action='SCALE_IN'
  else:action='KEEP'
 else:
  choices=['KEEP']
  if state.active_slots<cfg.max_slots:choices.append('SCALE_OUT')
  if shrink:choices.append('SCALE_IN')
  action=min(choices,key=lambda a:value(state,forecast,t,a,cfg))
 if action=='SCALE_OUT':state.pending_ready=t+cfg.startup_bins;state.last_action=t;state.low_streak=0
 elif action=='SCALE_IN':state.active_slots-=1;state.last_action=t;state.last_change=t;state.low_streak=0
 return action,('forecast_queue_capacity' if kind=='threshold' else 'finite_horizon_slot_queue_cost')

COLUMNS=('origin','forecast_calls','observed_calls','active_slots','pending_ready','requested_slots','prior_queue_calls','queue_calls','served_calls','instant_shortfall_calls','action_code','ready_activated')
ACTION_CODES={'KEEP':0,'SCALE_OUT':1,'SCALE_IN':-1,'ABSTAIN':2}
def replay(y,p,first,capacity,delay,initial_slots,kind='threshold',weight=1.,switch_at=None):
 y=np.asarray(y,float);p=np.asarray(p,float)
 if y.ndim!=1 or p.shape!=y.shape or not 0<=first<len(y):raise ValueError('replay shape')
 if not np.isfinite(y[first:]).all() or (y[first:]<0).any():raise ValueError('unknown arrivals')
 if isinstance(initial_slots,bool) or not isinstance(initial_slots,int) or not 1<=initial_slots<=8:raise ValueError('initial_slots')
 if switch_at is not None and not first<=switch_at<=len(y):raise ValueError('switch_at')
 cfg=Config(capacity,delay,weight);state=State(initial_slots);rows=np.empty((len(y)-first,len(COLUMNS)),float)
 for t in range(first,len(y)):
  activated=state.pending_ready>=0 and state.pending_ready<=t
  if activated:state.active_slots+=1;state.pending_ready=-1;state.last_change=t
  prior=state.queue_calls
  current_kind='threshold' if switch_at is not None and t<switch_at else kind
  action,_=decide(state,float(p[t]),t,cfg,current_kind)
  cap=state.active_slots*capacity;served=min(prior+y[t],cap);state.queue_calls=max(0.,prior+y[t]-cap)
  requested=state.active_slots+int(state.pending_ready>=0)
  rows[t-first]=[t,p[t],y[t],state.active_slots,state.pending_ready,requested,prior,state.queue_calls,served,max(0.,y[t]-cap),ACTION_CODES[action],int(activated)]
 return rows

def summarize(rows,capacity):
 total=float(rows[:,2].sum());actions=rows[:,10];moves=actions[np.isin(actions,[-1,1])]
 bill=float(5*rows[:,5].sum());area=float(rows[:,7].sum());final=float(rows[-1,7])
 return {'bins':len(rows),'recorded_calls':total,'billed_slot_minutes':bill,'active_slot_minutes':float(5*rows[:,3].sum()),
  'instant_shortfall_fraction':float(rows[:,9].sum()/total) if total else None,
  'queue_area_calls_bins':area,'queue_area_over_total_calls':area/total if total else None,'final_queue_calls':final,
  'scale_out_requests':int((actions==1).sum()),'scale_in_requests':int((actions==-1).sum()),
  'direction_reversals':int((moves[1:]!=moves[:-1]).sum()),'abstentions':int((actions==2).sum()),
  'validation_objective':float(bill/5+area/capacity)}

def gate(a,b):
 constraints=(a['billed_slot_minutes']<=1.01*b['billed_slot_minutes']+1e-9 and
  a['instant_shortfall_fraction']<=b['instant_shortfall_fraction']+1e-12 and
  a['queue_area_calls_bins']<=b['queue_area_calls_bins']+1e-6 and
  a['final_queue_calls']<=b['final_queue_calls']+1e-6 and a['direction_reversals']<=b['direction_reversals'])
 benefit=(a['billed_slot_minutes']<=.95*b['billed_slot_minutes'] or
  (b['instant_shortfall_fraction']>0 and a['instant_shortfall_fraction']<=.95*b['instant_shortfall_fraction']))
 return 'RETROSPECTIVE_SIMULATION_PASS' if constraints and benefit else 'NO_GO'
