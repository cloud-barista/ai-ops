package deploymentplanner

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type staticCompletionClient struct {
	content string
	err     error
}

func (client staticCompletionClient) Complete(_ context.Context, candidate llmclient.Candidate, _ string, _ string) (llmclient.Completion, error) {
	return llmclient.Completion{
		Status:      "executed",
		Content:     client.content,
		LatencyMS:   25,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, client.err
}

func TestGeneratorCreatesValidatedDeploymentManifest(t *testing.T) {
	generator := NewGenerator(staticCompletionClient{content: validManifestJSON("appver-test", "target-gpu-001")})
	result, err := generator.Generate(context.Background(), testCandidate(), GenerateInput{
		NaturalLanguageRequest: "GPU 추론 서비스를 CPU 4개와 메모리 16Gi로 배포해줘.",
		AppVersionID:           "appver-test",
		TargetProfileID:        "target-gpu-001",
		RequestedBy:            "ai-ops-geon-planner",
		Parameters:             map[string]any{"port": float64(18080)},
	})
	if err != nil {
		t.Fatalf("generate manifest: %v", err)
	}
	if result.ExecutionStatus != "executed" || !result.GuardValid {
		t.Fatalf("unexpected generation result: %#v", result)
	}
	if result.Manifest.Spec.AppVersionID != "appver-test" || result.Manifest.Spec.TargetProfileID != "target-gpu-001" {
		t.Fatalf("trusted identifiers were not preserved: %#v", result.Manifest.Spec)
	}
	if result.Manifest.Spec.RequestedBy != "ai-ops-geon-planner" {
		t.Fatalf("trusted requester was not applied: %#v", result.Manifest.Spec)
	}
	if result.Manifest.Spec.Parameters["port"] != float64(18080) {
		t.Fatalf("trusted parameters were not applied: %#v", result.Manifest.Spec.Parameters)
	}
}

func TestGeneratorPreservesTrustedDeploymentRequirements(t *testing.T) {
	completion := strings.Replace(
		validManifestJSON("appver-other", "target-gpu-001"),
		`"accelerator":"nvidia"`,
		`"accelerator":""`,
		1,
	)
	client := &recordingCompletionClient{content: completion}
	requirements := &appdeploy.DeploymentRequirements{
		Runtime:     "gpu",
		Resources:   appdeploy.ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "1", Storage: "20Gi"},
		Accelerator: "nvidia",
		CostPolicy:  "min_cost",
	}
	result, err := NewGenerator(client).Generate(context.Background(), testCandidate(), GenerateInput{
		NaturalLanguageRequest: "Deploy the inference service.",
		AppVersionID:           "appver-test",
		TargetProfileID:        "target-gpu-001",
		RequestedBy:            "ai-ops-geon-planner",
		Requirements:           requirements,
	})
	if err != nil {
		t.Fatalf("generate manifest: %v", err)
	}
	if result.Manifest.Spec.AppVersionID != "appver-test" {
		t.Fatalf("untrusted app_version_id survived: %#v", result.Manifest.Spec)
	}
	if result.Manifest.Spec.Requirements == nil || !reflect.DeepEqual(*result.Manifest.Spec.Requirements, *requirements) {
		t.Fatalf("trusted requirements were not preserved: %#v", result.Manifest.Spec.Requirements)
	}
	if result.Manifest.Spec.Resources != requirements.Resources {
		t.Fatalf("spec resources were not normalized from requirements: %#v", result.Manifest.Spec.Resources)
	}
	if result.Manifest.Spec.Accelerator != requirements.Accelerator {
		t.Fatalf("spec accelerator was not normalized from requirements: %#v", result.Manifest.Spec)
	}
	if !strings.Contains(client.systemPrompt, "Preserve the supplied deployment requirements exactly.") ||
		!strings.Contains(client.userPrompt, `"cost_policy":"min_cost"`) {
		t.Fatalf("trusted requirements were not supplied to Qwen: system=%q user=%q", client.systemPrompt, client.userPrompt)
	}
}

func TestGeneratorRejectsSecretLikeRequirementsBeforePrompt(t *testing.T) {
	client := &recordingCompletionClient{content: validManifestJSON("appver-test", "target-gpu-001")}
	_, err := NewGenerator(client).Generate(context.Background(), testCandidate(), GenerateInput{
		NaturalLanguageRequest: "Deploy the inference service.",
		AppVersionID:           "appver-test",
		TargetProfileID:        "target-gpu-001",
		RequestedBy:            "ai-ops-geon-planner",
		Requirements: &appdeploy.DeploymentRequirements{
			Runtime: "gpu", Resources: appdeploy.ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "1", Storage: "20Gi"},
			SLO: map[string]any{"nested": map[string]any{"credential": "must-not-pass"}},
		},
	})
	if err == nil || !strings.Contains(err.Error(), "requirements.slo") {
		t.Fatalf("expected secret-like requirements rejection, got %v", err)
	}
	if client.calls != 0 || client.userPrompt != "" {
		t.Fatalf("secret-like requirements reached Qwen: calls=%d prompt=%q", client.calls, client.userPrompt)
	}
}

func TestGeneratorRejectsUntrustedOrMalformedOutput(t *testing.T) {
	tests := []struct {
		name     string
		content  string
		contains string
	}{
		{
			name:     "target substitution",
			content:  validManifestJSON("appver-test", "target-other"),
			contains: "target_profile_id",
		},
		{
			name: "unknown field",
			content: strings.Replace(
				validManifestJSON("appver-test", "target-gpu-001"),
				`"kind":"DeploymentManifest"`,
				`"kind":"DeploymentManifest","shell_command":"rm -rf /"`,
				1,
			),
			contains: "unknown field",
		},
		{
			name:     "malformed JSON",
			content:  `{"schema_version":`,
			contains: "parse deployment manifest",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			generator := NewGenerator(staticCompletionClient{content: test.content})
			result, err := generator.Generate(context.Background(), testCandidate(), GenerateInput{
				NaturalLanguageRequest: "GPU 서비스를 배포해줘.",
				AppVersionID:           "appver-test",
				TargetProfileID:        "target-gpu-001",
				RequestedBy:            "ai-ops-geon-planner",
			})
			if err == nil || !strings.Contains(err.Error(), test.contains) {
				t.Fatalf("expected error containing %q, got %v", test.contains, err)
			}
			if result.ExecutionStatus == "executed" && result.GuardValid {
				t.Fatalf("invalid output must not pass the guard: %#v", result)
			}
		})
	}
}

type recordingCompletionClient struct {
	calls        int
	content      string
	systemPrompt string
	userPrompt   string
}

func (client *recordingCompletionClient) Complete(_ context.Context, candidate llmclient.Candidate, systemPrompt string, userPrompt string) (llmclient.Completion, error) {
	client.calls++
	client.systemPrompt = systemPrompt
	client.userPrompt = userPrompt
	return llmclient.Completion{
		Status:      "executed",
		Content:     client.content,
		LatencyMS:   25,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, nil
}

func testCandidate() llmclient.Candidate {
	return llmclient.Candidate{
		CandidateID: "planner-test-llm",
		Provider:    "test-provider",
		ActualModel: "test-model",
		Enabled:     true,
		Endpoint:    "http://127.0.0.1/unused",
	}
}

func validManifestJSON(appVersionID string, targetProfileID string) string {
	return `{
		"schema_version":"deployment.khu.ai/v1alpha1",
		"kind":"DeploymentManifest",
		"metadata":{"name":"llm-inference-deployment"},
		"spec":{
			"app_version_id":"` + appVersionID + `",
			"target_profile_id":"` + targetProfileID + `",
			"accelerator":"nvidia",
			"resources":{"cpu":"4","memory":"16Gi","gpu":"1","storage":"20Gi"},
			"requested_by":"llm-output",
			"parameters":{}
		}
	}`
}
