package agentcontrol

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestAutomationRunnerCompletesNaturalLanguageFlow(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType:   InputTypeNaturalLanguage,
		Request:     "Qwen 추론 서비스를 CPU 4코어, 메모리 16GiB, GPU 1개, VRAM 16GiB, 스토리지 20GiB로 배포해 주세요.",
		RequestedBy: "test",
	})
	if err != nil {
		t.Fatalf("run automation: %v", err)
	}

	if run.Status != AutomationRunStatusCompleted {
		t.Fatalf("status = %q, want %q", run.Status, AutomationRunStatusCompleted)
	}
	if run.RequirementAnalysis == nil || run.ResourceRecommendation == nil || run.Flow == nil {
		t.Fatalf("automation stages are incomplete: %#v", run)
	}
	if run.Flow.Decision == nil || run.Flow.Decision.Action != ActionDeploy {
		t.Fatalf("decision = %#v, want DEPLOY", run.Flow.Decision)
	}
	if run.DesiredDeploymentSpec == nil {
		t.Fatal("approved run did not return DesiredDeploymentSpec")
	}
	if run.RequirementAnalysis.Mode != AnalysisModeLocalRule {
		t.Fatalf("analysis mode = %q", run.RequirementAnalysis.Mode)
	}
}

func TestAutomationRunnerCompletesStructuredFlow(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "cpu-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run structured automation: %v", err)
	}
	if run.Flow == nil || run.Flow.Decision == nil || run.Flow.Decision.Action != ActionDeploy {
		t.Fatalf("structured decision = %#v", run.Flow)
	}
	if run.RequirementAnalysis.Mode != AnalysisModeStructured {
		t.Fatalf("analysis mode = %q, want structured", run.RequirementAnalysis.Mode)
	}
}

func TestAutomationRunnerReturnsRejectForInvalidRequirements(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "invalid-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 3,
			ReplicasMax: 1,
		},
	})
	if err != nil {
		t.Fatalf("run invalid automation: %v", err)
	}
	if run.Flow == nil || run.Flow.Decision == nil || run.Flow.Decision.Action != ActionReject {
		t.Fatalf("invalid requirements decision = %#v, want REJECT", run.Flow)
	}
	if run.DesiredDeploymentSpec != nil {
		t.Fatal("REJECT run must not return DesiredDeploymentSpec")
	}
}

func TestAutomationRunnerReturnsRetryWhenCatalogCannotSatisfyProfile(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:                "oversized-service",
			CPUCores:             32,
			MemoryMiB:            131072,
			StorageGiB:           1000,
			AcceleratorType:      "GPU",
			AcceleratorCount:     4,
			AcceleratorMemoryMiB: 98304,
			ReplicasMin:          1,
			ReplicasMax:          2,
		},
	})
	if err != nil {
		t.Fatalf("run oversized automation: %v", err)
	}
	if run.Flow == nil || run.Flow.Decision == nil || run.Flow.Decision.Action != ActionRetry {
		t.Fatalf("oversized decision = %#v, want RETRY", run.Flow)
	}
	if run.DesiredDeploymentSpec != nil {
		t.Fatal("RETRY run must not return DesiredDeploymentSpec")
	}
}

func TestAutomationRunnerPreservesGeneratedTraceIdentifiers(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "trace-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run automation: %v", err)
	}
	if run.RunID != "run-test" || run.CorrelationID != "flow-test" || run.TraceID != "trace-test" {
		t.Fatalf("run identifiers = %#v", run)
	}
	application := run.Flow.ApplicationContext
	recommendation := run.Flow.ResourceRecommendation
	if application == nil || recommendation == nil ||
		application.CorrelationID != run.CorrelationID ||
		recommendation.CorrelationID != run.CorrelationID ||
		application.TraceID != run.TraceID ||
		recommendation.TraceID != run.TraceID {
		t.Fatalf("stage identifiers are inconsistent: %#v", run)
	}
}

func TestAutomationRunnerStopsWhenAgentRegistryRejectsAuthorization(t *testing.T) {
	service := NewServiceWithDependencies(
		nil,
		stubAuthorizer{
			result: AgentAuthorization{
				Authorized: false,
				Reason:     "Agent is disabled",
			},
		},
	)
	runner := testAutomationRunner(t, service)
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "unauthorized-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run unauthorized automation: %v", err)
	}
	if run.Flow == nil || run.Flow.State != StateAgentAuthorizationRejected {
		t.Fatalf("authorization state = %#v", run.Flow)
	}
	if run.Flow.Decision != nil || run.DesiredDeploymentSpec != nil {
		t.Fatalf("unauthorized run created output: %#v", run)
	}
}

func TestAutomationRunnerStoresRunForLookup(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	created, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "stored-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run automation: %v", err)
	}

	stored, ok := runner.Get(created.RunID)
	if !ok {
		t.Fatalf("run %q was not stored", created.RunID)
	}
	if stored.CorrelationID != created.CorrelationID {
		t.Fatalf("stored run = %#v, created = %#v", stored, created)
	}
}

func TestAutomationRunnerRunsProtocolAnalysisRequestOnce(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	request := testApplicationAnalysisRequest()

	first, replayed, err := runner.RunAnalysisRequest(context.Background(), request)
	if err != nil {
		t.Fatalf("run protocol request: %v", err)
	}
	if replayed {
		t.Fatal("first protocol request was reported as a replay")
	}
	if first.CorrelationID != request.CorrelationID || first.TraceID != request.TraceID {
		t.Fatalf("protocol identifiers were not preserved: %#v", first)
	}
	if first.Flow == nil || first.Flow.ApplicationContext == nil {
		t.Fatalf("protocol run did not create an Application Context: %#v", first)
	}
	if first.Flow.ApplicationContext.CausationID != request.MessageID {
		t.Fatalf(
			"application context causation_id = %q, want %q",
			first.Flow.ApplicationContext.CausationID,
			request.MessageID,
		)
	}
	profile := first.RequirementAnalysis.ApplicationProfile
	if profile.AppID != "chat-service" ||
		profile.AppVersion != "1.0.0" ||
		profile.Artifact == nil ||
		profile.Artifact.URI != "docker://registry.example.org/chat-service:1.0.0" ||
		profile.Workload.ExpectedRPS != 5 {
		t.Fatalf("protocol application metadata was not preserved: %#v", profile)
	}
	if first.Flow.DeploymentRequest == nil {
		t.Fatalf("approved protocol run has no deployment request: %#v", first.Flow)
	}
	firstRequestID := first.Flow.DeploymentRequest.Data.DeploymentRequest.RequestID

	second, replayed, err := runner.RunAnalysisRequest(context.Background(), request)
	if err != nil {
		t.Fatalf("replay protocol request: %v", err)
	}
	if !replayed {
		t.Fatal("second protocol request was not reported as a replay")
	}
	if second.RunID != first.RunID {
		t.Fatalf("replay run_id = %q, want %q", second.RunID, first.RunID)
	}
	if second.Flow.DeploymentRequest.Data.DeploymentRequest.RequestID != firstRequestID {
		t.Fatalf(
			"replay deployment request_id = %q, want %q",
			second.Flow.DeploymentRequest.Data.DeploymentRequest.RequestID,
			firstRequestID,
		)
	}
	if got := len(runner.List()); got != 1 {
		t.Fatalf("stored run count = %d, want 1", got)
	}
}

func TestAutomationRunnerRejectsChangedPayloadForExistingMessageID(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	request := testApplicationAnalysisRequest()
	if _, _, err := runner.RunAnalysisRequest(context.Background(), request); err != nil {
		t.Fatalf("run protocol request: %v", err)
	}

	changed := request
	changed.Data.Application.UserRequest = "Deploy a different application."
	_, _, err := runner.RunAnalysisRequest(context.Background(), changed)
	if !errors.Is(err, ErrAnalysisRequestIdempotencyConflict) {
		t.Fatalf("changed replay error = %v, want idempotency conflict", err)
	}
}

func TestAutomationRunnerSubmitsApprovedRequestOnce(t *testing.T) {
	adapter := &recordingDeploymentAdapter{
		result: DeploymentSubmission{
			Adapter:     DeploymentAdapterMock,
			Status:      DeploymentSubmissionSimulated,
			Simulated:   true,
			RequestID:   "deploy-request-test",
			SubmittedAt: "2026-07-30T02:00:00Z",
		},
	}
	runner := testAutomationRunnerWithAdapter(t, NewService(), adapter)

	run, err := runner.Run(context.Background(), approvedAutomationInput())
	if err != nil {
		t.Fatalf("run approved automation: %v", err)
	}
	if adapter.calls != 1 {
		t.Fatalf("adapter calls = %d, want 1", adapter.calls)
	}
	if run.DeploymentSubmission == nil {
		t.Fatalf("approved run has no deployment submission: %#v", run)
	}
	if run.DeploymentSubmission.Status != DeploymentSubmissionSimulated {
		t.Fatalf("submission = %#v", run.DeploymentSubmission)
	}
	if len(adapter.requests) != 1 ||
		adapter.requests[0].Data.DeploymentRequest.RequestID == "" {
		t.Fatalf("adapter requests = %#v", adapter.requests)
	}
}

func TestAutomationRunnerDoesNotSubmitRejectOrRetry(t *testing.T) {
	tests := []struct {
		name  string
		input AutomationRunInput
	}{
		{
			name: "reject",
			input: AutomationRunInput{
				InputType: InputTypeStructured,
				AppSpec: &StructuredAppSpec{
					AppID:       "invalid-service",
					CPUCores:    2,
					MemoryMiB:   4096,
					StorageGiB:  20,
					ReplicasMin: 3,
					ReplicasMax: 1,
				},
			},
		},
		{
			name: "retry",
			input: AutomationRunInput{
				InputType: InputTypeStructured,
				AppSpec: &StructuredAppSpec{
					AppID:                "oversized-service",
					CPUCores:             32,
					MemoryMiB:            131072,
					StorageGiB:           1000,
					AcceleratorType:      "GPU",
					AcceleratorCount:     4,
					AcceleratorMemoryMiB: 98304,
					ReplicasMin:          1,
					ReplicasMax:          2,
				},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			adapter := &recordingDeploymentAdapter{}
			runner := testAutomationRunnerWithAdapter(t, NewService(), adapter)

			run, err := runner.Run(context.Background(), test.input)
			if err != nil {
				t.Fatalf("run %s automation: %v", test.name, err)
			}
			if adapter.calls != 0 {
				t.Fatalf("adapter calls = %d, want 0", adapter.calls)
			}
			if run.DeploymentSubmission != nil {
				t.Fatalf("%s run has submission: %#v", test.name, run.DeploymentSubmission)
			}
		})
	}
}

func TestAutomationRunnerDoesNotResubmitIdempotentReplay(t *testing.T) {
	adapter := &recordingDeploymentAdapter{
		result: DeploymentSubmission{
			Adapter:   DeploymentAdapterMock,
			Status:    DeploymentSubmissionSimulated,
			Simulated: true,
		},
	}
	runner := testAutomationRunnerWithAdapter(t, NewService(), adapter)
	request := testApplicationAnalysisRequest()

	first, replayed, err := runner.RunAnalysisRequest(context.Background(), request)
	if err != nil || replayed {
		t.Fatalf("first request: replayed=%v err=%v", replayed, err)
	}
	second, replayed, err := runner.RunAnalysisRequest(context.Background(), request)
	if err != nil || !replayed {
		t.Fatalf("replayed request: replayed=%v err=%v", replayed, err)
	}
	if adapter.calls != 1 {
		t.Fatalf("adapter calls = %d, want 1", adapter.calls)
	}
	if first.RunID != second.RunID {
		t.Fatalf("replay run_id = %q, want %q", second.RunID, first.RunID)
	}
}

func TestAutomationRunnerRecordsSanitizedAdapterFailure(t *testing.T) {
	adapter := &recordingDeploymentAdapter{
		err: errors.New("upstream secret token was rejected"),
	}
	runner := testAutomationRunnerWithAdapter(t, NewService(), adapter)

	run, err := runner.Run(context.Background(), approvedAutomationInput())
	if err == nil {
		t.Fatal("adapter failure was not returned")
	}
	if run.Status != AutomationRunStatusFailed {
		t.Fatalf("status = %q, want %q", run.Status, AutomationRunStatusFailed)
	}
	if run.ErrorCode != "DEPLOYMENT_ADAPTER_FAILED" {
		t.Fatalf("error_code = %q", run.ErrorCode)
	}
	if strings.Contains(run.ErrorMessage, "secret") ||
		strings.Contains(run.ErrorMessage, "token") {
		t.Fatalf("run exposed adapter error: %q", run.ErrorMessage)
	}
	if run.DeploymentSubmission == nil ||
		run.DeploymentSubmission.Status != DeploymentSubmissionFailed {
		t.Fatalf("failure submission = %#v", run.DeploymentSubmission)
	}
	if strings.Contains(run.DeploymentSubmission.ErrorMessage, "secret") ||
		strings.Contains(run.DeploymentSubmission.ErrorMessage, "token") {
		t.Fatalf(
			"submission exposed adapter error: %q",
			run.DeploymentSubmission.ErrorMessage,
		)
	}
	stored, ok := runner.Get(run.RunID)
	if !ok || stored.ErrorCode != run.ErrorCode {
		t.Fatalf("failed run was not stored: %#v", stored)
	}
}

func testApplicationAnalysisRequest() ApplicationAnalysisRequestEnvelope {
	return ApplicationAnalysisRequestEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-analysis-001",
			MessageType:     MessageApplicationAnalysisRequest,
			OccurredAt:      "2026-07-30T01:00:00Z",
			CorrelationID:   "flow-protocol-001",
			TraceID:         "trace-protocol-001",
			Source: Endpoint{
				System:    "khu-ai-app",
				Component: "application-request-api",
			},
			Target: Endpoint{
				System:    "khu-agent-control",
				Component: "requirement-analyzer",
			},
		},
		Data: ApplicationAnalysisRequestData{
			Application: AnalysisRequestApplication{
				AppID:      "chat-service",
				AppVersion: "1.0.0",
				Artifact: Artifact{
					Type:       "OCI_IMAGE",
					URI:        "docker://registry.example.org/chat-service:1.0.0",
					Entrypoint: []string{"/opt/app/start-server"},
				},
				UserRequest: "GPU 1, CPU 8 cores, memory 32GiB, storage 100GiB, p95 latency 2 seconds.",
				DeclaredSpec: DeclaredApplicationSpec{
					ExpectedRPS:    5,
					MaxInputTokens: 4096,
				},
				Labels: map[string]string{"project": "ai-mcmp"},
			},
		},
	}
}

func testAutomationRunner(t *testing.T, service *Service) *AutomationRunner {
	t.Helper()
	return testAutomationRunnerWithAdapter(
		t,
		service,
		MockDeploymentAdapter{
			Now: func() time.Time {
				return time.Date(2026, 7, 29, 8, 0, 0, 0, time.UTC)
			},
		},
	)
}

func testAutomationRunnerWithAdapter(
	t *testing.T,
	service *Service,
	adapter DeploymentAdapter,
) *AutomationRunner {
	t.Helper()
	runner := NewAutomationRunnerWithAdapter(
		LocalRequirementAnalyzer{},
		CatalogResourceRecommender{
			Catalog: testResourceCatalog(),
			Now: func() time.Time {
				return time.Date(2026, 7, 29, 8, 0, 0, 0, time.UTC)
			},
		},
		service,
		adapter,
	)
	runner.Now = func() time.Time {
		return time.Date(2026, 7, 29, 8, 0, 0, 0, time.UTC)
	}
	runner.IDGenerator = func(prefix string) string {
		return prefix + "-test"
	}
	return runner
}

func approvedAutomationInput() AutomationRunInput {
	return AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "approved-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	}
}

type recordingDeploymentAdapter struct {
	calls    int
	requests []DeploymentCreateRequestEnvelope
	result   DeploymentSubmission
	err      error
}

func (adapter *recordingDeploymentAdapter) Submit(
	_ context.Context,
	request DeploymentCreateRequestEnvelope,
) (DeploymentSubmission, error) {
	adapter.calls++
	adapter.requests = append(adapter.requests, request)
	result := adapter.result
	if result.RequestID == "" {
		result.RequestID = request.Data.DeploymentRequest.RequestID
	}
	return result, adapter.err
}
