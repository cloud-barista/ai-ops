package autonomy

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
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

func TestManagerGuardedAutoExecutesOnceThenRequiresNewEvidence(t *testing.T) {
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
	if second.Status != string(EvaluationInsufficientEvidence) || control.stopCalls != 1 || control.createCalls != 1 {
		t.Fatalf("evidence fence must prevent duplicate execution: %#v stop=%d create=%d", second, control.stopCalls, control.createCalls)
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

func TestManagerTerminalStopDoesNotConsumeActionBudget(t *testing.T) {
	now := time.Now().UTC()
	control := newManagerControl(now)
	control.deployment.Status = "STOPPED"
	manager := NewManager(control, &managerPlanner{decision: Decision{Action: ActionStop}}, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeGuardedAuto)
	config.ConsecutiveViolations = 1
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	result := manager.RunCycle(context.Background())
	status := manager.Status()
	if result.Status != string(ExecutionNotApplicable) || control.stopCalls != 0 {
		t.Fatalf("terminal stop must not issue stop: result=%#v stop_calls=%d", result, control.stopCalls)
	}
	if status.State.AutomaticActionCount != 0 || !status.State.CooldownUntil.IsZero() {
		t.Fatalf("terminal stop consumed safety budget: %#v", status.State)
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

func TestManagerClearEventsPreservesRuntimeStateAndSequence(t *testing.T) {
	manager := NewManager(nil, nil, nil)
	config := DefaultConfig()
	config.DeploymentID = "dep-clear-events"
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}
	manager.mu.Lock()
	manager.state.AutomaticActionCount = 2
	manager.state.CooldownUntil = time.Now().UTC().Add(time.Minute)
	manager.mu.Unlock()
	manager.recordEvent(Event{Stage: "test", Status: "first"})
	manager.recordEvent(Event{Stage: "test", Status: "second"})
	before := manager.Status()
	previousSequence := manager.Events()[1].Sequence

	deleted := manager.ClearEvents()
	if deleted != 2 || len(manager.Events()) != 0 {
		t.Fatalf("deleted=%d events=%d", deleted, len(manager.Events()))
	}
	after := manager.Status()
	if after.Config != before.Config || after.State.PrimaryDeploymentID != before.State.PrimaryDeploymentID ||
		after.State.AutomaticActionCount != before.State.AutomaticActionCount || after.State.CooldownUntil != before.State.CooldownUntil {
		t.Fatalf("event clearing changed runtime state: before=%#v after=%#v", before, after)
	}

	manager.recordEvent(Event{Stage: "test", Status: "after-clear"})
	events := manager.Events()
	if len(events) != 1 || events[0].Sequence <= previousSequence {
		t.Fatalf("event sequence was reused after clear: previous=%d events=%#v", previousSequence, events)
	}
}

func TestManagerDeleteEventPreservesOrderAndSequence(t *testing.T) {
	manager := NewManager(nil, nil, nil)
	manager.recordEvent(Event{Stage: "test", Status: "first"})
	manager.recordEvent(Event{Stage: "test", Status: "second"})
	manager.recordEvent(Event{Stage: "test", Status: "third"})
	events := manager.Events()

	deleted, ok := manager.DeleteEvent(events[1].Sequence)
	if !ok || deleted.Sequence != events[1].Sequence || deleted.Status != "second" {
		t.Fatalf("unexpected deletion: deleted=%#v ok=%t", deleted, ok)
	}
	remaining := manager.Events()
	if len(remaining) != 2 || remaining[0].Sequence != events[0].Sequence || remaining[1].Sequence != events[2].Sequence {
		t.Fatalf("event order changed after deletion: %#v", remaining)
	}
	if _, ok := manager.DeleteEvent(events[1].Sequence); ok {
		t.Fatal("missing event deletion unexpectedly succeeded")
	}
	manager.recordEvent(Event{Stage: "test", Status: "fourth"})
	remaining = manager.Events()
	if remaining[len(remaining)-1].Sequence <= events[2].Sequence {
		t.Fatalf("event sequence was reused after individual deletion: %#v", remaining)
	}
}

func TestManagerEmergencyStopFencesInFlightManualCycle(t *testing.T) {
	now := time.Now().UTC()
	control := newManagerControl(now)
	planner := &blockingManagerPlanner{started: make(chan struct{}), release: make(chan struct{}), decision: Decision{Action: ActionRestart}}
	manager := NewManager(control, planner, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeGuardedAuto)
	config.ConsecutiveViolations = 1
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	done := make(chan CycleResult, 1)
	go func() { done <- manager.RunCycle(context.Background()) }()
	<-planner.started
	manager.EmergencyStop()
	close(planner.release)
	result := <-done
	if control.stopCalls != 0 || control.createCalls != 0 {
		t.Fatalf("emergency stop did not fence in-flight execution: stop=%d create=%d", control.stopCalls, control.createCalls)
	}
	if result.Status != string(GuardBlocked) {
		t.Fatalf("in-flight cycle should finish blocked: %#v", result)
	}
}

func TestManagerRequiresMetricNewerThanLastAction(t *testing.T) {
	now := time.Now().UTC()
	control := newManagerControl(now)
	manager := NewManager(control, &managerPlanner{decision: Decision{Action: ActionScaleOut}}, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeGuardedAuto)
	config.ConsecutiveViolations = 1
	config.CooldownSeconds = 0
	config.StandbyTargetProfileID = "target-standby"
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	first := manager.RunCycle(context.Background())
	second := manager.RunCycle(context.Background())
	if first.Status != string(ExecutionSucceeded) || second.Status != string(EvaluationInsufficientEvidence) {
		t.Fatalf("same metric must not authorize a second Action: first=%#v second=%#v", first, second)
	}
	if control.createCalls != 1 {
		t.Fatalf("same metric triggered %d creates", control.createCalls)
	}
}

func TestManagerRequiresNewEvidenceBeforeRepeatingActionOnFailedPrimary(t *testing.T) {
	now := time.Now().UTC()
	control := newManagerControl(now)
	control.deployment.Status = "FAILED"
	manager := NewManager(control, &managerPlanner{decision: Decision{Action: ActionScaleOut}}, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeGuardedAuto)
	config.ConsecutiveViolations = 1
	config.CooldownSeconds = 0
	config.StandbyTargetProfileID = "target-standby"
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}

	first := manager.RunCycle(context.Background())
	second := manager.RunCycle(context.Background())
	if first.Status != string(ExecutionSucceeded) || second.Status != string(EvaluationInsufficientEvidence) {
		t.Fatalf("failed primary was reused as evidence: first=%#v second=%#v", first, second)
	}
	if control.createCalls != 1 {
		t.Fatalf("unchanged failure state triggered %d creates", control.createCalls)
	}
}

func TestManagerStatusDoesNotExposeManifestParametersOrMetricMetadata(t *testing.T) {
	now := time.Now().UTC()
	control := newManagerControl(now)
	control.deployment.Manifest.Spec.Parameters = map[string]any{"password": "manifest-secret"}
	control.metric.Metadata = map[string]any{"api_key": "metric-secret"}
	manager := NewManager(control, &managerPlanner{decision: Decision{Action: ActionObserve}}, &managerAuthorizer{approved: true})
	manager.now = func() time.Time { return now }
	config := managerConfig(ModeMonitorOnly)
	config.ConsecutiveViolations = 1
	if err := manager.Configure(config); err != nil {
		t.Fatal(err)
	}
	manager.RunCycle(context.Background())

	content, err := json.Marshal(manager.Status())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(content), "manifest-secret") || strings.Contains(string(content), "metric-secret") {
		t.Fatalf("status leaked AppDeploy supplied data: %s", content)
	}
}

func TestManagerEventsCarryConfiguredControlRunID(t *testing.T) {
	manager := NewManager(nil, nil, nil)
	config := managerConfig(ModeMonitorOnly)
	config.RunID = "run-001"
	if err := manager.Configure(config); err != nil {
		t.Fatalf("configure manager: %v", err)
	}

	manager.RunCycle(context.Background())
	events := manager.Events()
	if len(events) == 0 || events[len(events)-1].RunID != "run-001" {
		t.Fatalf("Autonomy Event did not retain ControlRun identity: %#v", events)
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

type blockingManagerPlanner struct {
	started  chan struct{}
	release  chan struct{}
	decision Decision
}

func (planner *blockingManagerPlanner) Plan(context.Context, DecisionInput) (Decision, error) {
	close(planner.started)
	<-planner.release
	return planner.decision, nil
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
