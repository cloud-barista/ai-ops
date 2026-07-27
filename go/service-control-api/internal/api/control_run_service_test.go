package api

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type fakeControlRunManifestGenerator struct {
	calls  int
	result deploymentplanner.GenerateResult
	err    error
}

func (generator *fakeControlRunManifestGenerator) Generate(
	_ context.Context,
	_ llmclient.Candidate,
	_ deploymentplanner.GenerateInput,
) (deploymentplanner.GenerateResult, error) {
	generator.calls++
	return generator.result, generator.err
}

func TestCreateControlRunReturnsApprovedManifestWithoutAppDeploy(t *testing.T) {
	config := NewServerConfig()
	config.AppDeployBaseURL = ""
	service := NewService(config)
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		validCreateControlRunRequest(),
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		generator,
	)
	if err != nil {
		t.Fatalf("create ControlRun: %v", err)
	}
	if run.Status != controlrun.StatusManifestApproved || run.Manifest.Spec.AppVersionID != "appver-001" {
		t.Fatalf("unexpected approved Run: %#v", run)
	}
	if run.SelectedAgent.Name != "AIApplicationAutomationAgent" || generator.calls != 1 {
		t.Fatalf("Registry or Qwen stage was not connected: run=%#v calls=%d", run.SelectedAgent, generator.calls)
	}

	wantStages := []string{"request_guard", "agent_registry", "qwen_planner", "manifest_guard"}
	if len(run.Stages) != len(wantStages) {
		t.Fatalf("unexpected stages: %#v", run.Stages)
	}
	for index, want := range wantStages {
		if run.Stages[index].Name != want || run.Stages[index].Status != "approved" {
			t.Fatalf("unexpected stage %d: %#v", index, run.Stages[index])
		}
	}
}

func TestCreateControlRunRejectsRequestBeforeRegistryAndQwen(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}
	request := validCreateControlRunRequest()
	request.NaturalLanguageRequest = "Deploy this application to Kubernetes with kubectl."

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		request,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		generator,
	)
	if err == nil {
		t.Fatal("expected Request Guard rejection")
	}
	if run.Status != controlrun.StatusRequestRejected || generator.calls != 0 {
		t.Fatalf("request did not fail before Qwen: run=%#v calls=%d", run, generator.calls)
	}
	if len(run.Stages) != 1 || run.Stages[0].Name != "request_guard" || run.Stages[0].Status != "rejected" {
		t.Fatalf("unexpected rejection stages: %#v", run.Stages)
	}
}

func TestCreateControlRunRejectsUnauthorizedPlannerBeforeQwen(t *testing.T) {
	config := NewServerConfig()
	config.RepoRoot = writePlannerRegistryRoot(t, AgentProfile{
		Name:           "AIApplicationAutomationAgent",
		Enabled:        false,
		Capabilities:   []string{capabilityDeploymentManifestPlanning},
		BoundedActions: []string{actionGenerateDeploymentManifest},
	})
	service := NewService(config)
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		validCreateControlRunRequest(),
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		NewServerConfig().PlannerGuardPolicyPath,
		generator,
	)
	if err == nil {
		t.Fatal("expected Agent Registry rejection")
	}
	if run.Status != controlrun.StatusAgentRejected || generator.calls != 0 {
		t.Fatalf("Agent rejection did not stop Qwen: run=%#v calls=%d", run, generator.calls)
	}
}

func TestCreateControlRunRecordsManifestGuardRejection(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	result := approvedGenerateResult("appver-001")
	result.Manifest.Spec.Accelerator = "none"
	result.Manifest.Spec.Resources.GPU = "1"
	generator := &fakeControlRunManifestGenerator{result: result}

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		validCreateControlRunRequest(),
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		generator,
	)
	if err == nil {
		t.Fatal("expected Manifest Guard rejection")
	}
	if run.Status != controlrun.StatusManifestRejected {
		t.Fatalf("unexpected rejected Run: %#v", run)
	}
	if got := run.Stages[len(run.Stages)-1]; got.Name != "manifest_guard" || got.Status != "rejected" {
		t.Fatalf("Manifest Guard rejection was not recorded: %#v", run.Stages)
	}
}

func validCreateControlRunRequest() CreateControlRunRequest {
	return CreateControlRunRequest{
		NaturalLanguageRequest: "Deploy an inference application with CPU 2 and memory 4Gi.",
		AppVersionID:           "appver-001",
		CandidateID:            "decision-model",
		RequestedBy:            "ai-ops-geon-planner",
	}
}

func approvedGenerateResult(appVersionID string) deploymentplanner.GenerateResult {
	manifest := appdeploy.DeploymentManifest{
		SchemaVersion: appdeploy.ManifestSchemaVersion,
		Kind:          appdeploy.ManifestKind,
		Spec: appdeploy.DeploymentSpec{
			AppVersionID: appVersionID,
			Accelerator:  "none",
			Resources: appdeploy.ResourceRequirements{
				CPU:     "2",
				Memory:  "4Gi",
				GPU:     "0",
				Storage: "10Gi",
			},
			RequestedBy: "ai-ops-geon-planner",
		},
	}
	return deploymentplanner.GenerateResult{
		ExecutionStatus: "executed",
		CandidateID:     "decision-model",
		Provider:        "test-provider",
		ActualModel:     "test-model",
		GuardValid:      true,
		GuardReason:     "validated",
		Manifest:        manifest,
	}
}

func writePlannerRegistryRoot(t *testing.T, agents ...AgentProfile) string {
	t.Helper()
	root := t.TempDir()
	configDir := filepath.Join(root, "config")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatalf("create config directory: %v", err)
	}
	contents, err := json.Marshal(AgentRegistry{Version: "test", Agents: agents})
	if err != nil {
		t.Fatalf("encode Agent Registry: %v", err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "agent_registry.json"), contents, 0o600); err != nil {
		t.Fatalf("write Agent Registry: %v", err)
	}
	return root
}
