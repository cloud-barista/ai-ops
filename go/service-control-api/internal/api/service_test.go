package api

import "testing"

func TestSelectOpsLLMMatchesConfiguredBaseline(t *testing.T) {
	service := NewService(NewServerConfig())

	result, err := service.SelectOpsLLM("quality_first")
	if err != nil {
		t.Fatalf("SelectOpsLLM returned error: %v", err)
	}

	if result.SelectedModel != "gpt-5.5" {
		t.Fatalf("expected gpt-5.5, got %s", result.SelectedModel)
	}
	if result.SelectedScore != 0.891333 {
		t.Fatalf("expected score 0.891333, got %.6f", result.SelectedScore)
	}
	if len(result.Ranking) != 3 {
		t.Fatalf("expected 3 ranked models, got %d", len(result.Ranking))
	}
	if result.Ranking[1].Model != "gpt-4o-mini" {
		t.Fatalf("expected gpt-4o-mini second, got %s", result.Ranking[1].Model)
	}
}

func TestValidateAgentActionUsesRegistryBounds(t *testing.T) {
	service := NewService(NewServerConfig())

	valid, err := service.ValidateAgentAction(
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

	result, err := service.RecommendPlacement("llm-chat-inference")
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

	result, err := service.BuildDeploymentPlan("text-classifier")
	if err != nil {
		t.Fatalf("BuildDeploymentPlan returned error: %v", err)
	}

	requests := result.DeploymentPlan.Kubernetes.Resources.Requests
	if requests["cpu"] != "8" {
		t.Fatalf("expected 8 CPU request, got %s", requests["cpu"])
	}
	if requests["memory"] != "32Gi" {
		t.Fatalf("expected 32Gi memory request, got %s", requests["memory"])
	}
}

func TestRunServiceOperationsCombinesCoreDecisionsInGo(t *testing.T) {
	service := NewService(NewServerConfig())

	report, err := service.RunServiceOperations(ServiceOperationsRequest{
		LLMPolicy:       "quality_first",
		Workload:        "llm-chat-inference",
		Namespace:       "online-boutique",
		Deployment:      "paymentservice",
		Mode:            "mock",
		GuardBackend:    "go",
		LLMConfigPath:   "config/ops_llm_benchmark.json",
		InferenceConfig: "config/inference_optimization.json",
	})
	if err != nil {
		t.Fatalf("RunServiceOperations returned error: %v", err)
	}

	if !report.Valid {
		t.Fatalf("expected report to be valid: %#v", report)
	}
	if report.SelectedLLM != "gpt-5.5" {
		t.Fatalf("expected gpt-5.5, got %s", report.SelectedLLM)
	}
	if report.SelectedResource != "gpu-vm-l4" {
		t.Fatalf("expected gpu-vm-l4, got %s", report.SelectedResource)
	}
	if !report.RecoveryPipelineReady {
		t.Fatal("expected recovery pipeline readiness")
	}
	if report.GuardBackend != "go" {
		t.Fatalf("expected guard backend go, got %s", report.GuardBackend)
	}
	if report.DeploymentManifest.Kind != "Deployment" {
		t.Fatalf("expected Deployment manifest, got %s", report.DeploymentManifest.Kind)
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
