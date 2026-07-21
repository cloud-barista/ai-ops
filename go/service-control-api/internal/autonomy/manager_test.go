package autonomy

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestManagerMonitorOnlyRequiresConfirmedViolation(t *testing.T) {
	now := time.Date(2026, 7, 21, 4, 0, 0, 0, time.UTC)
	control := newManagerControl(now)
	planner := &managerPlanner{decision: Decision{Action: ActionRestart, Reason: "latency recovery", Confidence: 0.9}}
	authorizer := &managerAuthorizer{approved: true, reason: "registered Action"}
	manager := NewManager(control, planner, authorizer)
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeMonitorOnly)
	config.ConsecutiveViolations = 2
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	first := manager.RunCycle(context.Background())
	if first.Status != "observing_violation" || planner.calls != 0 {
		t.Fatalf("first cycle must only observe: %#v planner_calls=%d", first, planner.calls)
	}
	second := manager.RunCycle(context.Background())
	if second.Status != string(GuardWouldExecute) || planner.calls != 1 || authorizer.calls != 1 {
		t.Fatalf("second cycle should plan and guard: %#v planner=%d authorizer=%d", second, planner.calls, authorizer.calls)
	}
	if control.stopCalls != 0 || control.createCalls != 0 {
		t.Fatalf("monitor-only changed AppDeploy state: stop=%d create=%d", control.stopCalls, control.createCalls)
	}
}

func TestManagerGuardedAutoExecutesOnceThenCooldownBlocks(t *testing.T) {
	now := time.Date(2026, 7, 21, 4, 0, 0, 0, time.UTC)
	control := newManagerControl(now)
	planner := &managerPlanner{decision: Decision{Action: ActionRestart, Reason: "restart", Confidence: 0.8}}
	manager := NewManager(control, planner, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeGuardedAuto)
	config.ConsecutiveViolations = 1
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	first := manager.RunCycle(context.Background())
	if first.Status != string(ExecutionSucceeded) || control.stopCalls != 1 || control.createCalls != 1 {
		t.Fatalf("expected one guarded execution: %#v stop=%d create=%d", first, control.stopCalls, control.createCalls)
	}
	second := manager.RunCycle(context.Background())
	if second.Status != string(GuardBlocked) || control.stopCalls != 1 || control.createCalls != 1 {
		t.Fatalf("cooldown must prevent duplicate execution: %#v stop=%d create=%d", second, control.stopCalls, control.createCalls)
	}
}

func TestManagerPartialFailureReturnsToMonitorOnly(t *testing.T) {
	now := time.Now().UTC()
	control := newManagerControl(now)
	control.createErr = errors.New("temporary create error")
	manager := NewManager(control, &managerPlanner{decision: Decision{Action: ActionRestart}}, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeGuardedAuto)
	config.ConsecutiveViolations = 1
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	result := manager.RunCycle(context.Background())
	status := manager.Status()
	if result.Status != string(ExecutionPartialFailure) || status.Config.Mode != ModeMonitorOnly || !status.ExecutionLocked {
		t.Fatalf("partial failure must lock automatic execution: result=%#v status=%#v", result, status)
	}
}

func TestManagerStartIsIdempotentAndEmergencyStopIsSafe(t *testing.T) {
	now := time.Now().UTC()
	manager := NewManager(newManagerControl(now), &managerPlanner{decision: Decision{Action: ActionObserve}}, &managerAuthorizer{approved: true})
	config := managerConfig(ModeGuardedAuto)
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Start(); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Start(); err != nil {
		t.Fatal(err)
	}
	if manager.loopStarts != 1 {
		t.Fatalf("duplicate start created %d loops", manager.loopStarts)
	}
	manager.EmergencyStop()
	status := manager.Status()
	if status.Running || status.Config.Mode != ModeMonitorOnly {
		t.Fatalf("unexpected emergency state: %#v", status)
	}
}

func TestManagerEventStoreKeepsNewestTwoHundred(t *testing.T) {
	manager := NewManager(nil, nil, nil)
	for index := 0; index < 205; index++ {
		manager.recordEvent(Event{CycleID: "cycle", Stage: "test", Status: "event"})
	}
	events := manager.Events()
	if len(events) != 200 {
		t.Fatalf("events=%d want=200", len(events))
	}
	if events[0].Sequence != 6 || events[len(events)-1].Sequence != 205 {
		t.Fatalf("unexpected retained sequence range: %d..%d", events[0].Sequence, events[len(events)-1].Sequence)
	}
}

func managerConfig(mode Mode) Config {
	config := DefaultConfig()
	config.Mode = mode
	config.DeploymentID = "dep-1"
	return config
}

type managerPlanner struct {
	mu       sync.Mutex
	decision Decision
	calls    int
}

func (planner *managerPlanner) Plan(context.Context, DecisionInput) (Decision, error) {
	planner.mu.Lock()
	defer planner.mu.Unlock()
	planner.calls++
	return planner.decision, nil
}

type managerAuthorizer struct {
	approved bool
	reason   string
	calls    int
}

func (authorizer *managerAuthorizer) Validate(_ context.Context, _ string) (bool, string, error) {
	authorizer.calls++
	return authorizer.approved, authorizer.reason, nil
}

type managerControl struct {
	deployment  appdeploy.DeploymentResponse
	metric      appdeploy.InferenceMetricRecord
	stopCalls   int
	createCalls int
	createErr   error
}

func newManagerControl(now time.Time) *managerControl {
	return &managerControl{deployment: executorDeployment(), metric: metricAt(now, 900, 0.5, 100, 10)}
}
func (control *managerControl) GetDeployment(context.Context, string) (appdeploy.DeploymentResponse, error) {
	return control.deployment, nil
}
func (control *managerControl) ListDeploymentMetrics(context.Context, string) (appdeploy.DeploymentMetricsResponse, error) {
	return appdeploy.DeploymentMetricsResponse{Items: []appdeploy.InferenceMetricRecord{control.metric}}, nil
}
func (control *managerControl) GetMonitoringSummary(context.Context) (appdeploy.MonitoringSummaryResponse, error) {
	return appdeploy.MonitoringSummaryResponse{Status: "ok", RuntimeHealth: []appdeploy.RuntimeHealthSnapshot{{TargetProfileID: "target-primary", Status: "available", RuntimeHealth: "ok"}}}, nil
}
func (control *managerControl) CreateDeployment(_ context.Context, manifest appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error) {
	control.createCalls++
	if control.createErr != nil {
		return appdeploy.DeploymentResponse{}, control.createErr
	}
	control.deployment = appdeploy.DeploymentResponse{DeploymentID: "dep-new", Status: "REQUESTED", Manifest: manifest, TargetProfileID: manifest.Spec.TargetProfileID, AppVersionID: manifest.Spec.AppVersionID}
	return control.deployment, nil
}
func (control *managerControl) StopDeployment(context.Context, string) (appdeploy.DeploymentResponse, error) {
	control.stopCalls++
	return appdeploy.DeploymentResponse{DeploymentID: "dep-1", Status: "STOPPED"}, nil
}
