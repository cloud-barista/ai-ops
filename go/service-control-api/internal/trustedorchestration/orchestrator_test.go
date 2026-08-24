package trustedorchestration

import (
	"context"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/llmopbridge"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

func TestOrchestratorRunsApprovedRequestThroughGeon(t *testing.T) {
	input := approvedTestInput()
	reviewer := &recordingReviewer{result: approvedSafeguard(input.Request)}
	runner := integrationAutomationRunner()
	resolverCalls := 0
	resolver := func(appID string, appVersion string) (llmopbridge.TrustedAppVersionBinding, error) {
		resolverCalls++
		return llmopbridge.TrustedAppVersionBinding{
			AppID:        appID,
			AppVersion:   appVersion,
			AppVersionID: input.Request.Application.AppVersionID,
		}, nil
	}
	orchestrator := New(reviewer, runner, resolver)

	result, err := orchestrator.Run(context.Background(), input)
	if err != nil {
		t.Fatalf("run trusted orchestration: %v", err)
	}
	if result.Status != StatusApprovedFlowReady {
		t.Fatalf("status = %q, want %q", result.Status, StatusApprovedFlowReady)
	}
	if reviewer.calls != 1 || resolverCalls != 1 {
		t.Fatalf("reviewer/resolver calls = %d/%d, want 1/1", reviewer.calls, resolverCalls)
	}
	if result.AutomationRun == nil || result.AutomationRun.Flow == nil {
		t.Fatal("approved orchestration did not retain the geon automation run")
	}
	if result.AutomationRun.CorrelationID != input.Request.CorrelationID ||
		result.AutomationRun.TraceID != input.Request.TraceID {
		t.Fatalf("geon identifiers drifted: %#v", result.AutomationRun)
	}
	if result.ApprovedProjection == nil {
		t.Fatal("approved orchestration did not produce the approved-flow projection")
	}
	projection := result.ApprovedProjection
	if projection.Evidence.Revision != 1 ||
		projection.Evidence.Phase != agentcontrol.ManifestPhaseInitial ||
		projection.Evidence.TriggerAction != agentcontrol.ActionDeploy {
		t.Fatalf("unexpected approved revision evidence: %#v", projection.Evidence)
	}
	if projection.Request.Application.UserRequest != input.Request.Application.UserRequest ||
		projection.Request.Application.AppVersionID != input.Request.Application.AppVersionID {
		t.Fatalf("trusted request binding changed: %#v", projection.Request.Application)
	}
}

func TestOrchestratorStopsNonApprovedSafeguardBeforeGeon(t *testing.T) {
	tests := []struct {
		name   string
		status string
		action string
	}{
		{name: "clarification", status: llmop.StatusClarificationNeeded, action: llmop.SafeguardDecisionClarify},
		{name: "rejection", status: llmop.StatusRequestRejected, action: llmop.SafeguardDecisionReject},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := approvedTestInput()
			reviewer := &recordingReviewer{result: llmop.SafeguardStageResult{
				APIVersion:    llmop.APIVersion,
				Stage:         llmop.SafeguardStageName,
				RequestID:     input.Request.RequestID,
				CorrelationID: input.Request.CorrelationID,
				TraceID:       input.Request.TraceID,
				Status:        test.status,
				Decision:      llmop.Decision{Action: test.action},
			}}
			runner := &recordingFlowRunner{}
			resolverCalls := 0
			orchestrator := New(reviewer, runner, func(string, string) (llmopbridge.TrustedAppVersionBinding, error) {
				resolverCalls++
				return llmopbridge.TrustedAppVersionBinding{}, nil
			})

			result, err := orchestrator.Run(context.Background(), input)
			if err != nil {
				t.Fatalf("stop non-approved safeguard: %v", err)
			}
			if result.Status != StatusSafeguardStopped {
				t.Fatalf("status = %q, want %q", result.Status, StatusSafeguardStopped)
			}
			if runner.calls != 0 || resolverCalls != 0 {
				t.Fatalf("non-approved request reached geon/resolver: %d/%d", runner.calls, resolverCalls)
			}
		})
	}
}

func TestOrchestratorStopsIncompleteApprovalBeforeGeon(t *testing.T) {
	input := approvedTestInput()
	stage := approvedSafeguard(input.Request)
	stage.Continuation = nil
	runner := &recordingFlowRunner{}
	orchestrator := New(
		&recordingReviewer{result: stage},
		runner,
		func(string, string) (llmopbridge.TrustedAppVersionBinding, error) {
			return llmopbridge.TrustedAppVersionBinding{}, nil
		},
	)

	if _, err := orchestrator.Run(context.Background(), input); err == nil {
		t.Fatal("incomplete safeguard approval must fail closed")
	}
	if runner.calls != 0 {
		t.Fatalf("incomplete approval invoked geon %d times", runner.calls)
	}
}

func TestOrchestratorRejectsIdentityDriftBeforeSafeguard(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*Input)
	}{
		{
			name: "user request",
			mutate: func(input *Input) {
				input.AnalysisRequest.Data.Application.UserRequest = "changed request"
			},
		},
		{
			name: "correlation id",
			mutate: func(input *Input) {
				input.AnalysisRequest.CorrelationID = "flow-changed-001"
			},
		},
		{
			name: "trace id",
			mutate: func(input *Input) {
				input.AnalysisRequest.TraceID = "trace-changed-001"
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := approvedTestInput()
			test.mutate(&input)
			reviewer := &recordingReviewer{result: approvedSafeguard(input.Request)}
			runner := &recordingFlowRunner{}
			orchestrator := New(
				reviewer,
				runner,
				func(string, string) (llmopbridge.TrustedAppVersionBinding, error) {
					return llmopbridge.TrustedAppVersionBinding{}, nil
				},
			)

			if _, err := orchestrator.Run(context.Background(), input); err == nil {
				t.Fatal("identity drift must fail closed")
			}
			if reviewer.calls != 0 || runner.calls != 0 {
				t.Fatalf("identity drift reached reviewer/geon: %d/%d", reviewer.calls, runner.calls)
			}
		})
	}
}

func TestOrchestratorReturnsGeonRejectWithoutProjection(t *testing.T) {
	input := approvedTestInput()
	runner := &recordingFlowRunner{run: agentcontrol.AutomationRun{
		RunID:         "run-rejected-001",
		CorrelationID: input.Request.CorrelationID,
		TraceID:       input.Request.TraceID,
		Status:        agentcontrol.AutomationRunStatusCompleted,
		Flow: &agentcontrol.Flow{
			CorrelationID: input.Request.CorrelationID,
			TraceID:       input.Request.TraceID,
			State:         agentcontrol.StateDecisionRejected,
		},
	}}
	orchestrator := New(
		&recordingReviewer{result: approvedSafeguard(input.Request)},
		runner,
		func(string, string) (llmopbridge.TrustedAppVersionBinding, error) {
			return llmopbridge.TrustedAppVersionBinding{}, nil
		},
	)

	result, err := orchestrator.Run(context.Background(), input)
	if err != nil {
		t.Fatalf("geon rejection must be an auditable terminal result: %v", err)
	}
	if result.Status != StatusGeonRejected || result.ApprovedProjection != nil {
		t.Fatalf("unexpected geon rejection result: %#v", result)
	}
}

type recordingReviewer struct {
	result llmop.SafeguardStageResult
	err    error
	calls  int
}

type recordingFlowRunner struct {
	run      agentcontrol.AutomationRun
	replayed bool
	err      error
	calls    int
}

func (runner *recordingFlowRunner) RunAnalysisRequest(
	_ context.Context,
	_ agentcontrol.ApplicationAnalysisRequestEnvelope,
) (agentcontrol.AutomationRun, bool, error) {
	runner.calls++
	return runner.run, runner.replayed, runner.err
}

func (reviewer *recordingReviewer) Review(
	_ context.Context,
	_ llmop.Request,
) (llmop.SafeguardStageResult, error) {
	reviewer.calls++
	return reviewer.result, reviewer.err
}

func integrationAutomationRunner() *agentcontrol.AutomationRunner {
	now := func() time.Time {
		return time.Date(2026, 8, 24, 6, 0, 0, 0, time.UTC)
	}
	runner := agentcontrol.NewAutomationRunnerWithAdapter(
		agentcontrol.LocalRequirementAnalyzer{},
		agentcontrol.CatalogResourceRecommender{
			Catalog: agentcontrol.ResourceCatalog{
				Version: "integration-1.0",
				Candidates: []agentcontrol.CatalogResource{{
					CandidateID:       "resource-cpu-balanced",
					CPUCores:          4,
					MemoryMiB:         8192,
					StorageGiB:        20,
					CostPerHour:       0.1,
					AvailabilityScore: 0.99,
				}},
			},
			Now: now,
		},
		agentcontrol.NewService(),
		agentcontrol.MockDeploymentAdapter{Now: now},
	)
	runner.Now = now
	runner.IDGenerator = func(prefix string) string { return prefix + "-trusted-001" }
	return runner
}

func approvedTestInput() Input {
	request := llmop.Request{
		APIVersion:    llmop.APIVersion,
		RequestID:     "llmop-request-trusted-001",
		CorrelationID: "flow-trusted-001",
		TraceID:       "trace-trusted-001",
		CandidateID:   "qwen-safeguard-001",
		RequestedBy:   "khu-user",
		Application: llmop.Application{
			AppVersionID: "appver-trusted-001",
			UserRequest:  "Deploy with GPU 0, CPU 4 cores, memory 8GiB, storage 20GiB, and 1 replica.",
		},
		Policy: llmop.RequestPolicy{Mode: llmop.ModePrepareOnly},
	}
	analysis := agentcontrol.ApplicationAnalysisRequestEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       "msg-analysis-trusted-001",
			MessageType:     agentcontrol.MessageApplicationAnalysisRequest,
			OccurredAt:      "2026-08-24T06:00:00Z",
			CorrelationID:   request.CorrelationID,
			TraceID:         request.TraceID,
			Source:          agentcontrol.Endpoint{System: "khu-ui", Component: "request-api"},
			Target:          agentcontrol.Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: agentcontrol.ApplicationAnalysisRequestData{
			Application: agentcontrol.AnalysisRequestApplication{
				AppID:      "cpu-service",
				AppVersion: "1.0.0",
				Artifact: agentcontrol.Artifact{
					Type:       "script",
					URI:        "file:///opt/apps/cpu-service/run.sh",
					Entrypoint: []string{"bash", "run.sh"},
				},
				UserRequest: request.Application.UserRequest,
			},
		},
	}
	return Input{Request: request, AnalysisRequest: analysis}
}

func approvedSafeguard(request llmop.Request) llmop.SafeguardStageResult {
	confidence := 0.99
	return llmop.SafeguardStageResult{
		APIVersion:    llmop.APIVersion,
		Stage:         llmop.SafeguardStageName,
		RequestID:     request.RequestID,
		CorrelationID: request.CorrelationID,
		TraceID:       request.TraceID,
		Status:        llmop.StatusSafeguardApproved,
		Approved:      true,
		Decision: llmop.Decision{
			Action:            llmop.SafeguardDecisionAllow,
			ReasonCode:        "BOUNDED_REQUEST_ALLOWED",
			Reason:            "The bounded request may continue.",
			Confidence:        &confidence,
			ObservationStatus: "not_provided",
		},
		RequestGuard: plannerguard.Decision{
			Valid:         true,
			Status:        "approved",
			PolicyVersion: "test-policy-v1",
			Reason:        "request satisfies the bounded policy",
			Checks: []plannerguard.Check{{
				Name:   "required_fields",
				Passed: true,
				Reason: "required fields are present",
			}},
		},
		Review: &llmop.SafeguardReviewEvidence{
			Provider:    llmop.OfflineFixtureProvider,
			CandidateID: request.CandidateID,
			ActualModel: llmop.OfflineFixtureActualModel,
			Decision:    llmop.SafeguardDecisionAllow,
			ReasonCode:  "BOUNDED_REQUEST_ALLOWED",
			Confidence:  &confidence,
		},
		Input: llmop.InputSummary{ObservationStatus: "not_provided"},
		Continuation: &llmop.ApprovedContinuationEvidence{
			Stage:             llmop.SafeguardContinuationStageName,
			RequestID:         request.RequestID,
			CorrelationID:     request.CorrelationID,
			TraceID:           request.TraceID,
			PolicyVersion:     "test-policy-v1",
			CandidateID:       request.CandidateID,
			ReviewDecision:    llmop.SafeguardDecisionAllow,
			ReviewReasonCode:  "BOUNDED_REQUEST_ALLOWED",
			Confidence:        &confidence,
			ObservationStatus: "not_provided",
			BindingAlgorithm:  llmop.SafeguardBindingAlgorithm,
			RequestBinding:    strings.Repeat("a", 64),
			SubmissionMode:    "not_submitted",
		},
	}
}
