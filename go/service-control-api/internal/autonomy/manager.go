package autonomy

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

type DecisionPlanner interface {
	Plan(context.Context, DecisionInput) (Decision, error)
}

type ActionAuthorizer interface {
	Validate(context.Context, string) (bool, string, error)
}

type Event struct {
	Sequence     uint64           `json:"sequence"`
	Timestamp    time.Time        `json:"timestamp"`
	CycleID      string           `json:"cycle_id"`
	DeploymentID string           `json:"deployment_id,omitempty"`
	Stage        string           `json:"stage"`
	Status       string           `json:"status"`
	Reason       string           `json:"reason"`
	Evaluation   *Evaluation      `json:"evaluation,omitempty"`
	Decision     *Decision        `json:"decision,omitempty"`
	Guard        *GuardDecision   `json:"guard,omitempty"`
	Execution    *ExecutionResult `json:"execution,omitempty"`
}

type DeploymentRuntimeState struct {
	PrimaryDeploymentID    string                               `json:"primary_deployment_id"`
	RelatedDeploymentIDs   []string                             `json:"related_deployment_ids"`
	ConsecutiveViolations  int                                  `json:"consecutive_violations"`
	LastMetricTimestamp    time.Time                            `json:"last_metric_timestamp,omitempty"`
	LastEvaluation         *Evaluation                          `json:"last_evaluation,omitempty"`
	LastDecision           *Decision                            `json:"last_decision,omitempty"`
	LastGuard              *GuardDecision                       `json:"last_guard,omitempty"`
	LastExecution          *ExecutionResult                     `json:"last_execution,omitempty"`
	CooldownUntil          time.Time                            `json:"cooldown_until,omitempty"`
	AutomaticActionCount   int                                  `json:"automatic_action_count"`
	LastActionAt           time.Time                            `json:"last_action_at,omitempty"`
	LastActionDeploymentID string                               `json:"last_action_deployment_id,omitempty"`
	LastDeployment         *appdeploy.DeploymentResponse        `json:"last_deployment,omitempty"`
	LastMonitoringSummary  *appdeploy.MonitoringSummaryResponse `json:"last_monitoring_summary,omitempty"`
}

type Status struct {
	Running             bool                   `json:"running"`
	ExecutionLocked     bool                   `json:"execution_locked"`
	AppDeployConfigured bool                   `json:"appdeploy_configured"`
	Config              Config                 `json:"config"`
	State               DeploymentRuntimeState `json:"state"`
	LatestEvent         *Event                 `json:"latest_event,omitempty"`
}

type CycleResult struct {
	CycleID    string           `json:"cycle_id"`
	Status     string           `json:"status"`
	Reason     string           `json:"reason"`
	Evaluation *Evaluation      `json:"evaluation,omitempty"`
	Decision   *Decision        `json:"decision,omitempty"`
	Guard      *GuardDecision   `json:"guard,omitempty"`
	Execution  *ExecutionResult `json:"execution,omitempty"`
}

type Manager struct {
	mu                sync.RWMutex
	cycleMu           sync.Mutex
	config            Config
	state             DeploymentRuntimeState
	running           bool
	locked            bool
	cancel            context.CancelFunc
	done              chan struct{}
	activeCycleCancel context.CancelFunc
	activeCycleID     string
	configGeneration  uint64
	events            []Event
	control           AppDeployControl
	planner           DecisionPlanner
	authorizer        ActionAuthorizer
	executor          Executor
	now               func() time.Time
	sequence          atomic.Uint64
	cycles            atomic.Uint64
	loopStarts        int
}

func NewManager(control AppDeployControl, planner DecisionPlanner, authorizer ActionAuthorizer) *Manager {
	return &Manager{
		config:     DefaultConfig(),
		control:    control,
		planner:    planner,
		authorizer: authorizer,
		executor:   NewExecutor(control),
		now:        func() time.Time { return time.Now().UTC() },
		events:     make([]Event, 0, 200),
	}
}

func (manager *Manager) Configure(config Config) error {
	if err := config.Validate(); err != nil {
		return err
	}
	manager.mu.Lock()
	if manager.running {
		manager.mu.Unlock()
		return fmt.Errorf("stop the autonomy loop before replacing its configuration")
	}
	manager.configGeneration++
	cancelCycle := manager.activeCycleCancel
	manager.config = config
	if manager.state.PrimaryDeploymentID != config.DeploymentID {
		manager.state = DeploymentRuntimeState{PrimaryDeploymentID: config.DeploymentID}
	}
	if config.Mode == ModeGuardedAuto {
		manager.locked = false
	}
	manager.mu.Unlock()
	if cancelCycle != nil {
		cancelCycle()
	}
	return nil
}

func (manager *Manager) Start() (Status, error) {
	manager.mu.Lock()
	if manager.running {
		status := manager.statusLocked()
		manager.mu.Unlock()
		return status, nil
	}
	if err := manager.config.Validate(); err != nil {
		manager.mu.Unlock()
		return Status{}, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	interval := time.Duration(manager.config.PollIntervalSeconds) * time.Second
	manager.cancel = cancel
	manager.done = done
	manager.running = true
	manager.loopStarts++
	status := manager.statusLocked()
	manager.mu.Unlock()

	go manager.loop(ctx, done, interval)
	return status, nil
}

func (manager *Manager) loop(ctx context.Context, done chan struct{}, interval time.Duration) {
	defer func() {
		manager.mu.Lock()
		if manager.done == done {
			manager.running = false
			manager.cancel = nil
			manager.done = nil
		}
		manager.mu.Unlock()
		close(done)
	}()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			manager.RunCycle(ctx)
		}
	}
}

func (manager *Manager) Stop() Status {
	manager.stopLoop(false)
	return manager.Status()
}

func (manager *Manager) EmergencyStop() Status {
	manager.stopLoop(true)
	manager.recordEvent(Event{Stage: "control", Status: "emergency_stopped", Reason: "the loop stopped and mode returned to Monitor Only"})
	return manager.Status()
}

func (manager *Manager) stopLoop(resetMode bool) {
	manager.mu.Lock()
	cancel := manager.cancel
	cancelCycle := manager.activeCycleCancel
	done := manager.done
	manager.configGeneration++
	if resetMode {
		manager.config.Mode = ModeMonitorOnly
	}
	manager.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	if cancelCycle != nil {
		cancelCycle()
	}
	if done != nil {
		<-done
	}
}

func (manager *Manager) Status() Status {
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	return manager.statusLocked()
}

func (manager *Manager) statusLocked() Status {
	status := Status{
		Running: manager.running, ExecutionLocked: manager.locked,
		AppDeployConfigured: manager.control != nil, Config: manager.config, State: manager.state,
	}
	status.State.RelatedDeploymentIDs = append([]string(nil), manager.state.RelatedDeploymentIDs...)
	if len(manager.events) > 0 {
		latest := manager.events[len(manager.events)-1]
		status.LatestEvent = &latest
	}
	return status
}

func (manager *Manager) Events() []Event {
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	return append([]Event(nil), manager.events...)
}

func (manager *Manager) ClearEvents() int {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	deleted := len(manager.events)
	manager.events = make([]Event, 0, 200)
	return deleted
}

func (manager *Manager) RunCycle(ctx context.Context) CycleResult {
	manager.cycleMu.Lock()
	defer manager.cycleMu.Unlock()

	now := manager.now()
	cycleID := fmt.Sprintf("cycle-%d", manager.cycles.Add(1))
	cycleCtx, cancelCycle := context.WithCancel(ctx)
	manager.mu.Lock()
	config := manager.config
	state := manager.state
	locked := manager.locked
	generation := manager.configGeneration
	manager.activeCycleCancel = cancelCycle
	manager.activeCycleID = cycleID
	manager.mu.Unlock()
	defer func() {
		cancelCycle()
		manager.mu.Lock()
		if manager.activeCycleID == cycleID {
			manager.activeCycleCancel = nil
			manager.activeCycleID = ""
		}
		manager.mu.Unlock()
	}()
	result := CycleResult{CycleID: cycleID}

	if manager.control == nil {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "monitor", Status: "source_unavailable", Reason: "AppDeploy is not configured"})
	}
	deployment, err := manager.control.GetDeployment(cycleCtx, config.DeploymentID)
	if err != nil {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "monitor", Status: "source_unavailable", Reason: "AppDeploy deployment status could not be read"})
	}
	metrics, err := manager.control.ListDeploymentMetrics(cycleCtx, config.DeploymentID)
	if err != nil {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "monitor", Status: "source_unavailable", Reason: "AppDeploy metrics could not be read"})
	}
	summary, err := manager.control.GetMonitoringSummary(cycleCtx)
	if err != nil {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "monitor", Status: "source_unavailable", Reason: "AppDeploy monitoring summary could not be read"})
	}
	metric := latestMetric(metrics.Items)
	metric = safeMetric(metric)
	runtimeHealth := runtimeHealthFor(summary.RuntimeHealth, deployment.TargetProfileID)
	evaluation := Evaluate(EvaluationInput{
		Now: now, Metric: metric, DeploymentStatus: deployment.Status, MonitoringStatus: summary.Status,
		RuntimeHealth: runtimeHealth, Policy: config.SLO,
		MaxMetricAge:         time.Duration(config.MaxMetricAgeSeconds) * time.Second,
		EvidenceNotBefore:    state.LastActionAt,
		FailureEvidenceFresh: state.LastActionAt.IsZero() || state.LastActionDeploymentID != config.DeploymentID,
		PreviousConsecutive:  state.ConsecutiveViolations,
	})
	result.Evaluation = &evaluation
	manager.mu.Lock()
	manager.state.PrimaryDeploymentID = config.DeploymentID
	manager.state.ConsecutiveViolations = evaluation.ConsecutiveViolations
	manager.state.LastEvaluation = &evaluation
	safeDeployment := deploymentForStatus(deployment)
	safeSummary := monitoringSummaryForStatus(summary)
	manager.state.LastDeployment = &safeDeployment
	manager.state.LastMonitoringSummary = &safeSummary
	if metric != nil {
		manager.state.LastMetricTimestamp = metric.Timestamp
	}
	manager.mu.Unlock()
	manager.recordEvent(Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "slo", Status: string(evaluation.Status), Reason: evaluation.Reason, Evaluation: &evaluation})

	if evaluation.Status == EvaluationHealthy {
		status := "healthy"
		if state.ConsecutiveViolations > 0 {
			status = "recovered"
		}
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "feedback", Status: status, Reason: evaluation.Reason, Evaluation: &evaluation})
	}
	if evaluation.Status == EvaluationInsufficientEvidence {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "feedback", Status: string(EvaluationInsufficientEvidence), Reason: evaluation.Reason, Evaluation: &evaluation})
	}
	if evaluation.ConsecutiveViolations < config.ConsecutiveViolations {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "slo", Status: "observing_violation", Reason: fmt.Sprintf("waiting for %d consecutive violating cycles", config.ConsecutiveViolations), Evaluation: &evaluation})
	}
	if locked {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "guard", Status: string(GuardBlocked), Reason: "automatic execution is locked after a partial failure", Evaluation: &evaluation})
	}
	if manager.planner == nil {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "qwen", Status: "decision_failed", Reason: "Qwen planner is not configured", Evaluation: &evaluation})
	}
	decision, err := manager.planner.Plan(cycleCtx, DecisionInput{Deployment: deployment, Evaluation: evaluation, Config: config})
	if err != nil || !decision.Action.Valid() {
		return manager.finishCycle(result, Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "qwen", Status: "decision_failed", Reason: "Qwen did not return a valid bounded Action", Evaluation: &evaluation})
	}
	result.Decision = &decision
	manager.recordEvent(Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "qwen", Status: "proposed", Reason: decision.Reason, Evaluation: &evaluation, Decision: &decision})

	approved, authorizationReason := false, "Agent Registry authorizer is not configured"
	if manager.authorizer != nil {
		approved, authorizationReason, err = manager.authorizer.Validate(cycleCtx, string(decision.Action))
		if err != nil {
			approved = false
			authorizationReason = "Agent Registry authorization failed"
		}
	}
	guard := ValidateExecution(GuardInput{
		Mode: config.Mode, Action: decision.Action, RegistryApproved: approved, RegistryReason: authorizationReason,
		Evaluation: evaluation, RequiredConsecutive: config.ConsecutiveViolations,
		CooldownUntil: state.CooldownUntil, Now: now, ActionCount: state.AutomaticActionCount,
		MaxActions: config.MaxActionsPerDeployment, StandbyTargetProfileID: config.StandbyTargetProfileID,
		RollbackAppVersionID: config.RollbackAppVersionID,
	})
	result.Guard = &guard
	manager.mu.Lock()
	manager.state.LastDecision = &decision
	manager.state.LastGuard = &guard
	manager.mu.Unlock()
	manager.recordEvent(Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "guard", Status: string(guard.Status), Reason: guard.Reason, Evaluation: &evaluation, Decision: &decision, Guard: &guard})
	if !guard.Executable {
		result.Status = string(guard.Status)
		result.Reason = guard.Reason
		return result
	}
	if allowed, reason := manager.executionFence(generation); !allowed {
		fencedGuard := GuardDecision{Status: GuardBlocked, Reason: reason}
		result.Guard = &fencedGuard
		result.Status = string(GuardBlocked)
		result.Reason = reason
		manager.mu.Lock()
		manager.state.LastGuard = &fencedGuard
		manager.mu.Unlock()
		manager.recordEvent(Event{Timestamp: manager.now(), CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "guard", Status: string(GuardBlocked), Reason: reason, Evaluation: &evaluation, Decision: &decision, Guard: &fencedGuard})
		return result
	}

	execution := manager.executor.Execute(cycleCtx, ExecutionInput{Action: decision.Action, Deployment: deployment, StandbyTargetProfileID: config.StandbyTargetProfileID, RollbackAppVersionID: config.RollbackAppVersionID})
	completedAt := manager.now()
	result.Execution = &execution
	manager.mu.Lock()
	manager.state.LastExecution = &execution
	if execution.Status == ExecutionSucceeded && stateChangingAction(decision.Action) {
		manager.state.AutomaticActionCount++
		manager.state.ConsecutiveViolations = 0
		manager.state.LastActionAt = completedAt
		manager.state.LastActionDeploymentID = config.DeploymentID
		manager.state.CooldownUntil = completedAt.Add(time.Duration(config.CooldownSeconds) * time.Second)
		if execution.NewDeploymentID != "" {
			if decision.Action == ActionScaleOut {
				manager.state.RelatedDeploymentIDs = append(manager.state.RelatedDeploymentIDs, execution.NewDeploymentID)
			} else {
				manager.config.DeploymentID = execution.NewDeploymentID
				manager.state.PrimaryDeploymentID = execution.NewDeploymentID
			}
		}
	}
	if execution.Status == ExecutionPartialFailure {
		manager.locked = true
		manager.config.Mode = ModeMonitorOnly
		manager.state.ConsecutiveViolations = 0
		manager.state.LastActionAt = completedAt
		manager.state.LastActionDeploymentID = config.DeploymentID
	}
	manager.mu.Unlock()
	manager.recordEvent(Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "execute", Status: string(execution.Status), Reason: execution.Reason, Evaluation: &evaluation, Decision: &decision, Guard: &guard, Execution: &execution})
	result.Status = string(execution.Status)
	result.Reason = execution.Reason
	if execution.Status == ExecutionSucceeded && stateChangingAction(decision.Action) {
		manager.recordEvent(Event{Timestamp: now, CycleID: cycleID, DeploymentID: config.DeploymentID, Stage: "feedback", Status: "awaiting_evidence", Reason: "Action completed; a later cycle must collect fresh evidence before another Action", Execution: &execution})
	}
	return result
}

func (manager *Manager) executionFence(generation uint64) (bool, string) {
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	if manager.configGeneration != generation {
		return false, "the cycle was cancelled by a stop or configuration change"
	}
	if manager.config.Mode != ModeGuardedAuto {
		return false, "current mode no longer permits automatic state changes"
	}
	if manager.locked {
		return false, "automatic execution is locked after a partial failure"
	}
	return true, ""
}

func (manager *Manager) finishCycle(result CycleResult, event Event) CycleResult {
	manager.recordEvent(event)
	result.Status = event.Status
	result.Reason = event.Reason
	if result.Evaluation == nil {
		result.Evaluation = event.Evaluation
	}
	return result
}

func (manager *Manager) recordEvent(event Event) {
	if event.Timestamp.IsZero() {
		event.Timestamp = manager.now()
	}
	event.Sequence = manager.sequence.Add(1)
	manager.mu.Lock()
	manager.events = append(manager.events, event)
	if len(manager.events) > 200 {
		manager.events = append([]Event(nil), manager.events[len(manager.events)-200:]...)
	}
	manager.mu.Unlock()
}

func latestMetric(metrics []appdeploy.InferenceMetricRecord) *appdeploy.InferenceMetricRecord {
	if len(metrics) == 0 {
		return nil
	}
	latest := metrics[0]
	for _, metric := range metrics[1:] {
		if metric.Timestamp.After(latest.Timestamp) {
			latest = metric
		}
	}
	return &latest
}

func safeMetric(metric *appdeploy.InferenceMetricRecord) *appdeploy.InferenceMetricRecord {
	if metric == nil {
		return nil
	}
	safe := *metric
	safe.Metadata = nil
	return &safe
}

func deploymentForStatus(deployment appdeploy.DeploymentResponse) appdeploy.DeploymentResponse {
	safe := deployment
	safe.Manifest.Spec.Parameters = nil
	return safe
}

func monitoringSummaryForStatus(summary appdeploy.MonitoringSummaryResponse) appdeploy.MonitoringSummaryResponse {
	safe := summary
	safe.Alarms = append([]appdeploy.DeploymentAlarmSummary(nil), summary.Alarms...)
	for index := range safe.Alarms {
		safe.Alarms[index].LatestMessage = ""
	}
	return safe
}

func stateChangingAction(action Action) bool {
	return action == ActionScaleOut || action == ActionRestart || action == ActionRollback || action == ActionStop
}

func runtimeHealthFor(snapshots []appdeploy.RuntimeHealthSnapshot, targetProfileID string) string {
	for _, snapshot := range snapshots {
		if snapshot.TargetProfileID == targetProfileID {
			if unhealthyStatus(snapshot.RuntimeHealth) {
				return snapshot.RuntimeHealth
			}
			return snapshot.Status
		}
	}
	return ""
}
