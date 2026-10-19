package llmop

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestOfflineFixturePlannerRejectsSpoofedExecutionEvidence(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	candidate := testCandidate()
	candidate.Provider = "openai-compatible"
	candidate.ActualModel = "qwen3.5:4b"

	result, err := NewOfflineFixturePlanner(`{}`, `{}`, normalizer).Prepare(
		context.Background(),
		candidate,
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil || result.Status != StatusModelUnavailable {
		t.Fatalf("spoofed offline evidence must fail closed: status=%s err=%v", result.Status, err)
	}
	if result.Evidence.SafeguardReview != nil ||
		result.Evidence.Model.Provider != "" ||
		result.Evidence.Model.ActualModel != "" {
		t.Fatalf("spoofed execution labels reached evidence: %#v", result.Evidence)
	}
}

func TestParseSafeguardReviewRejectsDuplicateObjectKeys(t *testing.T) {
	_, err := parseSafeguardReview(`{
		"decision":"allow_request",
		"decision":"reject_request",
		"reason_code":"DUPLICATE_DECISION",
		"reason":"Duplicate keys must not be last-wins.",
		"confidence":0.9
	}`)
	if err == nil {
		t.Fatal("duplicate safeguard keys must be rejected before typed decoding")
	}
}

func TestSafeguardPromptDoesNotRequireDeploymentIdentifierForPreparation(t *testing.T) {
	for _, required := range []string{
		"request_scope booleans are informational only",
		"false deployment_id_present value is not missing planning information",
		"BOUNDED_RESOURCE_PLAN",
		"set reason to exactly: The request is a bounded prepare-only resource planning request.",
	} {
		if !strings.Contains(naturalLanguageSafeguardSystemPrompt, required) {
			t.Fatalf("safeguard prompt omitted prepare-only boundary: %q", required)
		}
	}
}

func TestSafeguardedPlannerRunsReviewBeforeManifestProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: `{
		"decision":"allow_request",
		"reason_code":"BOUNDED_REQUEST_ALLOWED",
		"reason":"The bounded planning request contains explicit resource values.",
		"confidence":0.99
	}`}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	request := testRequest(t, now)
	request.Application.PlanningConstraints = &PlanningConstraints{
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
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("safeguarded pipeline returned an unexpected error: %v", err)
	}
	if safeguardClient.calls != 1 || proposalClient.calls != 1 {
		t.Fatalf(
			"expected one safeguard and one proposal completion, got %d and %d",
			safeguardClient.calls,
			proposalClient.calls,
		)
	}
	if !strings.Contains(safeguardClient.userPrompt, `"decision"`) ||
		strings.Contains(safeguardClient.userPrompt, `"action"`) {
		t.Fatalf("safeguard stage received the wrong output contract: %s", safeguardClient.userPrompt)
	}
	if !strings.Contains(proposalClient.userPrompt, `"action"`) ||
		strings.Contains(proposalClient.userPrompt, `"decision"`) {
		t.Fatalf("proposal stage received the wrong output contract: %s", proposalClient.userPrompt)
	}
	var safeguardPrompt map[string]any
	if err := json.Unmarshal(
		[]byte(strings.TrimPrefix(safeguardClient.userPrompt, safeguardReviewUserPrefix)),
		&safeguardPrompt,
	); err != nil {
		t.Fatalf("decode safeguard stage prompt: %v", err)
	}
	var proposalPrompt map[string]any
	if err := json.Unmarshal(
		[]byte(strings.TrimPrefix(proposalClient.userPrompt, userPromptPrefix)),
		&proposalPrompt,
	); err != nil {
		t.Fatalf("decode proposal stage prompt: %v", err)
	}
	for _, key := range []string{"user_request", "request_scope", "operation_context"} {
		if !reflect.DeepEqual(safeguardPrompt[key], proposalPrompt[key]) {
			t.Fatalf("stage prompts changed shared %s context", key)
		}
	}
	if reflect.DeepEqual(safeguardPrompt["required_output"], proposalPrompt["required_output"]) {
		t.Fatal("stage prompts must use distinct required_output contracts")
	}
	operationContext, ok := safeguardPrompt["operation_context"].(map[string]any)
	if !ok {
		t.Fatal("safeguard prompt omitted the shared operation_context object")
	}
	planningConstraints, ok := operationContext["planning_constraints"].(map[string]any)
	if !ok {
		t.Fatal("shared operation_context omitted trusted planning constraints")
	}
	if _, ok := planningConstraints["recommended_resources"].(map[string]any); !ok {
		t.Fatal("shared operation_context omitted the exact upstream recommendation")
	}
	for _, key := range []string{"source_profile_id", "source_recommendation_id"} {
		if _, exists := planningConstraints[key]; exists {
			t.Fatalf("integration-only %s reached a Qwen prompt", key)
		}
	}
	if result.Status != StatusHandoffReady || result.Handoff.PreparedRequest == nil {
		t.Fatalf("expected %s with a prepared request, got %s", StatusHandoffReady, result.Status)
	}
	if result.Evidence.SafeguardReview == nil ||
		result.Evidence.SafeguardReview.Decision != SafeguardDecisionAllow ||
		result.Evidence.SafeguardReview.ReasonCode != "BOUNDED_REQUEST_ALLOWED" {
		t.Fatalf("unexpected safeguard evidence: %#v", result.Evidence.SafeguardReview)
	}
}

func TestSafeguardedPlannerStopsAtClarification(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "이 서비스의 배포 계획을 준비해줘."
	safeguardClient := &capturingCompletionClient{content: `{
		"decision":"request_clarification",
		"reason_code":"RESOURCE_VALUES_REQUIRED",
		"reason":"Exact CPU, GPU, memory, and storage values are required.",
		"confidence":0.98
	}`}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("clarification is a valid safeguard result: %v", err)
	}
	if result.Status != StatusClarificationNeeded || proposalClient.calls != 0 {
		t.Fatalf("expected clarification before proposal, got %s and %d calls", result.Status, proposalClient.calls)
	}
	assertNoPreparedHandoff(t, result)
}

func TestSafeguardedPlannerStopsAtReviewRejection(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: `{
		"decision":"reject_request",
		"reason_code":"RESPONSIBILITY_BOUNDARY",
		"reason":"The request crosses the bounded planning responsibility.",
		"confidence":0.97
	}`}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err != nil {
		t.Fatalf("bounded safeguard rejection is a valid outcome: %v", err)
	}
	if result.Status != StatusRequestRejected || proposalClient.calls != 0 {
		t.Fatalf("expected review rejection before proposal, got %s and %d calls", result.Status, proposalClient.calls)
	}
	assertNoPreparedHandoff(t, result)
}

func TestSafeguardedPlannerRejectsMalformedReviewWithoutProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: `{
		"decision":"allow_request",
		"reason_code":"BOUNDED_REQUEST_ALLOWED",
		"reason":"The request appears bounded.",
		"confidence":0.99,
		"resources":{"cpu":"4"}
	}`}
	proposalClient := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected malformed safeguard output to fail")
	}
	if result.Status != StatusModelUnavailable || proposalClient.calls != 0 {
		t.Fatalf("expected model failure before proposal, got %s and %d calls", result.Status, proposalClient.calls)
	}
	assertNoPreparedHandoff(t, result)
}

func TestSafeguardedPlannerRejectsProviderDisclosureBeforeProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name       string
		provider   string
		reasonCode string
		reason     string
	}{
		{
			name:       "provider reason code",
			reasonCode: "PROVIDER_SELECTED",
			reason:     "The request crosses the bounded planning responsibility.",
		},
		{
			name:       "provider word in reason",
			reasonCode: "RESPONSIBILITY_BOUNDARY",
			reason:     "A provider must be selected upstream.",
		},
		{
			name:       "configured provider identifier with punctuation",
			reasonCode: "RESPONSIBILITY_BOUNDARY",
			reason:     `Use "fixture-openai-compatible's" policy for this request.`,
		},
		{
			name:       "short configured provider identifier",
			provider:   "hf",
			reasonCode: "RESPONSIBILITY_BOUNDARY",
			reason:     "Use hf for this request.",
		},
		{
			name:       "configured provider with nonstandard delimiter",
			provider:   "foo@bar",
			reasonCode: "RESPONSIBILITY_BOUNDARY",
			reason:     "Use foo@bar for this request.",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			content, err := json.Marshal(map[string]any{
				"decision":    SafeguardDecisionReject,
				"reason_code": test.reasonCode,
				"reason":      test.reason,
				"confidence":  0.98,
			})
			if err != nil {
				t.Fatalf("marshal safeguard review: %v", err)
			}
			safeguardClient := &capturingCompletionClient{content: string(content)}
			proposalClient := &capturingCompletionClient{}
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }
			candidate := testCandidate()
			if test.provider != "" {
				candidate.Provider = test.provider
			}

			result, err := newSafeguardedPlanner(
				safeguardClient,
				proposalClient,
				normalizer,
			).Prepare(
				context.Background(),
				candidate,
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err == nil || result.Status != StatusModelUnavailable || proposalClient.calls != 0 {
				t.Fatalf("expected provider disclosure to fail before proposal: %s, %v", result.Status, err)
			}
			assertNoPreparedHandoff(t, result)
		})
	}
}

func TestSafeguardedPlannerRejectsProviderWithoutAlphanumericIdentity(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{}
	proposalClient := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	candidate := testCandidate()
	candidate.Provider = "---@"

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		candidate,
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil || result.Status != StatusModelUnavailable ||
		safeguardClient.calls != 0 || proposalClient.calls != 0 {
		t.Fatalf("expected invalid provider identity before completion: %s, %v", result.Status, err)
	}
	assertNoPreparedHandoff(t, result)
}

func TestSafeguardedPlannerRunsNoModelForDeterministicRequestRejection(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Policy.Mode = "approved_submit"
	safeguardClient := &capturingCompletionClient{}
	proposalClient := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil {
		t.Fatal("expected deterministic request rejection")
	}
	if result.Status != StatusRequestRejected ||
		safeguardClient.calls != 0 ||
		proposalClient.calls != 0 {
		t.Fatalf(
			"expected no model calls, got status %s and calls %d/%d",
			result.Status,
			safeguardClient.calls,
			proposalClient.calls,
		)
	}
	assertNoPreparedHandoff(t, result)
}

func TestSafeguardedPlannerRejectsLowConfidenceAllowBeforeProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: `{
		"decision":"allow_request",
		"reason_code":"BOUNDED_REQUEST_ALLOWED",
		"reason":"The request appears bounded.",
		"confidence":0.49
	}`}
	proposalClient := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil || result.Status != StatusModelUnavailable || proposalClient.calls != 0 {
		t.Fatalf("expected bounded safeguard failure before proposal: %s, %v", result.Status, err)
	}
	assertNoPreparedHandoff(t, result)
}

func TestSafeguardedPlannerClearsHandoffWhenProposalFailsSemanticGuard(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	safeguardClient := &capturingCompletionClient{content: `{
		"decision":"allow_request",
		"reason_code":"BOUNDED_REQUEST_ALLOWED",
		"reason":"The request is bounded to prepare-only planning.",
		"confidence":0.99
	}`}
	proposalClient := &capturingCompletionClient{
		content: createProposalJSON("8", "16Gi", "1", "20Gi", "nvidia"),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil || result.Status != StatusManifestRejected ||
		safeguardClient.calls != 1 || proposalClient.calls != 1 {
		t.Fatalf("expected semantic rejection after both stages: %s, %v", result.Status, err)
	}
	assertNoPreparedHandoff(t, result)
}

func assertNoPreparedHandoff(t *testing.T, result Result) {
	t.Helper()
	if result.Manifest != nil || result.Handoff.PreparedRequest != nil ||
		result.Handoff.NextEndpoint != "" ||
		result.Handoff.SubmissionMode != "not_submitted" {
		t.Fatalf("non-handoff result exposed prepared submission state: %#v", result.Handoff)
	}
}
