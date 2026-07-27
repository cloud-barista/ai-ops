package api

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/autonomy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type fakeControlRunManifestGenerator struct {
	calls  int
	result deploymentplanner.GenerateResult
	err    error
}

type fakeControlRunDeploymentClient struct {
	createResult appdeploy.DeploymentResponse
	createErr    error
	statuses     []appdeploy.DeploymentResponse
	statusIndex  int
	logs         appdeploy.DeploymentLogsResponse
}

func (client *fakeControlRunDeploymentClient) CreateDeployment(
	context.Context,
	appdeploy.DeploymentManifest,
) (appdeploy.DeploymentResponse, error) {
	return client.createResult, client.createErr
}

func (client *fakeControlRunDeploymentClient) GetDeployment(
	context.Context,
	string,
) (appdeploy.DeploymentResponse, error) {
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

func (client *fakeControlRunDeploymentClient) GetDeploymentLogs(
	context.Context,
	string,
) (appdeploy.DeploymentLogsResponse, error) {
	return client.logs, nil
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
	if run.Execution == nil || run.Execution.Status != "completed" {
		t.Fatalf("Manifest Agent did not run through Dispatcher: %#v", run.Execution)
	}

	wantStages := []string{"request_guard", "agent_registry", "agent_dispatch", "qwen_planner", "manifest_guard"}
	if len(run.Stages) != len(wantStages) {
		t.Fatalf("unexpected stages: %#v", run.Stages)
	}
	for index, want := range wantStages {
		if run.Stages[index].Name != want || run.Stages[index].Status != "approved" {
			t.Fatalf("unexpected stage %d: %#v", index, run.Stages[index])
		}
	}
}

func TestCreateControlRunRejectsPlannerWithoutInternalExecutorBeforeQwen(t *testing.T) {
	config := NewServerConfig()
	config.RepoRoot = writePlannerRegistryRoot(t, AgentProfile{
		Name:           "UnimplementedManifestAgent",
		Enabled:        true,
		Capabilities:   []string{capabilityDeploymentManifestPlanning},
		BoundedActions: []string{actionGenerateDeploymentManifest},
	})
	service := NewService(config)
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}
	request := validCreateControlRunRequest()
	request.AgentName = "UnimplementedManifestAgent"

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		request,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		NewServerConfig().PlannerGuardPolicyPath,
		generator,
	)
	if err == nil {
		t.Fatal("expected unimplemented internal Agent to be rejected")
	}
	if run.Status != controlrun.StatusManifestRejected || generator.calls != 0 {
		t.Fatalf("unimplemented Agent reached Qwen: run=%#v calls=%d", run, generator.calls)
	}
	if got := run.Stages[len(run.Stages)-1]; got.Name != "agent_dispatch" || got.Status != "rejected" {
		t.Fatalf("Dispatcher rejection was not recorded: %#v", run.Stages)
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

func TestSubmitControlRunDeploysApprovedManifest(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	run := createApprovedControlRun(t, service, config)
	deployer := &fakeControlRunDeploymentClient{
		createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-001", Status: "REQUESTED"},
		statuses: []appdeploy.DeploymentResponse{{
			DeploymentID:    "dep-001",
			Status:          "RUNNING",
			TargetProfileID: "target-appdeploy-selected",
		}},
		logs: appdeploy.DeploymentLogsResponse{Items: []appdeploy.DeploymentLog{{
			DeploymentID: "dep-001",
			Stage:        "RUNNING",
			Message:      "ready",
		}}},
	}

	submitted, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		run.RunID,
		SubmitControlRunRequest{PollIntervalMS: 1, MaxPollAttempts: 2},
		deployer,
	)
	if err != nil {
		t.Fatalf("submit ControlRun: %v", err)
	}
	if submitted.Status != controlrun.StatusDeployed || submitted.Deployment == nil {
		t.Fatalf("unexpected submitted Run: %#v", submitted)
	}
	if submitted.Deployment.DeploymentID != "dep-001" ||
		submitted.Deployment.TargetProfileID != "target-appdeploy-selected" ||
		len(submitted.Logs) != 1 {
		t.Fatalf("AppDeploy result was not attached: %#v", submitted)
	}
	if submitted.Manifest.Spec.AppVersionID != "appver-001" {
		t.Fatalf("approved Manifest was lost: %#v", submitted.Manifest)
	}
}

func TestSubmitControlRunRejectsInvalidState(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	run := createApprovedControlRun(t, service, config)
	deployer := &fakeControlRunDeploymentClient{
		createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-001", Status: "RUNNING"},
		logs:         appdeploy.DeploymentLogsResponse{},
	}
	submitted, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		run.RunID,
		SubmitControlRunRequest{},
		deployer,
	)
	if err != nil {
		t.Fatalf("first submit: %v", err)
	}
	if _, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		submitted.RunID,
		SubmitControlRunRequest{},
		deployer,
	); err == nil {
		t.Fatal("expected deployed Run resubmission to fail")
	}
}

func TestSubmitControlRunRequiresRegistrySubmitPermission(t *testing.T) {
	config := NewServerConfig()
	config.RepoRoot = writePlannerRegistryRoot(t, AgentProfile{
		Name:           "AIApplicationAutomationAgent",
		Enabled:        true,
		Capabilities:   []string{capabilityDeploymentManifestPlanning},
		BoundedActions: []string{actionGenerateDeploymentManifest},
	})
	service := NewService(config)
	request := validCreateControlRunRequest()
	request.AgentName = "AIApplicationAutomationAgent"
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}
	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		request,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		NewServerConfig().PlannerGuardPolicyPath,
		generator,
	)
	if err != nil {
		t.Fatalf("create approved Run: %v", err)
	}

	if _, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		run.RunID,
		SubmitControlRunRequest{},
		&fakeControlRunDeploymentClient{},
	); err == nil {
		t.Fatal("expected missing submit_deployment_manifest permission to fail")
	}
}

func TestSubmitControlRunPreservesManifestAfterAppDeployFailure(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	run := createApprovedControlRun(t, service, config)
	deployer := &fakeControlRunDeploymentClient{createErr: errors.New("AppDeploy unavailable")}

	failed, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		run.RunID,
		SubmitControlRunRequest{},
		deployer,
	)
	if err == nil {
		t.Fatal("expected AppDeploy failure")
	}
	if failed.Status != controlrun.StatusAppDeployFailed ||
		failed.Manifest.Spec.AppVersionID != "appver-001" {
		t.Fatalf("approved Manifest was not preserved: %#v", failed)
	}
}

func TestConfigureAutonomyUsesDeployedControlRun(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	run := createApprovedControlRun(t, service, config)
	deployer := &fakeControlRunDeploymentClient{
		createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-linked", Status: "RUNNING"},
		logs:         appdeploy.DeploymentLogsResponse{},
	}
	deployed, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		run.RunID,
		SubmitControlRunRequest{},
		deployer,
	)
	if err != nil {
		t.Fatalf("submit linked Run: %v", err)
	}

	autonomyConfig := autonomy.DefaultConfig()
	autonomyConfig.RunID = deployed.RunID
	if err := service.ConfigureAutonomy(autonomyConfig); err != nil {
		t.Fatalf("configure linked Autonomy: %v", err)
	}
	status := service.autonomyManager.Status()
	if status.Config.DeploymentID != "dep-linked" || status.Config.RunID != deployed.RunID {
		t.Fatalf("Autonomy was not linked to deployed Run: %#v", status.Config)
	}
}

func TestConfigureAutonomyRejectsControlRunWithoutDeployment(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	run := createApprovedControlRun(t, service, config)
	autonomyConfig := autonomy.DefaultConfig()
	autonomyConfig.RunID = run.RunID

	if err := service.ConfigureAutonomy(autonomyConfig); err == nil {
		t.Fatal("expected Manifest-only Run to be rejected for Autonomy")
	}
}

func TestActionProposalCorrelationIsAttachedToDeployedControlRun(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	run := createApprovedControlRun(t, service, config)
	deployed, err := service.SubmitControlRunWithDeployer(
		context.Background(),
		run.RunID,
		SubmitControlRunRequest{},
		&fakeControlRunDeploymentClient{
			createResult: appdeploy.DeploymentResponse{DeploymentID: "dep-linked", Status: "RUNNING"},
			logs:         appdeploy.DeploymentLogsResponse{},
		},
	)
	if err != nil {
		t.Fatalf("submit linked Run: %v", err)
	}
	provider, closeProvider := automationProvider(t, `{
		"action":"observe_status",
		"reason":"Observe the validated deployment.",
		"confidence":0.9,
		"required_capability":"ai_application_deployment_control",
		"target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
	}`)
	defer closeProvider()
	registerAutomationExecutor(t, service, "observe_status")

	result, err := service.PlanLLMAutomationActionFromPaths(
		context.Background(),
		service.config.path("config", "vm_workload_requirements.json"),
		writeAutomationCandidateConfig(t, provider),
		LLMAutomationActionRequest{
			RunID:       deployed.RunID,
			Workload:    "llm-chat-inference",
			TargetVM:    recordedL4VM(),
			CandidateID: "decision-model",
		},
	)
	if err != nil {
		t.Fatalf("plan linked Action: %v", err)
	}
	loaded, ok := service.GetControlRun(deployed.RunID)
	if !ok || result.CorrelationID == "" ||
		len(loaded.CorrelationIDs) != 1 ||
		loaded.CorrelationIDs[0] != result.CorrelationID {
		t.Fatalf("Action correlation was not attached: result=%#v run=%#v", result, loaded)
	}

	record, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID: result.CorrelationID,
		Executor:      "GenericDeploymentExecutor",
		Status:        "succeeded",
		Message:       "execution completed",
	})
	if err != nil {
		t.Fatalf("record linked Feedback: %v", err)
	}
	loaded, ok = service.GetControlRun(deployed.RunID)
	lastStage := loaded.Stages[len(loaded.Stages)-1]
	if !ok || record.RunID != deployed.RunID ||
		lastStage.Name != "execution_feedback" ||
		lastStage.Status != "succeeded" {
		t.Fatalf("Feedback was not attached to the Run timeline: record=%#v run=%#v", record, loaded)
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

func createApprovedControlRun(t *testing.T, service Service, config ServerConfig) controlrun.Run {
	t.Helper()
	generator := &fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")}
	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		validCreateControlRunRequest(),
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		generator,
	)
	if err != nil {
		t.Fatalf("create approved ControlRun: %v", err)
	}
	return run
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
