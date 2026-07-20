package plannerguard

import "testing"

func TestValidateRequestApprovesAllowedDeploymentRequest(t *testing.T) {
	decision := ValidateRequest(Request{
		NaturalLanguageRequest: "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 VM에 배포해 주세요.",
		AppVersionID:           "appver-llm-v1",
		CandidateID:            "decision-model",
		RequestedBy:            "ai-ops-geon-planner",
	}, testPolicy())

	if !decision.Valid || decision.Status != "approved" {
		t.Fatalf("expected approved request, got %#v", decision)
	}
}

func TestValidateRequestRejectsUnauthorizedRequester(t *testing.T) {
	decision := ValidateRequest(Request{
		NaturalLanguageRequest: "GPU 추론 앱을 VM에 배포해 주세요.",
		AppVersionID:           "appver-llm-v1",
		CandidateID:            "decision-model",
		RequestedBy:            "unknown-caller",
	}, testPolicy())

	if decision.Valid || decision.Status != "rejected" || decision.Reason == "" {
		t.Fatalf("expected requester rejection, got %#v", decision)
	}
}

func TestValidateRequestRejectsOutOfScopeRuntimeRequest(t *testing.T) {
	decision := ValidateRequest(Request{
		NaturalLanguageRequest: "이 앱을 Kubernetes와 Helm으로 배포해 주세요.",
		AppVersionID:           "appver-llm-v1",
		CandidateID:            "decision-model",
		RequestedBy:            "ai-ops-geon-planner",
	}, testPolicy())

	if decision.Valid || decision.Status != "rejected" {
		t.Fatalf("expected out-of-scope request rejection, got %#v", decision)
	}
}

func TestValidateRequestRejectsNestedSecretParameter(t *testing.T) {
	decision := ValidateRequest(Request{
		NaturalLanguageRequest: "GPU 추론 앱을 VM에 배포해 주세요.",
		AppVersionID:           "appver-llm-v1",
		CandidateID:            "decision-model",
		RequestedBy:            "ai-ops-geon-planner",
		Parameters: map[string]any{
			"runtime": map[string]any{"api_token": "placeholder-value"},
		},
	}, testPolicy())

	if decision.Valid || decision.Status != "rejected" {
		t.Fatalf("expected secret parameter rejection, got %#v", decision)
	}
}

func TestValidateRequestAllowsNonSecretKeyContainingTokenWord(t *testing.T) {
	decision := ValidateRequest(Request{
		NaturalLanguageRequest: "GPU 추론 앱을 VM에 배포해 주세요.",
		AppVersionID:           "appver-llm-v1",
		CandidateID:            "decision-model",
		RequestedBy:            "ai-ops-geon-planner",
		Parameters: map[string]any{
			"tokenizer_name": "research-tokenizer",
		},
	}, testPolicy())

	if !decision.Valid {
		t.Fatalf("expected non-secret tokenizer parameter to pass, got %#v", decision)
	}
}

func testPolicy() Policy {
	return Policy{
		Version:                "v1",
		MaxRequestLength:       8000,
		AllowedRequesters:      []string{"ai-ops-geon-planner"},
		ForbiddenRequestTerms:  []string{"kubernetes", "helm", "container", "docker"},
		ForbiddenParameterKeys: []string{"password", "token", "secret", "credential", "api_key", "private_key"},
	}
}
