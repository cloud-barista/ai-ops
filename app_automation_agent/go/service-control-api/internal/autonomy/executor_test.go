package autonomy

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestExecutorBoundedActions(t *testing.T) {
	deployment := executorDeployment()
	tests := []struct {
		name      string
		action    Action
		standby   string
		rollback  string
		wantCalls []string
		assert    func(*testing.T, executionFake, ExecutionResult)
	}{
		{name: "observe", action: ActionObserve, wantCalls: []string{"get:dep-1", "metrics:dep-1"}},
		{name: "stop", action: ActionStop, wantCalls: []string{"stop:dep-1"}},
		{name: "restart", action: ActionRestart, wantCalls: []string{"stop:dep-1", "create"}, assert: func(t *testing.T, fake executionFake, _ ExecutionResult) {
			if !reflect.DeepEqual(fake.created[0], deployment.Manifest) {
				t.Fatalf("restart changed manifest: %#v", fake.created[0])
			}
		}},
		{name: "rollback", action: ActionRollback, rollback: "appver-old", wantCalls: []string{"stop:dep-1", "create"}, assert: func(t *testing.T, fake executionFake, _ ExecutionResult) {
			got := fake.created[0]
			if got.Spec.AppVersionID != "appver-old" || got.Spec.TargetProfileID != deployment.Manifest.Spec.TargetProfileID {
				t.Fatalf("unexpected rollback manifest: %#v", got)
			}
		}},
		{name: "scale out", action: ActionScaleOut, standby: "target-standby", wantCalls: []string{"create"}, assert: func(t *testing.T, fake executionFake, result ExecutionResult) {
			got := fake.created[0]
			if got.Spec.TargetProfileID != "target-standby" || got.Spec.AppVersionID != deployment.Manifest.Spec.AppVersionID {
				t.Fatalf("unexpected scale-out manifest: %#v", got)
			}
			if !result.TrafficHandoffRequired {
				t.Fatal("scale-out must disclose required traffic handoff")
			}
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fake := executionFake{deployment: deployment}
			executor := NewExecutor(&fake)
			result := executor.Execute(context.Background(), ExecutionInput{Action: test.action, Deployment: deployment, StandbyTargetProfileID: test.standby, RollbackAppVersionID: test.rollback})
			if result.Status != ExecutionSucceeded {
				t.Fatalf("execution failed: %#v", result)
			}
			if !reflect.DeepEqual(fake.calls, test.wantCalls) {
				t.Fatalf("calls=%v want=%v", fake.calls, test.wantCalls)
			}
			if test.assert != nil {
				test.assert(t, fake, result)
			}
		})
	}
}

func TestExecutorReportsPartialFailure(t *testing.T) {
	fake := executionFake{deployment: executorDeployment(), createErr: errors.New("create unavailable")}
	result := NewExecutor(&fake).Execute(context.Background(), ExecutionInput{Action: ActionRestart, Deployment: fake.deployment})
	if result.Status != ExecutionPartialFailure || !result.StopCompleted {
		t.Fatalf("expected partial failure after successful stop: %#v", result)
	}
}

func TestExecutorStopOnTerminalDeploymentOnlyObserves(t *testing.T) {
	deployment := executorDeployment()
	deployment.Status = "STOPPED"
	fake := executionFake{deployment: deployment}
	result := NewExecutor(&fake).Execute(context.Background(), ExecutionInput{Action: ActionStop, Deployment: deployment})
	if result.Status != ExecutionNotApplicable {
		t.Fatalf("terminal stop should be non-applicable: %#v", result)
	}
	if !reflect.DeepEqual(fake.calls, []string{"get:dep-1"}) {
		t.Fatalf("terminal stop must only observe status: %v", fake.calls)
	}
}

func TestExecutorRestartSkipsStopForTerminalDeployment(t *testing.T) {
	deployment := executorDeployment()
	deployment.Status = "STOPPED"
	fake := executionFake{deployment: deployment}
	result := NewExecutor(&fake).Execute(context.Background(), ExecutionInput{Action: ActionRestart, Deployment: deployment})
	if result.Status != ExecutionSucceeded || result.StopCompleted {
		t.Fatalf("terminal restart should create without stop: %#v", result)
	}
	if !reflect.DeepEqual(fake.calls, []string{"create"}) {
		t.Fatalf("terminal restart calls=%v", fake.calls)
	}
}

func executorDeployment() appdeploy.DeploymentResponse {
	return appdeploy.DeploymentResponse{
		DeploymentID: "dep-1", AppVersionID: "appver-current", TargetProfileID: "target-primary", Status: "RUNNING",
		Manifest: appdeploy.DeploymentManifest{
			SchemaVersion: appdeploy.ManifestSchemaVersion, Kind: appdeploy.ManifestKind,
			Spec: appdeploy.DeploymentSpec{AppVersionID: "appver-current", TargetProfileID: "target-primary", Accelerator: "gpu", Resources: appdeploy.ResourceRequirements{CPU: "4", Memory: "16Gi", GPU: "1", Storage: "20Gi"}},
		},
	}
}

type executionFake struct {
	deployment appdeploy.DeploymentResponse
	calls      []string
	created    []appdeploy.DeploymentManifest
	createErr  error
}

func (fake *executionFake) GetDeployment(_ context.Context, id string) (appdeploy.DeploymentResponse, error) {
	fake.calls = append(fake.calls, "get:"+id)
	return fake.deployment, nil
}
func (fake *executionFake) ListDeploymentMetrics(_ context.Context, id string) (appdeploy.DeploymentMetricsResponse, error) {
	fake.calls = append(fake.calls, "metrics:"+id)
	return appdeploy.DeploymentMetricsResponse{}, nil
}
func (fake *executionFake) GetMonitoringSummary(context.Context) (appdeploy.MonitoringSummaryResponse, error) {
	return appdeploy.MonitoringSummaryResponse{}, nil
}
func (fake *executionFake) CreateDeployment(_ context.Context, manifest appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error) {
	fake.calls = append(fake.calls, "create")
	fake.created = append(fake.created, manifest)
	if fake.createErr != nil {
		return appdeploy.DeploymentResponse{}, fake.createErr
	}
	return appdeploy.DeploymentResponse{DeploymentID: "dep-new", Status: "REQUESTED", Manifest: manifest}, nil
}
func (fake *executionFake) StopDeployment(_ context.Context, id string) (appdeploy.DeploymentResponse, error) {
	fake.calls = append(fake.calls, "stop:"+id)
	return appdeploy.DeploymentResponse{DeploymentID: id, Status: "STOPPED"}, nil
}
