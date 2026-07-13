package api

import (
	"context"
	"testing"
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
	if result.SelectedActualModel != "to-be-evaluated-primary-model" {
		t.Fatalf("expected selected actual model placeholder, got %s", result.SelectedActualModel)
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
		"AIApplicationManagementAgent",
		"app_select_inference_vm",
	)
	if err != nil {
		t.Fatalf("ValidateAgentAction returned error: %v", err)
	}
	if !valid {
		t.Fatal("expected app_select_inference_vm to be valid")
	}

	valid, err = service.ValidateAgentAction(
		context.Background(),
		"AIApplicationManagementAgent",
		"infra_select_cpu_gpu_vm",
	)
	if err != nil {
		t.Fatalf("ValidateAgentAction returned error for known agent: %v", err)
	}
	if valid {
		t.Fatal("expected infra_select_cpu_gpu_vm to be invalid for application agent")
	}
}

func TestRecommendPlacementMatchesConfiguredScoreBaseline(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.RecommendPlacement(context.Background(), "llm-chat-inference")
	if err != nil {
		t.Fatalf("RecommendPlacement returned error: %v", err)
	}

	if result.SelectedResource != "gpu-vm-l4" {
		t.Fatalf("expected gpu-vm-l4, got %s", result.SelectedResource)
	}
	if result.Score != 1.0 {
		t.Fatalf("expected placement score 1.0, got %.6f", result.Score)
	}
	if result.RejectedResources["cpu-vm-standard"] != "accelerator required but resource is CPU-only" {
		t.Fatalf("unexpected CPU rejection: %#v", result.RejectedResources)
	}
}

func TestBuildDeploymentPlanUsesBoundedResourceRequests(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.BuildDeploymentPlan(context.Background(), "text-classifier")
	if err != nil {
		t.Fatalf("BuildDeploymentPlan returned error: %v", err)
	}

	requests := result.DeploymentPlan.VM.Resources.Requests
	if requests["cpu_cores"] != "8" {
		t.Fatalf("expected 8 CPU cores, got %s", requests["cpu_cores"])
	}
	if requests["memory_gb"] != "32" {
		t.Fatalf("expected 32GB memory request, got %s", requests["memory_gb"])
	}
}

func TestRunServiceOperationsCombinesCoreDecisionsInGo(t *testing.T) {
	service := NewService(NewServerConfig())

	report, err := service.RunServiceOperations(context.Background(), ServiceOperationsRequest{
		LLMPolicy:         "quality_first",
		Workload:          "llm-chat-inference",
		OperationService:  "llm-chat-inference",
		OperationResource: "gpu-vm-l4",
		Mode:              "mock",
		GuardBackend:      "go",
		LLMConfigPath:     "config/ops_llm_benchmark.json",
		InferenceConfig:   "config/inference_optimization.json",
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
	if report.SelectedActualModel != "to-be-evaluated-primary-model" {
		t.Fatalf("expected selected actual model placeholder, got %s", report.SelectedActualModel)
	}
	if report.BenchmarkStatus != "not_executed" {
		t.Fatalf("expected benchmark status not_executed, got %s", report.BenchmarkStatus)
	}
	if report.SelectedProvider != "prototype-policy" {
		t.Fatalf("expected selected provider prototype-policy, got %s", report.SelectedProvider)
	}
	if report.SelectedResource != "gpu-vm-l4" {
		t.Fatalf("expected gpu-vm-l4, got %s", report.SelectedResource)
	}
	if !report.OperationPipelineReady {
		t.Fatal("expected operation pipeline readiness")
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
	if !report.AgentReviews.Cost.Approved {
		t.Fatalf("expected cost review approval: %#v", report.AgentReviews.Cost)
	}
}
