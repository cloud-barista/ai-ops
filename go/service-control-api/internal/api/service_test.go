package api

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/automation"
)

func TestSelectOpsLLMMatchesConfiguredBaseline(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.SelectOpsLLM(context.Background(), "quality_first")
	if err != nil {
		t.Fatalf("SelectOpsLLM returned error: %v", err)
	}

	if result.SelectedModel != "primary-ops-llm" {
		t.Fatalf("expected primary-ops-llm, got %s", result.SelectedModel)
	}
	if result.SelectedActualModel != "qwen3.5:4b" {
		t.Fatalf("expected qwen3.5:4b, got %s", result.SelectedActualModel)
	}
	if result.EvaluationType != "prototype_policy_baseline" {
		t.Fatalf("expected prototype policy baseline, got %s", result.EvaluationType)
	}
	if result.BenchmarkStatus != "not_executed" {
		t.Fatalf("expected not_executed benchmark status, got %s", result.BenchmarkStatus)
	}
	if result.SelectedScore != 0.891333 {
		t.Fatalf("expected score 0.891333, got %.6f", result.SelectedScore)
	}
	if len(result.Ranking) != 3 {
		t.Fatalf("expected 3 ranked models, got %d", len(result.Ranking))
	}
	if result.Ranking[1].Model != "low-cost-ops-llm" {
		t.Fatalf("expected low-cost-ops-llm second, got %s", result.Ranking[1].Model)
	}
}

func TestValidateAgentActionUsesRegistryBounds(t *testing.T) {
	service := NewService(NewServerConfig())

	valid, err := service.ValidateAgentAction(
		context.Background(),
		"AIApplicationAutomationAgent",
		"observe_status",
	)
	if err != nil {
		t.Fatalf("ValidateAgentAction returned error: %v", err)
	}
	if !valid {
		t.Fatal("expected observe_status to be valid")
	}

	valid, err = service.ValidateAgentAction(
		context.Background(),
		"AIApplicationAutomationAgent",
		"restart_vm",
	)
	if err != nil {
		t.Fatalf("ValidateAgentAction returned error for known agent: %v", err)
	}
	if valid {
		t.Fatal("expected restart_vm to be invalid for application agent")
	}
}

func TestValidateVMSuitabilityUsesRecordedVMInsteadOfRankingCandidates(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.ValidateVMSuitability(context.Background(), VMCompatibilityRequest{
		Workload: "llm-chat-inference",
		TargetVM: recordedL4VM(),
	})
	if err != nil {
		t.Fatalf("ValidateVMSuitability returned error: %v", err)
	}

	if result.TargetVMID != "aws-us-west-2-g6-xlarge-l4-20260707" {
		t.Fatalf("expected recorded target VM, got %s", result.TargetVMID)
	}
	if result.ValidationMode != "actual_vm_compatibility" {
		t.Fatalf("expected actual VM compatibility mode, got %s", result.ValidationMode)
	}
	if result.CompatibilityStatus != "provisionally_compatible" {
		t.Fatalf("expected provisional compatibility, got %s", result.CompatibilityStatus)
	}
	if !result.ResourceChecksPassed {
		t.Fatalf("expected resource checks to pass: %#v", result.Checks)
	}
	if result.PerformanceStatus != "not_measured" {
		t.Fatalf("expected unmeasured performance to be explicit, got %s", result.PerformanceStatus)
	}
}

func TestValidateVMSuitabilityRejectsGPUWorkloadOnCPUTarget(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.ValidateVMSuitability(context.Background(), VMCompatibilityRequest{
		Workload: "llm-chat-inference",
		TargetVM: VMResourceSnapshot{
			ID:             "cb-tumblebug-cpu-vm",
			Source:         "cb-tumblebug",
			EvidenceStatus: "collected",
			Accelerator:    "cpu",
		},
	})
	if err != nil {
		t.Fatalf("ValidateVMSuitability returned error: %v", err)
	}
	if result.CompatibilityStatus != "incompatible" {
		t.Fatalf("expected incompatible result, got %s", result.CompatibilityStatus)
	}
	if result.Action != "manual_review_required" {
		t.Fatalf("expected manual review action, got %s", result.Action)
	}
}

func TestValidateVMSuitabilityRejectsMeasuredPerformanceBelowRequirement(t *testing.T) {
	service := NewService(NewServerConfig())
	configPath := filepath.Join(t.TempDir(), "vm-requirements.json")
	config := `{
  "version": "1",
  "validation_mode": "actual_vm_compatibility",
  "workloads": [{
    "id": "measured-llm",
    "service_name": "measured-llm",
    "required_accelerator": "gpu",
    "latency_slo_ms": 100,
    "minimum_throughput_rps": 10,
    "allowed_control_actions": ["observe_status"],
    "requirement_source": "test"
  }]
}`
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write requirements config: %v", err)
	}

	latency := 180.0
	throughput := 4.0
	target := recordedL4VM()
	target.Performance = VMPerformanceEvidence{
		Status:        "measured",
		LatencyMS:     &latency,
		ThroughputRPS: &throughput,
		Source:        "workload-benchmark",
	}

	result, err := service.ValidateVMSuitabilityFromPath(context.Background(), configPath, VMCompatibilityRequest{
		Workload: "measured-llm",
		TargetVM: target,
	})
	if err != nil {
		t.Fatalf("ValidateVMSuitabilityFromPath returned error: %v", err)
	}
	if !result.ResourceChecksPassed {
		t.Fatalf("expected hardware checks to pass: %#v", result.Checks)
	}
	if result.CompatibilityStatus != "incompatible" {
		t.Fatalf("expected measured SLO failure to be incompatible, got %s", result.CompatibilityStatus)
	}
	if !hasFailedCheck(result.Checks, "latency_slo_ms") {
		t.Fatalf("expected latency SLO failure: %#v", result.Checks)
	}
	if !hasFailedCheck(result.Checks, "minimum_throughput_rps") {
		t.Fatalf("expected throughput failure: %#v", result.Checks)
	}
}

func hasFailedCheck(checks []VMCompatibilityCheck, name string) bool {
	for _, check := range checks {
		if check.Name == name && check.Status == "fail" {
			return true
		}
	}
	return false
}

func TestBuildDeploymentPlanCreatesNonExecutingRegisteredAgentHandoff(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.BuildDeploymentPlan(context.Background(), VMCompatibilityRequest{
		Workload: "llm-chat-inference",
		TargetVM: recordedL4VM(),
	})
	if err != nil {
		t.Fatalf("BuildDeploymentPlan returned error: %v", err)
	}
	if result.DeploymentPlan.ExecutorType != "registered_external_agent" {
		t.Fatalf("expected registered external agent executor, got %s", result.DeploymentPlan.ExecutorType)
	}
	if result.DeploymentPlan.RequiredCapability != "ai_application_deployment_control" {
		t.Fatalf("expected generic deployment-control capability, got %s", result.DeploymentPlan.RequiredCapability)
	}
	if result.DeploymentPlan.ExecutionStatus != "not_executed" {
		t.Fatalf("expected non-executing handoff plan, got %s", result.DeploymentPlan.ExecutionStatus)
	}
	if result.DeploymentPlan.TargetVMID != recordedL4VM().ID {
		t.Fatalf("expected recorded VM target, got %s", result.DeploymentPlan.TargetVMID)
	}
	if result.DeploymentPlan.SelectedExecutor != "" {
		t.Fatalf("expected no hard-coded executor, got %s", result.DeploymentPlan.SelectedExecutor)
	}
}

func TestBuildDeploymentPlanSelectsAnyRegisteredAgentWithRequiredCapability(t *testing.T) {
	service := NewService(NewServerConfig())
	enabled := true
	_, err := service.RegisterExternalAgent(context.Background(), ExternalAgentRegistrationRequest{
		Name:           "TeamExecutionAgent",
		Version:        "v1",
		Role:           "Execute approved AI application deployment and control requests.",
		Endpoint:       "https://execution.example.invalid",
		InvocationPath: "/v1/control",
		Capabilities:   []string{"ai_application_deployment_control"},
		BoundedActions: []string{"deploy_application"},
		Enabled:        &enabled,
	})
	if err != nil {
		t.Fatalf("RegisterExternalAgent returned error: %v", err)
	}

	result, err := service.BuildDeploymentPlan(context.Background(), VMCompatibilityRequest{
		Workload: "llm-chat-inference",
		TargetVM: recordedL4VM(),
	})
	if err != nil {
		t.Fatalf("BuildDeploymentPlan returned error: %v", err)
	}
	if result.DeploymentPlan.SelectedExecutor != "TeamExecutionAgent" {
		t.Fatalf("expected capability-based executor selection, got %s", result.DeploymentPlan.SelectedExecutor)
	}
}

func TestPlanLLMAutomationActionApprovesAllowedAction(t *testing.T) {
	service := NewService(NewServerConfig())
	registerAutomationExecutor(t, service, "observe_status")
	provider, closeProvider := automationProvider(t, `{
  "action":"observe_status",
  "reason":"Performance evidence is not measured.",
  "confidence":0.82,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`)
	defer closeProvider()

	result, err := service.PlanLLMAutomationActionFromPaths(
		context.Background(),
		service.config.path("config", "vm_workload_requirements.json"),
		writeAutomationCandidateConfig(t, provider),
		LLMAutomationActionRequest{
			Workload:    "llm-chat-inference",
			TargetVM:    recordedL4VM(),
			CandidateID: "decision-model",
		},
	)
	if err != nil {
		t.Fatalf("PlanLLMAutomationActionFromPaths returned error: %v", err)
	}
	if !result.Valid || result.Status != "approved" {
		t.Fatalf("expected approved result: %#v", result)
	}
	if result.Decision.DecisionExecutionStatus != "executed" {
		t.Fatalf("expected executed LLM decision: %#v", result.Decision)
	}
	if !result.Guard.Valid || result.Handoff.Agent != "GenericDeploymentExecutor" {
		t.Fatalf("expected guarded generic handoff: %#v", result)
	}
	if result.Handoff.ExecutionStatus != "not_executed" {
		t.Fatalf("expected non-executing handoff, got %s", result.Handoff.ExecutionStatus)
	}
}

func TestPlanLLMAutomationActionRejectsUnboundedAction(t *testing.T) {
	service := NewService(NewServerConfig())
	provider, closeProvider := automationProvider(t, `{
  "action":"scale_out",
  "reason":"scale requested",
  "confidence":0.9,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`)
	defer closeProvider()

	result, err := service.PlanLLMAutomationActionFromPaths(
		context.Background(),
		service.config.path("config", "vm_workload_requirements.json"),
		writeAutomationCandidateConfig(t, provider),
		LLMAutomationActionRequest{Workload: "llm-chat-inference", TargetVM: recordedL4VM(), CandidateID: "decision-model"},
	)
	if err != nil {
		t.Fatalf("policy rejection should be a response, got error: %v", err)
	}
	if result.Valid || result.Status != "rejected" || result.Guard.Valid {
		t.Fatalf("expected guard rejection: %#v", result)
	}
}

func TestPlanLLMAutomationActionSkipsLLMForIncompatibleVM(t *testing.T) {
	service := NewService(NewServerConfig())
	providerCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		providerCalls++
	}))
	defer server.Close()
	target := recordedL4VM()
	target.Accelerator = "cpu"

	result, err := service.PlanLLMAutomationActionFromPaths(
		context.Background(),
		service.config.path("config", "vm_workload_requirements.json"),
		writeAutomationCandidateConfig(t, server.URL),
		LLMAutomationActionRequest{Workload: "llm-chat-inference", TargetVM: target, CandidateID: "decision-model"},
	)
	if err != nil {
		t.Fatalf("incompatible VM should return a result: %v", err)
	}
	if result.Status != "vm_incompatible" || providerCalls != 0 {
		t.Fatalf("expected VM rejection before LLM call: result=%#v calls=%d", result, providerCalls)
	}
}

func TestPlanLLMAutomationActionReturnsPendingWhenExecutorMissing(t *testing.T) {
	service := NewService(NewServerConfig())
	provider, closeProvider := automationProvider(t, `{
  "action":"observe_status",
  "reason":"observe before deployment",
  "confidence":0.75,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`)
	defer closeProvider()

	result, err := service.PlanLLMAutomationActionFromPaths(
		context.Background(),
		service.config.path("config", "vm_workload_requirements.json"),
		writeAutomationCandidateConfig(t, provider),
		LLMAutomationActionRequest{Workload: "llm-chat-inference", TargetVM: recordedL4VM(), CandidateID: "decision-model"},
	)
	if err != nil {
		t.Fatalf("PlanLLMAutomationActionFromPaths returned error: %v", err)
	}
	if result.Valid || result.Status != "pending_executor" || !result.Guard.Valid {
		t.Fatalf("expected pending executor result: %#v", result)
	}
}

func TestPlanLLMAutomationActionDoesNotFallbackAfterProviderFailure(t *testing.T) {
	service := NewService(NewServerConfig())
	result, err := service.PlanLLMAutomationActionFromPaths(
		context.Background(),
		service.config.path("config", "vm_workload_requirements.json"),
		writeAutomationCandidateConfig(t, "http://127.0.0.1:1/v1/chat/completions"),
		LLMAutomationActionRequest{Workload: "llm-chat-inference", TargetVM: recordedL4VM(), CandidateID: "decision-model"},
	)
	if err == nil {
		t.Fatalf("expected provider failure, got %#v", result)
	}
	if result.Decision.DecisionExecutionStatus != "llm_failed" || result.Valid {
		t.Fatalf("expected failed real LLM call without fallback: %#v", result)
	}
}

func automationProvider(t *testing.T, content string) (string, func()) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("content-type", "application/json")
		_, _ = fmt.Fprintf(writer, `{"choices":[{"message":{"content":%q}}]}`, content)
	}))
	return server.URL, server.Close
}

func writeAutomationCandidateConfig(t *testing.T, endpoint string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "candidates.json")
	content := fmt.Sprintf(`{
  "version":"1",
  "candidates":[{
    "candidate_id":"decision-model",
    "role_label":"primary-ops-llm",
    "provider":"test-provider",
    "actual_model":"test-model",
    "endpoint":%q,
    "enabled":true,
    "timeout_seconds":1
  }]
}`, endpoint)
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write candidate config: %v", err)
	}
	return path
}

func registerAutomationExecutor(t *testing.T, service Service, action string) {
	t.Helper()
	enabled := true
	_, err := service.RegisterExternalAgent(context.Background(), ExternalAgentRegistrationRequest{
		Name:           "GenericDeploymentExecutor",
		Version:        "v1",
		Role:           "Execute approved AI application control actions.",
		Endpoint:       "http://executor.internal",
		InvocationPath: "/v1/actions",
		Capabilities:   []string{"ai_application_deployment_control"},
		BoundedActions: []string{action},
		Enabled:        &enabled,
	})
	if err != nil {
		t.Fatalf("register executor: %v", err)
	}
}

func TestRunServiceOperationsCombinesCoreDecisionsInGo(t *testing.T) {
	service := NewService(NewServerConfig())

	report, err := service.RunServiceOperations(context.Background(), ServiceOperationsRequest{
		LLMPolicy:         "quality_first",
		Workload:          "llm-chat-inference",
		OperationService:  "llm-chat-inference",
		OperationResource: recordedL4VM().ID,
		Mode:              "plan_only",
		GuardBackend:      "go",
		LLMConfigPath:     "config/ops_llm_benchmark.json",
		VMRequirements:    "config/vm_workload_requirements.json",
		TargetVM:          recordedL4VM(),
	})
	if err != nil {
		t.Fatalf("RunServiceOperations returned error: %v", err)
	}

	if !report.Valid {
		t.Fatalf("expected report to be valid: %#v", report)
	}
	if report.SelectedLLM != "primary-ops-llm" {
		t.Fatalf("expected primary-ops-llm, got %s", report.SelectedLLM)
	}
	if report.SelectedActualModel != "qwen3.5:4b" {
		t.Fatalf("expected qwen3.5:4b, got %s", report.SelectedActualModel)
	}
	if report.BenchmarkStatus != "not_executed" {
		t.Fatalf("expected benchmark status not_executed, got %s", report.BenchmarkStatus)
	}
	if report.DecisionExecutionStatus != "not_executed" {
		t.Fatalf("expected explicit skipped LLM decision, got %s", report.DecisionExecutionStatus)
	}
	if report.SelectedProvider != "prototype-policy" {
		t.Fatalf("expected selected provider prototype-policy, got %s", report.SelectedProvider)
	}
	if report.SelectedResource != recordedL4VM().ID {
		t.Fatalf("expected recorded VM, got %s", report.SelectedResource)
	}
	if report.OperationPipelineReady {
		t.Fatal("expected external execution pipeline to remain unwired")
	}
	if report.GuardBackend != "go" {
		t.Fatalf("expected guard backend go, got %s", report.GuardBackend)
	}
	if !report.GuardValidation.Valid {
		t.Fatalf("expected guard validation to be valid: %#v", report.GuardValidation)
	}
	if report.GuardValidation.RuntimeWired {
		t.Fatal("expected standalone guard runtime wiring to remain false")
	}
	if !report.DeploymentValidation.Valid {
		t.Fatalf("expected VM deployment validation to pass: %#v", report.DeploymentValidation)
	}
	if !report.AgentReviews.Application.Approved {
		t.Fatalf("expected application review approval: %#v", report.AgentReviews.Application)
	}
	if !report.AgentReviews.Infrastructure.Approved {
		t.Fatalf("expected infrastructure review approval: %#v", report.AgentReviews.Infrastructure)
	}
	if !report.AgentReviews.Cost.Skipped {
		t.Fatalf("expected unmeasured cost review to be skipped: %#v", report.AgentReviews.Cost)
	}
}

func TestRunServiceOperationsUsesActualLLMActionWhenConfigured(t *testing.T) {
	service := NewService(NewServerConfig())
	registerAutomationExecutor(t, service, "observe_status")
	provider, closeProvider := automationProvider(t, `{
  "action":"observe_status",
  "reason":"Observe the provisionally compatible VM.",
  "confidence":0.84,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`)
	defer closeProvider()

	report, err := service.RunServiceOperations(context.Background(), ServiceOperationsRequest{
		LLMPolicy:         "quality_first",
		LLMCandidatesPath: writeAutomationCandidateConfig(t, provider),
		LLMCandidateID:    "decision-model",
		Workload:          "llm-chat-inference",
		Mode:              "plan_only",
		GuardBackend:      "go",
		LLMConfigPath:     "config/ops_llm_benchmark.json",
		VMRequirements:    "config/vm_workload_requirements.json",
		TargetVM:          recordedL4VM(),
	})
	if err != nil {
		t.Fatalf("RunServiceOperations returned error: %v", err)
	}
	if report.DecisionExecutionStatus != "executed" {
		t.Fatalf("expected actual LLM decision execution: %#v", report.LLMAutomationAction)
	}
	if !report.LLMAutomationAction.Valid || !report.LLMAutomationAction.Guard.Valid {
		t.Fatalf("expected approved LLM Action: %#v", report.LLMAutomationAction)
	}
	if report.LLMAutomationAction.Handoff.Action != "observe_status" {
		t.Fatalf("expected LLM-selected bounded Action, got %#v", report.LLMAutomationAction.Handoff)
	}
}

func recordedL4VM() VMResourceSnapshot {
	return VMResourceSnapshot{
		ID:               "aws-us-west-2-g6-xlarge-l4-20260707",
		Source:           "cb-tumblebug_and_vm_evidence",
		EvidenceStatus:   "collected",
		Provider:         "aws",
		Region:           "us-west-2",
		AvailabilityZone: "us-west-2a",
		InstanceType:     "g6.xlarge",
		Accelerator:      "gpu",
		GPUModel:         "NVIDIA L4",
		GPUMemoryMiB:     23034,
		DriverVersion:    "595.71.05",
		CUDAVersion:      "13.2",
		CollectedAt:      "2026-07-07T07:48:03Z",
		Performance: VMPerformanceEvidence{
			Status: "not_measured",
		},
	}
}

func TestAgentReviewsDistinguishLLMAgentFromGoValidation(t *testing.T) {
	reviews := buildAgentReviews(DeploymentPlanResponse{
		VMCompatibilityResponse: VMCompatibilityResponse{
			Valid:                true,
			ResourceChecksPassed: true,
			TargetVMID:           "recorded-l4-vm",
			ResourceSource:       "recorded_vm_snapshot",
			PerformanceStatus:    "not_measured",
		},
		DeploymentPlan: DeploymentPlan{
			Workload:    "llm-chat-inference",
			ServiceName: "llm-chat-inference",
		},
	})

	if reviews.Application.Agent != "AIApplicationAutomationAgent" || reviews.Application.ReviewerType != "llm_agent" {
		t.Fatalf("unexpected application reviewer: %#v", reviews.Application)
	}
	if reviews.Infrastructure.Agent != "" || reviews.Infrastructure.ReviewerType != "deterministic_go_validator" {
		t.Fatalf("VM suitability must be a Go validator, not an AI agent: %#v", reviews.Infrastructure)
	}
	if reviews.Cost.Agent != "" || reviews.Cost.ReviewerType != "evidence_check" {
		t.Fatalf("cost review must remain an evidence check: %#v", reviews.Cost)
	}
}

func TestValidateLLMActionProposalRequiresAgentRegistryPermission(t *testing.T) {
	guard := validateLLMActionProposal(
		VMWorkloadRequirement{AllowedControlActions: []string{"observe_status"}},
		"recorded-l4-vm",
		AgentProfile{
			Name:           "AIApplicationAutomationAgent",
			Enabled:        true,
			Capabilities:   []string{"ai_application_deployment_control"},
			BoundedActions: []string{"stop_application"},
		},
		automation.ActionProposal{
			Action:             "observe_status",
			RequiredCapability: "ai_application_deployment_control",
			TargetVMID:         "recorded-l4-vm",
		},
	)

	if guard.Valid || !strings.Contains(guard.Reason, "Agent Registry") {
		t.Fatalf("expected Agent Registry rejection, got %#v", guard)
	}
}
