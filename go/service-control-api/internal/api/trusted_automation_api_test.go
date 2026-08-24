package api

import (
	"context"
	"net/http"
	"strings"
	"testing"

	playgroundvalidator "github.com/go-playground/validator/v10"
	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/trustedorchestration"
)

func TestTrustedAutomationRunAPIRoutesThroughSafeguardBeforeGeon(t *testing.T) {
	fake := &recordingTrustedFlowOrchestrator{result: trustedorchestration.Result{
		Status: trustedorchestration.StatusApprovedFlowReady,
		Safeguard: llmop.SafeguardStageResult{
			Status:   llmop.StatusSafeguardApproved,
			Approved: true,
		},
		AutomationRun: &agentcontrol.AutomationRun{
			RunID:  "run-trusted-api-001",
			Status: agentcontrol.AutomationRunStatusCompleted,
		},
	}}
	server := echo.New()
	server.Validator = requestValidator{validator: playgroundvalidator.New()}
	handler := restHandler{service: Service{trustedOrchestration: fake}}
	server.POST(
		pathAgentControl+"/trusted-automation-runs",
		handler.RestPostTrustedAutomationRun,
	)

	response := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/trusted-automation-runs",
		`{
			"app_version_id":"appver-geon-poc-001",
			"candidate_id":"qwen3.5-ops-planner",
			"input":{
				"input_type":"natural_language",
				"request":"GPU 1개, CPU 4코어, 메모리 8GiB로 배포해 주세요.",
				"requested_by":"geon-web",
				"decision_agent":"AIApplicationAutomationAgent"
			}
		}`,
	)
	if response.Code != http.StatusCreated {
		t.Fatalf("trusted automation run: code=%d body=%s", response.Code, response.Body.String())
	}
	if fake.calls != 1 {
		t.Fatalf("trusted orchestrator calls = %d, want 1", fake.calls)
	}
	input := fake.input
	if input.Request.Application.UserRequest != input.AnalysisRequest.Data.Application.UserRequest {
		t.Fatal("Safeguard and geon requests must contain the exact same user request")
	}
	if input.Request.CorrelationID == "" ||
		input.Request.CorrelationID != input.AnalysisRequest.CorrelationID ||
		input.Request.TraceID != input.AnalysisRequest.TraceID {
		t.Fatalf("trusted identifiers are not joined: %#v", input)
	}
	if input.AnalysisRequest.Data.RequestedDecisionAgent != "AIApplicationAutomationAgent" {
		t.Fatalf("decision Agent was not preserved: %#v", input.AnalysisRequest.Data)
	}
}

func TestStructuredTrustedAutomationUsesBoundedSafeguardSummary(t *testing.T) {
	input, err := buildTrustedOrchestrationInput(TrustedAutomationRunRequest{
		AppVersionID: "appver-structured-001",
		CandidateID:  "qwen3.5-ops-planner",
		Input: agentcontrol.AutomationRunInput{
			InputType:     agentcontrol.InputTypeStructured,
			RequestedBy:   "geon-web",
			DecisionAgent: "AIApplicationAutomationAgent",
			AppSpec: &agentcontrol.StructuredAppSpec{
				AppID:            "structured-ai-app",
				AppVersion:       "1.0.0",
				CPUCores:         4,
				MemoryMiB:        8192,
				StorageGiB:       20,
				AcceleratorType:  "GPU",
				AcceleratorCount: 1,
			},
		},
	})
	if err != nil {
		t.Fatalf("build structured trusted input: %v", err)
	}
	if len(input.Request.Application.Parameters) != 0 {
		t.Fatalf("raw structured App Spec reached Safeguard parameters: %#v", input.Request.Application.Parameters)
	}
	for _, expected := range []string{"CPU 4", "memory 8192 MiB", "GPU 1", "storage 20 GiB"} {
		if !strings.Contains(input.Request.Application.UserRequest, expected) {
			t.Fatalf("Safeguard summary omitted %q: %s", expected, input.Request.Application.UserRequest)
		}
	}
	if input.Request.Application.UserRequest != input.AnalysisRequest.Data.Application.UserRequest {
		t.Fatal("structured Safeguard and geon requests must stay identity-bound")
	}
	if input.AnalysisRequest.Data.StructuredAppSpec == nil ||
		input.AnalysisRequest.Data.StructuredAppSpec.AppID != "structured-ai-app" {
		t.Fatalf("geon did not retain the original structured App Spec: %#v", input.AnalysisRequest.Data.StructuredAppSpec)
	}
}

func TestTrustedAutomationRunnerAlwaysUsesMockAdapter(t *testing.T) {
	config := NewServerConfig()
	config.DeploymentAdapterMode = agentcontrol.DeploymentAdapterHandoff
	service := NewService(config)
	run, err := service.trustedAutomationRunner.Run(context.Background(), agentcontrol.AutomationRunInput{
		InputType:   agentcontrol.InputTypeStructured,
		RequestedBy: "geon-web/trusted-request-api",
		AppSpec: &agentcontrol.StructuredAppSpec{
			AppID:            "trusted-mock-app",
			AppVersion:       "1.0.0",
			CPUCores:         4,
			MemoryMiB:        8192,
			StorageGiB:       20,
			AcceleratorType:  "GPU",
			AcceleratorCount: 1,
		},
	})
	if err != nil {
		t.Fatalf("run trusted Mock flow: %v", err)
	}
	if run.DeploymentSubmission.Adapter != agentcontrol.DeploymentAdapterMock ||
		!run.DeploymentSubmission.Simulated ||
		run.DeploymentSubmission.Status != agentcontrol.DeploymentSubmissionSimulated {
		t.Fatalf("trusted flow escaped the Mock Adapter boundary: %#v", run.DeploymentSubmission)
	}
}

func TestTrustedAutomationRunAPIRejectsMissingTrustBinding(t *testing.T) {
	fake := &recordingTrustedFlowOrchestrator{}
	server := echo.New()
	server.Validator = requestValidator{validator: playgroundvalidator.New()}
	handler := restHandler{service: Service{trustedOrchestration: fake}}
	server.POST(
		pathAgentControl+"/trusted-automation-runs",
		handler.RestPostTrustedAutomationRun,
	)

	response := performJSONRequest(
		t,
		server,
		http.MethodPost,
		"/api/v1/agent-control/trusted-automation-runs",
		`{"input":{"input_type":"natural_language","request":"배포해 주세요."}}`,
	)
	if response.Code != http.StatusBadRequest || fake.calls != 0 {
		t.Fatalf("missing trust binding: code=%d calls=%d body=%s", response.Code, fake.calls, response.Body.String())
	}
}

type recordingTrustedFlowOrchestrator struct {
	input  trustedorchestration.Input
	result trustedorchestration.Result
	err    error
	calls  int
}

func (orchestrator *recordingTrustedFlowOrchestrator) RunApprovedFlow(
	_ context.Context,
	input trustedorchestration.Input,
) (trustedorchestration.Result, error) {
	orchestrator.calls++
	orchestrator.input = input
	return orchestrator.result, orchestrator.err
}
