package llmop

import (
	"context"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

const allowedSafeguardReviewJSON = `{
	"decision":"allow_request",
	"reason_code":"BOUNDED_REQUEST_ALLOWED",
	"reason":"The bounded planning request may continue to trusted enrichment.",
	"confidence":0.99
}`

func TestReviewRequestAllowsWithoutCallingProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: allowedSafeguardReviewJSON}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	pipeline := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		testStageNormalizer(now),
	)

	stage, err := pipeline.ReviewRequest(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err != nil {
		t.Fatalf("guard-first review returned an unexpected error: %v", err)
	}
	if safeguardClient.calls != 1 || proposalClient.calls != 0 {
		t.Fatalf("review must call safeguard/proposal exactly 1/0, got %d/%d", safeguardClient.calls, proposalClient.calls)
	}
	if stage.Status != StatusSafeguardApproved || !stage.Approved || stage.Continuation == nil {
		t.Fatalf("expected an approved continuation, got %#v", stage)
	}
	if stage.Continuation.SubmissionMode != "not_submitted" ||
		stage.Continuation.BindingAlgorithm != SafeguardBindingAlgorithm ||
		len(stage.Continuation.RequestBinding) != 64 {
		t.Fatalf("unexpected continuation evidence: %#v", stage.Continuation)
	}
}

func TestPrepareApprovedAcceptsOnlyTrustedPlanningConstraintEnrichment(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: allowedSafeguardReviewJSON}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	pipeline := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		testStageNormalizer(now),
	)
	request := testRequest(t, now)
	stage, err := pipeline.ReviewRequest(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("review request: %v", err)
	}
	request.Application.PlanningConstraints = testStagePlanningConstraints()

	result, err := pipeline.PrepareApproved(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
		stage,
	)
	if err != nil {
		t.Fatalf("prepare approved request: %v", err)
	}
	if safeguardClient.calls != 1 || proposalClient.calls != 1 {
		t.Fatalf("two-stage flow must call safeguard/proposal exactly 1/1, got %d/%d", safeguardClient.calls, proposalClient.calls)
	}
	if result.Status != StatusHandoffReady || result.Handoff.PreparedRequest == nil {
		t.Fatalf("expected a prepared AppDeploy request, got status=%s", result.Status)
	}
	if result.Evidence.SafeguardReview == nil ||
		result.Evidence.SafeguardReview.ReasonCode != stage.Review.ReasonCode {
		t.Fatal("proposal result did not preserve verified safeguard evidence")
	}
}

func TestReviewRequestDoesNotIssueContinuationForNonAllowDecisions(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name       string
		content    string
		wantStatus string
	}{
		{
			name: "clarification",
			content: `{"decision":"request_clarification","reason_code":"RESOURCE_VALUES_REQUIRED","reason":"Explicit resource intent is required.","confidence":0.9}`,
			wantStatus: StatusClarificationNeeded,
		},
		{
			name: "rejection",
			content: `{"decision":"reject_request","reason_code":"RESPONSIBILITY_BOUNDARY","reason":"The request is outside the bounded responsibility.","confidence":0.9}`,
			wantStatus: StatusRequestRejected,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			safeguardClient := &capturingCompletionClient{content: test.content}
			proposalClient := &capturingCompletionClient{}
			stage, err := newSafeguardedPlanner(
				safeguardClient,
				proposalClient,
				testStageNormalizer(now),
			).ReviewRequest(
				context.Background(),
				testCandidate(),
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err != nil {
				t.Fatalf("bounded non-allow decision must not be an execution error: %v", err)
			}
			if stage.Status != test.wantStatus || stage.Approved || stage.Continuation != nil {
				t.Fatalf("non-allow decision issued continuation: %#v", stage)
			}
			if safeguardClient.calls != 1 || proposalClient.calls != 0 {
				t.Fatalf("non-allow flow called safeguard/proposal %d/%d", safeguardClient.calls, proposalClient.calls)
			}
		})
	}
}

func TestReviewRequestStopsAtDeterministicGuardWithoutModel(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: allowedSafeguardReviewJSON}
	proposalClient := &capturingCompletionClient{}
	request := testRequest(t, now)
	request.Policy.Mode = "submit"

	stage, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		testStageNormalizer(now),
	).ReviewRequest(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil || stage.Status != StatusRequestRejected {
		t.Fatalf("deterministic rejection must fail closed, status=%s err=%v", stage.Status, err)
	}
	if safeguardClient.calls != 0 || proposalClient.calls != 0 || stage.Continuation != nil {
		t.Fatalf("deterministic rejection reached a model or continuation: %d/%d %#v", safeguardClient.calls, proposalClient.calls, stage.Continuation)
	}
}

func TestReviewRequestRejectsPreGeonPlanningConstraintsWithoutModel(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: allowedSafeguardReviewJSON}
	proposalClient := &capturingCompletionClient{}
	request := testRequest(t, now)
	request.Application.PlanningConstraints = testStagePlanningConstraints()

	stage, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		testStageNormalizer(now),
	).ReviewRequest(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil || stage.Status != StatusRequestRejected {
		t.Fatalf("pre-geon constraints must fail closed, status=%s err=%v", stage.Status, err)
	}
	if safeguardClient.calls != 0 || proposalClient.calls != 0 || stage.Continuation != nil {
		t.Fatalf("pre-geon constraints reached a model or continuation: %d/%d %#v", safeguardClient.calls, proposalClient.calls, stage.Continuation)
	}
}

func TestPrepareApprovedRejectsChangedBoundInputsBeforeProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	baseRequest := testRequest(t, now)
	baseCandidate := testCandidate()
	basePolicy := testGuardPolicy()
	safeguardClient := &capturingCompletionClient{content: allowedSafeguardReviewJSON}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	pipeline := newSafeguardedPlanner(safeguardClient, proposalClient, testStageNormalizer(now))
	stage, err := pipeline.ReviewRequest(
		context.Background(),
		baseCandidate,
		basePolicy,
		baseRequest,
	)
	if err != nil {
		t.Fatalf("create approved stage: %v", err)
	}

	tests := []struct {
		name      string
		mutate    func(*Request, *llmclient.Candidate, *plannerguard.Policy, *SafeguardStageResult)
	}{
		{
			name: "user request",
			mutate: func(request *Request, _ *llmclient.Candidate, _ *plannerguard.Policy, _ *SafeguardStageResult) {
				request.Application.UserRequest += " Audit note alpha."
			},
		},
		{
			name: "operation context",
			mutate: func(request *Request, _ *llmclient.Candidate, _ *plannerguard.Policy, _ *SafeguardStageResult) {
				request.OperationContext.MetricsSummary.LatencyP95MS++
			},
		},
		{
			name: "app version",
			mutate: func(request *Request, _ *llmclient.Candidate, _ *plannerguard.Policy, _ *SafeguardStageResult) {
				request.Application.AppVersionID = "appver-llm-inference-v2"
			},
		},
		{
			name: "candidate",
			mutate: func(_ *Request, candidate *llmclient.Candidate, _ *plannerguard.Policy, _ *SafeguardStageResult) {
				candidate.Provider = "fixture-other-compatible"
			},
		},
		{
			name: "policy",
			mutate: func(_ *Request, _ *llmclient.Candidate, policy *plannerguard.Policy, _ *SafeguardStageResult) {
				policy.Version = "v2"
			},
		},
		{
			name: "fabricated binding",
			mutate: func(_ *Request, _ *llmclient.Candidate, _ *plannerguard.Policy, approved *SafeguardStageResult) {
				approved.Continuation.RequestBinding = strings.Repeat("0", 64)
			},
		},
		{
			name: "invalid approval flag",
			mutate: func(_ *Request, _ *llmclient.Candidate, _ *plannerguard.Policy, approved *SafeguardStageResult) {
				approved.Approved = false
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request, cloneErr := cloneRequest(baseRequest)
			if cloneErr != nil {
				t.Fatalf("clone request: %v", cloneErr)
			}
			request.Application.PlanningConstraints = testStagePlanningConstraints()
			candidate := baseCandidate
			policy := basePolicy
			approved := stage
			continuationCopy := *stage.Continuation
			approved.Continuation = &continuationCopy
			test.mutate(&request, &candidate, &policy, &approved)
			proposalCalls := proposalClient.calls

			result, prepareErr := pipeline.PrepareApproved(
				context.Background(),
				candidate,
				policy,
				request,
				approved,
			)
			if prepareErr == nil || result.Status != StatusRequestRejected {
				t.Fatalf("changed or invalid approval must fail closed: status=%s err=%v", result.Status, prepareErr)
			}
			if proposalClient.calls != proposalCalls {
				t.Fatal("changed or invalid approval reached proposal completion")
			}
			assertNoPreparedHandoff(t, result)
		})
	}
}

func testStageNormalizer(now time.Time) Normalizer {
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	return normalizer
}

func testStagePlanningConstraints() *PlanningConstraints {
	return &PlanningConstraints{
		SourceProfileID:        "profile-shared-context-001",
		SourceRecommendationID: "recommendation-shared-context-001",
		RecommendationFeasible: true,
		CPUCoresMin:            2,
		MemoryMiBMin:           8 * 1024,
		GPUCountMin:            1,
		StorageGiBMin:          10,
		Accelerator:            "nvidia",
		RecommendedResources: &RecommendedResources{
			CPUCores:    4,
			MemoryMiB:   16 * 1024,
			GPUCount:    1,
			StorageGiB:  20,
			Accelerator: "nvidia",
		},
	}
}
