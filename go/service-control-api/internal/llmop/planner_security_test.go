package llmop

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func TestNewResultDoesNotEchoUnsafeIdentifiers(t *testing.T) {
	request := testRequest(t, mustTime(t, "2026-08-05T14:05:00+09:00"))
	request.RequestID = strings.Repeat("r", maxRawObservationFieldBytes)
	request.CorrelationID = "corr\u202eunsafe"
	request.TraceID = "trace with spaces"

	result := NewResult(request)
	if result.RequestID != "" || result.CorrelationID != "" || result.TraceID != "" {
		t.Fatalf("unsafe identifiers must not be echoed: %#v", result)
	}

	request.RequestID = "req"
	request.CorrelationID = "corr"
	request.TraceID = "trace"
	result = NewResult(request)
	if result.RequestID != "" || result.CorrelationID != "" || result.TraceID != "" {
		t.Fatalf("short strong identifiers must not be echoed: %#v", result)
	}
}

func TestParseProposalRejectsDuplicateObjectKeys(t *testing.T) {
	_, err := parseProposal(`{
		"action":"request_clarification",
		"action":"create_deployment_manifest",
		"reason_code":"DUPLICATE_ACTION",
		"reason":"Duplicate keys must not be last-wins.",
		"confidence":0.9,
		"accelerator":"none",
		"resources":{"cpu":"4","memory":"8Gi","gpu":"0","storage":"20Gi"},
		"assumptions":[]
	}`)
	if err == nil {
		t.Fatal("duplicate proposal keys must be rejected before typed decoding")
	}
}

func TestParseProposalRejectsDeepJSONBeforeTypedDecode(t *testing.T) {
	content := strings.Repeat("[", maxCompletionJSONDepth+2) +
		"0" + strings.Repeat("]", maxCompletionJSONDepth+2)
	if _, err := parseProposal(content); err == nil {
		t.Fatal("deep JSON must fail before recursive parsing exceeds the contract")
	}
}

func TestParseProposalRejectsCaseVariantKey(t *testing.T) {
	_, err := parseProposal(`{
		"Action":"request_clarification",
		"reason_code":"CASE_VARIANT_KEY",
		"reason":"Canonical lowercase keys are required.",
		"confidence":0.9
	}`)
	if err == nil {
		t.Fatal("case-variant JSON keys must not reach Go's case-insensitive struct decoder")
	}
}

func TestValidateProposalRejectsUnsafeDisplayCharacters(t *testing.T) {
	confidence := 0.9
	proposal := Proposal{
		Action:      ActionRequestClarification,
		ReasonCode:  "RESOURCE_VALUES_REQUIRED",
		Reason:      "safe prefix\u202eevil suffix",
		Confidence:  &confidence,
		Assumptions: []string{},
	}
	if err := validateProposal(proposal, guardedRequest(), guardPolicyWithoutSensitiveKeys()); err == nil {
		t.Fatal("bidirectional display controls must be rejected")
	}
}

func TestPrepareRejectsUnsafeCandidateEvidenceLabels(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	candidate := testCandidate()
	candidate.ActualModel = "fixture\u202emodel"
	client := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		candidate,
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil || result.Status != StatusModelUnavailable || client.calls != 0 {
		t.Fatalf("unsafe candidate evidence label must fail before completion: %s, %v", result.Status, err)
	}
	if result.Evidence.Model.ActualModel != "" || result.Evidence.Model.Provider != "" {
		t.Fatal("invalid candidate labels must not be copied into failure evidence")
	}
}

func TestPreparePreservesZeroConfidenceForClarification(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"request_clarification",
		"reason_code":"MORE_DETAIL_REQUIRED",
		"reason":"More resource detail is required.",
		"confidence":0
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err != nil {
		t.Fatalf("zero confidence clarification is a valid bounded result: %v", err)
	}
	if result.Status != StatusClarificationNeeded {
		t.Fatalf("expected %s, got %s", StatusClarificationNeeded, result.Status)
	}
	if result.Decision.Confidence == nil || *result.Decision.Confidence != 0 {
		t.Fatal("zero confidence must be preserved in the decision")
	}
	content, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal result: %v", err)
	}
	if !strings.Contains(string(content), `"confidence":0`) {
		t.Fatal("zero confidence must remain present in the public JSON result")
	}
}

type staticCompletionClient struct {
	completion llmclient.Completion
}

func (client staticCompletionClient) Complete(
	_ context.Context,
	_ llmclient.Candidate,
	_ string,
	_ string,
) (llmclient.Completion, error) {
	return client.completion, nil
}

func TestPrepareRequiresProposalConfidencePresence(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"request_clarification",
		"reason_code":"MISSING_RESOURCE_REQUIREMENTS",
		"reason":"The requested resource envelope is not specific enough."
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected missing confidence to fail the bounded output contract")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
}

func TestPrepareRejectsSecretBearingProposalText(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"request_clarification",
		"reason_code":"UNSAFE_MODEL_OUTPUT",
		"reason":"Authorization: Bearer leaked-model-secret",
		"confidence":0.2
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected secret-bearing model output to be rejected")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
	if strings.Contains(result.Decision.Reason, "leaked-model-secret") {
		t.Fatal("public decision reason exposed model-provided secret text")
	}
}

func TestPrepareRejectsKoreanResponsibilityBoundaryInProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"request_clarification",
		"reason_code":"OUT_OF_SCOPE_DETAIL",
		"reason":"쿠버네티스에 배포할 런타임 정보가 필요합니다.",
		"confidence":0.2
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected Korean responsibility-boundary output to be rejected")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
}

func TestPrepareRequiresNonCreateProposalToOmitResourceKeys(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	for _, field := range []string{
		`"accelerator":null`,
		`"resources":null`,
		`"resources":{}`,
	} {
		t.Run(field, func(t *testing.T) {
			client := &capturingCompletionClient{content: `{
				"action":"request_clarification",
				"reason_code":"MORE_DETAIL_REQUIRED",
				"reason":"More resource detail is required.",
				"confidence":0.2,
				` + field + `
			}`}
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			result, err := NewPlanner(client, normalizer).Prepare(
				context.Background(),
				testCandidate(),
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err == nil {
				t.Fatal("expected explicit non-create resource key to be rejected")
			}
			if result.Status != StatusManifestRejected {
				t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
			}
		})
	}
}

func TestPrepareRejectsOperationalReasonCodesAndText(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name       string
		reasonCode string
		reason     string
	}{
		{name: "provider code", reasonCode: "DEPLOY_TO_AWS", reason: "More detail is required."},
		{name: "runtime command", reasonCode: "MORE_DETAIL_REQUIRED", reason: "Run Python deploy.py."},
		{name: "localhost endpoint", reasonCode: "MORE_DETAIL_REQUIRED", reason: "Use localhost:8080."},
		{name: "unix socket", reasonCode: "MORE_DETAIL_REQUIRED", reason: "Use /var/run/planner.sock."},
		{name: "GPU SKU code", reasonCode: "H100_SELECTED", reason: "More detail is required."},
		{name: "SLO code", reasonCode: "P95_SLO_MET", reason: "More detail is required."},
		{name: "GPU SKU reason", reasonCode: "MORE_DETAIL_REQUIRED", reason: "An H100 requirement is satisfied."},
		{name: "OS reason", reasonCode: "MORE_DETAIL_REQUIRED", reason: "Ubuntu 24.04 is available."},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			content, err := json.Marshal(map[string]any{
				"action":      ActionRequestClarification,
				"reason_code": test.reasonCode,
				"reason":      test.reason,
				"confidence":  0.2,
			})
			if err != nil {
				t.Fatalf("marshal proposal: %v", err)
			}
			client := &capturingCompletionClient{content: string(content)}
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			result, err := NewPlanner(client, normalizer).Prepare(
				context.Background(),
				testCandidate(),
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err == nil {
				t.Fatal("expected operational output to be rejected")
			}
			if result.Status != StatusManifestRejected {
				t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
			}
		})
	}
}

func TestPrepareReplacesSuccessfulModelNarrativeWithDeterministicExplanation(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"create_deployment_manifest",
		"reason_code":"MODEL_RESOURCE_NARRATIVE",
		"reason":"CPU 256 and memory 2Ti are ready.",
		"confidence":0.95,
		"accelerator":"nvidia",
		"resources":{"cpu":"4","memory":"16Gi","gpu":"1","storage":"20Gi"},
		"assumptions":["CPU 128 would also be acceptable."]
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err != nil {
		t.Fatalf("valid exact resources should prepare a manifest: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
	if result.Decision.ReasonCode != "BOUNDED_MANIFEST_PREPARED" ||
		strings.Contains(result.Decision.Reason, "256") ||
		len(result.Decision.Assumptions) != 0 {
		t.Fatalf("successful public decision retained model narrative: %#v", result.Decision)
	}
}

func TestValidateResourceCeilingsRejectsNonCanonicalCounts(t *testing.T) {
	for _, resources := range []appdeploy.ResourceRequirements{
		{CPU: "04", Memory: "16Gi", GPU: "1", Storage: "20Gi"},
		{CPU: "4", Memory: "16Gi", GPU: "01", Storage: "20Gi"},
	} {
		if err := validateResourceCeilings(resources); err == nil {
			t.Fatalf("non-canonical count must be rejected: %#v", resources)
		}
	}
}

func TestPrepareRejectsTrustedIdentifierInProposalText(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"request_clarification",
		"reason_code":"IDENTIFIER_ECHO",
		"reason":"The identifier dep-observed-001 needs more evidence.",
		"confidence":0.4
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected trusted identifier echo to be rejected")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
}

func TestPrepareRequiresTrustedCompletionEvidence(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	candidate := testCandidate()
	client := staticCompletionClient{completion: llmclient.Completion{
		Status:      "executed",
		Content:     `{}`,
		Provider:    candidate.Provider,
		CandidateID: "unexpected-candidate",
		ActualModel: candidate.ActualModel,
	}}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		candidate,
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected mismatched completion evidence to be rejected")
	}
	if result.Status != StatusModelUnavailable {
		t.Fatalf("expected %s, got %s", StatusModelUnavailable, result.Status)
	}
	if result.Evidence.Model.CandidateID != candidate.CandidateID {
		t.Fatal("untrusted completion metadata replaced configured candidate evidence")
	}
	if result.Handoff.NextEndpoint != "" {
		t.Fatal("rejected result must not advertise an AppDeploy next endpoint")
	}
}

func TestPrepareDoesNotExposeProviderFailureDetails(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	providerErr := errors.New("POST http://private-qwen.internal/v1 returned token=leaked")
	client := &capturingCompletionClient{
		err: providerErr,
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected provider failure")
	}
	if result.Status != StatusModelUnavailable {
		t.Fatalf("expected %s, got %s", StatusModelUnavailable, result.Status)
	}
	for _, privateDetail := range []string{"private-qwen.internal", "leaked"} {
		if strings.Contains(result.Decision.Reason, privateDetail) {
			t.Fatalf("public decision reason exposed %q", privateDetail)
		}
		if strings.Contains(err.Error(), privateDetail) {
			t.Fatalf("public stage error exposed %q", privateDetail)
		}
	}
	if !errors.Is(err, providerErr) {
		t.Fatal("internal cause should remain available through errors.Is")
	}
}
