package main

import (
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/llmop"
)

func TestResolveDemoBindingPinsExactOfflineCandidateAndService(t *testing.T) {
	catalog := validDemoCatalog()
	request := demoCatalogRequest()

	candidate, service, err := resolveDemoBinding(catalog, "svc-demo", request)
	if err != nil {
		t.Fatalf("resolve demo binding: %v", err)
	}
	if candidate.CandidateID != request.CandidateID ||
		candidate.Provider != "offline-fixture" ||
		candidate.ActualModel != "fixture-qwen-contract-not-executed" ||
		candidate.Endpoint != "" || candidate.APIKeyEnv != "" ||
		service.AppVersionID != request.Application.AppVersionID {
		t.Fatalf("unexpected offline binding: %#v, %#v", candidate, service)
	}
}

func TestResolveDemoBindingRejectsNetworkOrServiceMismatch(t *testing.T) {
	request := demoCatalogRequest()
	catalog := validDemoCatalog()
	catalog.ExecutionPolicy.NetworkAllowed = true
	if _, _, err := resolveDemoBinding(catalog, "svc-demo", request); err == nil {
		t.Fatal("network-enabled demo policy must be rejected")
	}

	catalog = validDemoCatalog()
	request.Application.AppVersionID = "appver-other"
	if _, _, err := resolveDemoBinding(catalog, "svc-demo", request); err == nil ||
		!strings.Contains(err.Error(), "does not match") {
		t.Fatalf("service/application mismatch must be rejected: %v", err)
	}
}

func validDemoCatalog() demoCatalog {
	return demoCatalog{
		SchemaVersion: demoCatalogVersion,
		ExecutionPolicy: demoExecutionPolicy{Mode: "offline_fixture_only"},
		PlannerModelBindings: []demoPlannerModelBinding{{
			CandidateID:             "qwen3.5-ops-planner",
			BindingMode:             "caller_pinned",
			Provider:                "offline-fixture",
			ActualModel:             "fixture-qwen-contract-not-executed",
			IntendedModel:           "qwen3.5:4b",
			InitialPreferenceWeight: 1,
			WeightSemantics:         "metadata_only_no_selection",
			JSONMode:                true,
			BenchmarkStatus:         "not_executed",
		}},
		AIServices: []demoAIService{{
			ServiceID:        "svc-demo",
			DisplayName:      "Demo service",
			Task:             "chat_completion",
			Description:      "Offline fixture service",
			AppVersionID:     "appver-demo",
			WorkloadModelRef: "registered/demo-model",
			LifecycleStatus:  "demo_catalog_only",
			InputMediaType:   "application/json",
			OutputMediaType:  "application/json",
			DefaultResources: demoResourcePlan{
				CPU: "4", Memory: "8Gi", GPU: "0", Storage: "20Gi", Accelerator: "none",
			},
		}},
	}
}

func demoCatalogRequest() llmop.Request {
	return llmop.Request{
		CandidateID: "qwen3.5-ops-planner",
		Application: llmop.Application{AppVersionID: "appver-demo"},
	}
}
