package deploymentplanner

import (
	"context"
	"errors"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type fakeManifestGenerator struct {
	result GenerateResult
	err    error
}

func (generator fakeManifestGenerator) Generate(context.Context, llmclient.Candidate, GenerateInput) (GenerateResult, error) {
	return generator.result, generator.err
}

type fakeDeploymentClient struct {
	createResult appdeploy.DeploymentResponse
	createErr    error
	statuses     []appdeploy.DeploymentResponse
	statusIndex  int
	logs         appdeploy.DeploymentLogsResponse
}

func (client *fakeDeploymentClient) CreateDeployment(context.Context, appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error) {
	return client.createResult, client.createErr
}

func (client *fakeDeploymentClient) GetDeployment(context.Context, string) (appdeploy.DeploymentResponse, error) {
	if len(client.statuses) == 0 {
		return appdeploy.DeploymentResponse{}, errors.New("no status configured")
	}
	index := client.statusIndex
	if index >= len(client.statuses) {
		index = len(client.statuses) - 1
	}
	client.statusIndex++
	return client.statuses[index], nil
}

func (client *fakeDeploymentClient) GetDeploymentLogs(context.Context, string) (appdeploy.DeploymentLogsResponse, error) {
	return client.logs, nil
}

func TestPlannerSubmitsManifestAndPollsUntilRunning(t *testing.T) {
	client := &fakeDeploymentClient{
		createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-1", Status: "REQUESTED"},
		statuses: []appdeploy.DeploymentResponse{
			{DeploymentID: "dep-1", Status: "VALIDATING"},
			{DeploymentID: "dep-1", Status: "RUNNING", TargetProfileID: "target-selected"},
		},
		logs: appdeploy.DeploymentLogsResponse{Items: []appdeploy.DeploymentLog{{Stage: "RUNNING", Message: "ready"}}},
	}
	planner := NewPlanner(successfulGenerator(), client)
	result, err := planner.PlanAndDeploy(context.Background(), plannerRequest())
	if err != nil {
		t.Fatalf("plan and deploy: %v", err)
	}
	if !result.Valid || result.Status != "RUNNING" || result.Polling.Attempts != 2 {
		t.Fatalf("unexpected planner result: %#v", result)
	}
	if result.Deployment.TargetProfileID != "target-selected" || len(result.Logs) != 1 {
		t.Fatalf("missing selected target or logs: %#v", result)
	}
	if result.RetryRecommended {
		t.Fatalf("successful deployment must not recommend retry: %#v", result)
	}
}

func TestPlannerReturnsTerminalFailureAndLogs(t *testing.T) {
	client := &fakeDeploymentClient{
		createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-2", Status: "REQUESTED"},
		statuses: []appdeploy.DeploymentResponse{
			{DeploymentID: "dep-2", Status: "DEPLOYMENT_FAILED"},
		},
		logs: appdeploy.DeploymentLogsResponse{Items: []appdeploy.DeploymentLog{{Stage: "DEPLOYING", ErrorCode: "DEPLOYMENT_FAILED", Message: "runtime rejected request"}}},
	}
	planner := NewPlanner(successfulGenerator(), client)
	result, err := planner.PlanAndDeploy(context.Background(), plannerRequest())
	if err != nil {
		t.Fatalf("terminal deployment failure should return a structured result: %v", err)
	}
	if result.Valid || result.Status != "DEPLOYMENT_FAILED" || len(result.Logs) != 1 {
		t.Fatalf("unexpected failure result: %#v", result)
	}
	if result.RetryRecommended {
		t.Fatalf("status alone must not imply retryability: %#v", result)
	}
}

func TestPlannerRecommendsRetryOnlyForExplicitRetryableCreateError(t *testing.T) {
	client := &fakeDeploymentClient{
		createErr: &appdeploy.APIError{StatusCode: 503, Code: "AI_INFRA_API_TIMEOUT", Retryable: true},
	}
	planner := NewPlanner(successfulGenerator(), client)
	result, err := planner.PlanAndDeploy(context.Background(), plannerRequest())
	if err == nil {
		t.Fatal("expected create error")
	}
	if !result.RetryRecommended || result.Status != "APPDEPLOY_REQUEST_FAILED" {
		t.Fatalf("unexpected retry decision: %#v", result)
	}
}

func TestPlannerStopsAfterBoundedPolling(t *testing.T) {
	client := &fakeDeploymentClient{
		createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-3", Status: "REQUESTED"},
		statuses:     []appdeploy.DeploymentResponse{{DeploymentID: "dep-3", Status: "DEPLOYING"}},
	}
	planner := NewPlanner(successfulGenerator(), client)
	request := plannerRequest()
	request.MaxPollAttempts = 2
	result, err := planner.PlanAndDeploy(context.Background(), request)
	if err != nil {
		t.Fatalf("poll exhaustion should return a structured result: %v", err)
	}
	if result.Valid || result.Status != "POLL_TIMEOUT" || result.Polling.Attempts != 2 {
		t.Fatalf("unexpected poll timeout: %#v", result)
	}
}

func successfulGenerator() fakeManifestGenerator {
	return fakeManifestGenerator{result: GenerateResult{
		ExecutionStatus: "executed",
		GuardValid:      true,
		Manifest:        plannerManifest(),
	}}
}

func plannerRequest() Request {
	return Request{
		Candidate: llmclient.Candidate{CandidateID: "planner-test", Enabled: true},
		GenerateInput: GenerateInput{
			NaturalLanguageRequest: "GPU 서비스를 배포해줘.",
			AppVersionID:           "appver-test",
			RequestedBy:            "ai-ops-geon-planner",
		},
		PollInterval:    time.Nanosecond,
		MaxPollAttempts: 4,
	}
}

func plannerManifest() appdeploy.DeploymentManifest {
	return appdeploy.DeploymentManifest{
		SchemaVersion: appdeploy.ManifestSchemaVersion,
		Kind:          appdeploy.ManifestKind,
		Spec: appdeploy.DeploymentSpec{
			AppVersionID: "appver-test",
			Accelerator:  "nvidia",
			Resources: appdeploy.ResourceRequirements{
				CPU: "4", Memory: "16Gi", GPU: "1", Storage: "20Gi",
			},
			RequestedBy: "ai-ops-geon-planner",
		},
	}
}
