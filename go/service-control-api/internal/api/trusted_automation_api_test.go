package api

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	playgroundvalidator "github.com/go-playground/validator/v10"
	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/audittrail"
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
	auditRoot := t.TempDir()
	handler := restHandler{service: Service{
		trustedOrchestration: fake,
		trustedAuditStore:    audittrail.NewFileStore(auditRoot),
		trustedAuditRequired: true,
	}}
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
			"app_version_id":"app-version-must-not-persist",
			"candidate_id":"candidate-must-not-persist",
			"input":{
				"input_type":"natural_language",
				"request":"raw-audit-request-must-not-persist GPU 1개, CPU 4코어, 메모리 8GiB로 배포해 주세요.",
				"requested_by":"caller-must-not-persist",
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
	var payload TrustedAutomationRunResponse
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode trusted response: %v", err)
	}
	if payload.Audit.PersistenceStatus != audittrail.PersistenceComplete ||
		!payload.Audit.Complete ||
		payload.Audit.AuditID == "" {
		t.Fatalf("audit reference = %#v", payload.Audit)
	}
	for _, relativePath := range []string{payload.Audit.EventsPath, payload.Audit.SummaryPath} {
		contents, err := os.ReadFile(filepath.Join(auditRoot, filepath.FromSlash(relativePath)))
		if err != nil {
			t.Fatalf("read audit artifact %s: %v", relativePath, err)
		}
		for _, forbidden := range []string{
			"raw-audit-request-must-not-persist",
			"app-version-must-not-persist",
			"candidate-must-not-persist",
			"caller-must-not-persist",
		} {
			if strings.Contains(string(contents), forbidden) {
				t.Fatalf("forbidden raw value %q leaked into %s", forbidden, relativePath)
			}
		}
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
	var payload TrustedAutomationRunErrorResponse
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode trusted binding error: %v", err)
	}
	if payload.ErrorCode != "TRUSTED_AUTOMATION_FAILED" ||
		payload.Result.Audit.PersistenceStatus != audittrail.PersistenceDegraded {
		t.Fatalf("trusted binding error = %#v", payload)
	}
}

func TestTrustedAutomationRunAPIFailsClosedWhenAuditCannotStart(t *testing.T) {
	fake := &recordingTrustedFlowOrchestrator{}
	rootFile := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(rootFile, []byte("blocked"), 0o600); err != nil {
		t.Fatalf("write blocked audit root: %v", err)
	}
	server := echo.New()
	server.Validator = requestValidator{validator: playgroundvalidator.New()}
	handler := restHandler{service: Service{
		trustedOrchestration: fake,
		trustedAuditStore:    audittrail.NewFileStore(rootFile),
		trustedAuditRequired: true,
	}}
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
			"app_version_id":"appver-audit-001",
			"candidate_id":"qwen3.5-ops-planner",
			"input":{
				"input_type":"natural_language",
				"request":"CPU 4 cores, memory 8 GiB, GPU 0, storage 20 GiB"
			}
		}`,
	)
	if response.Code != http.StatusInternalServerError || fake.calls != 0 {
		t.Fatalf("audit fail-closed: code=%d calls=%d body=%s", response.Code, fake.calls, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), `"error_code":"AUDIT_PERSISTENCE_FAILED"`) {
		t.Fatalf("safe audit error code is missing: %s", response.Body.String())
	}
}

func TestTrustedAutomationRunAPIMarksBestEffortAuditAsDegraded(t *testing.T) {
	fake := &recordingTrustedFlowOrchestrator{result: trustedorchestration.Result{
		Status: trustedorchestration.StatusApprovedFlowReady,
		Safeguard: llmop.SafeguardStageResult{
			Status:   llmop.StatusSafeguardApproved,
			Approved: true,
		},
	}}
	rootFile := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(rootFile, []byte("blocked"), 0o600); err != nil {
		t.Fatalf("write blocked audit root: %v", err)
	}
	server := echo.New()
	server.Validator = requestValidator{validator: playgroundvalidator.New()}
	handler := restHandler{service: Service{
		trustedOrchestration: fake,
		trustedAuditStore:    audittrail.NewFileStore(rootFile),
		trustedAuditRequired: false,
	}}
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
			"app_version_id":"appver-audit-002",
			"candidate_id":"qwen3.5-ops-planner",
			"input":{
				"input_type":"natural_language",
				"request":"CPU 4 cores, memory 8 GiB, GPU 0, storage 20 GiB"
			}
		}`,
	)
	if response.Code != http.StatusCreated || fake.calls != 1 {
		t.Fatalf("audit best effort: code=%d calls=%d body=%s", response.Code, fake.calls, response.Body.String())
	}
	var payload TrustedAutomationRunResponse
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode degraded response: %v", err)
	}
	if payload.Audit.PersistenceStatus != audittrail.PersistenceDegraded || payload.Audit.Complete {
		t.Fatalf("degraded audit reference = %#v", payload.Audit)
	}
}

func TestGetAutomationRunFallsBackToTrustedRunner(t *testing.T) {
	runner := agentcontrol.NewAutomationRunner(
		agentcontrol.LocalRequirementAnalyzer{},
		agentcontrol.CatalogResourceRecommender{Catalog: agentcontrol.ResourceCatalog{
			Version: "audit-test-v1",
			Candidates: []agentcontrol.CatalogResource{{
				CandidateID:       "audit-cpu-small",
				CPUCores:          4,
				MemoryMiB:         8192,
				StorageGiB:        20,
				CostPerHour:       0.1,
				AvailabilityScore: 1,
			}},
		}},
		agentcontrol.NewService(),
	)
	run, err := runner.Run(context.Background(), agentcontrol.AutomationRunInput{
		InputType:   agentcontrol.InputTypeStructured,
		RequestedBy: "trusted-test",
		AppSpec: &agentcontrol.StructuredAppSpec{
			AppID:      "audit-app",
			AppVersion: "1.0.0",
			CPUCores:   4,
			MemoryMiB:  8192,
			StorageGiB: 20,
		},
	})
	if err != nil {
		t.Fatalf("create trusted run: %v", err)
	}
	server := echo.New()
	handler := restHandler{service: Service{trustedAutomationRunner: runner}}
	server.GET(
		pathAgentControl+"/automation-runs/:run_id",
		handler.RestGetAutomationRun,
	)
	response := performJSONRequest(
		t,
		server,
		http.MethodGet,
		"/api/v1/agent-control/automation-runs/"+run.RunID,
		"",
	)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), run.RunID) {
		t.Fatalf("get trusted run: code=%d body=%s", response.Code, response.Body.String())
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
