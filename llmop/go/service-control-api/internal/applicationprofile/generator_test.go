package applicationprofile

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"kyunghee-aiops/llmop-api/internal/llmclient"
	"kyunghee-aiops/llmop-api/internal/llmselection"
)

type completionStub struct {
	completion   llmclient.Completion
	completions  []llmclient.Completion
	err          error
	errors       []error
	calls        int
	system       string
	user         string
	candidateIDs []string
}

func (stub *completionStub) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	systemPrompt string,
	userPrompt string,
) (llmclient.Completion, error) {
	stub.calls++
	stub.system = systemPrompt
	stub.user = userPrompt
	stub.candidateIDs = append(stub.candidateIDs, candidate.CandidateID)
	index := stub.calls - 1
	if index < len(stub.completions) {
		var err error
		if index < len(stub.errors) {
			err = stub.errors[index]
		}
		return stub.completions[index], err
	}
	return stub.completion, stub.err
}

type selectorStub struct {
	result llmselection.SelectionResponse
	err    error
	policy string
}

func (stub *selectorStub) Select(
	_ context.Context,
	policy string,
) (llmselection.SelectionResponse, error) {
	stub.policy = policy
	return stub.result, stub.err
}

func TestGeneratorCreatesOnlyApplicationProfileFromLLMOutput(t *testing.T) {
	client := &completionStub{completion: llmclient.Completion{
		Status:      "executed",
		Content:     validProfileCompletion(),
		Provider:    "openai-compatible",
		ActualModel: "qwen-test",
		LatencyMS:   17,
	}}
	generator := NewGenerator(
		writeCandidateConfig(t, true),
		"profile-generator",
		client,
	)
	request := GenerateRequest{
		Request: "GPU 1개와 CPU 4코어, 메모리 8GiB가 필요한 온라인 추론 서비스",
		Application: ApplicationMetadata{
			AppID:      "chat-service",
			AppVersion: "1.2.0",
			Artifact: &Artifact{
				Type:       "OCI_IMAGE",
				URI:        "registry.example.org/chat-service:1.2.0",
				Entrypoint: []string{"/app/server"},
			},
		},
	}

	result, err := generator.Generate(context.Background(), request)
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	profile := result.ApplicationProfile
	if !strings.HasPrefix(profile.ProfileID, "profile-chat-service-") {
		t.Fatalf("profile_id = %q", profile.ProfileID)
	}
	if profile.AppID != request.Application.AppID || profile.AppVersion != request.Application.AppVersion {
		t.Fatalf("application identity = %s:%s", profile.AppID, profile.AppVersion)
	}
	if profile.Requirements.Compute.CPUCoresMin != 4 ||
		profile.Requirements.Accelerator.CountMin != 1 {
		t.Fatalf("requirements = %#v", profile.Requirements)
	}
	if profile.Artifact == nil || profile.Artifact.URI != request.Application.Artifact.URI {
		t.Fatalf("artifact = %#v", profile.Artifact)
	}
	if result.Analysis.Mode != AnalysisModeLLM || result.Analysis.LatencyMS != 17 || result.Analysis.Attempts != 1 {
		t.Fatalf("analysis = %#v", result.Analysis)
	}
	if client.calls != 1 || !strings.Contains(client.system, "Do not select a VM") {
		t.Fatalf("completion calls=%d system=%q", client.calls, client.system)
	}
	if strings.Contains(client.user, request.Application.Artifact.URI) {
		t.Fatalf("artifact URI must not enter the LLM prompt: %s", client.user)
	}
}

func TestGeneratorLabelsLocalRuleFallbackWhenModelIsDisabled(t *testing.T) {
	client := &completionStub{}
	generator := NewGenerator(writeCandidateConfig(t, false), "profile-generator", client)

	result, err := generator.Generate(context.Background(), GenerateRequest{
		Request: "CPU 2코어, 메모리 4GiB, 스토리지 20GiB CPU-only 추론 서비스",
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if client.calls != 0 {
		t.Fatalf("completion calls = %d, want 0", client.calls)
	}
	if result.Analysis.Mode != AnalysisModeLocalRule ||
		result.Analysis.FallbackReason != "candidate_unavailable" ||
		result.ApplicationProfile.Requirements.Accelerator.Required {
		t.Fatalf("fallback result = %#v", result)
	}
}

func TestGeneratorRetriesThenFallsBackForUnknownModelFields(t *testing.T) {
	client := &completionStub{completion: llmclient.Completion{
		Content:     strings.Replace(validProfileCompletion(), `"analysis":`, `"target_vm_id":"vm-001","analysis":`, 1),
		Provider:    "test",
		ActualModel: "test",
	}}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", client)

	result, err := generator.Generate(context.Background(), GenerateRequest{
		Request: "GPU 1개, CPU 4코어, 메모리 8GiB, 스토리지 20GiB 추론 서비스",
		Application: ApplicationMetadata{
			AppID:      "chat-service",
			AppVersion: "1.2.0",
		},
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if client.calls != 2 || result.Analysis.Mode != AnalysisModeLocalRule ||
		result.Analysis.Attempts != 2 || result.Analysis.FallbackReason != "invalid_model_output" {
		t.Fatalf("calls=%d result=%#v", client.calls, result)
	}
}

func TestGeneratorRetryCanRecoverWithValidJSON(t *testing.T) {
	client := &completionStub{completions: []llmclient.Completion{
		{Content: `{"not":"a profile"}`, LatencyMS: 3},
		{Content: validProfileCompletion(), Provider: "test", ActualModel: "qwen-test", LatencyMS: 5},
	}}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", client)
	result, err := generator.Generate(context.Background(), GenerateRequest{
		Request:     "GPU 1개와 CPU 4코어, 메모리 8GiB가 필요한 온라인 추론 서비스",
		Application: ApplicationMetadata{AppID: "chat-service", AppVersion: "1.2.0"},
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if result.Analysis.Mode != AnalysisModeLLM || result.Analysis.Attempts != 2 ||
		result.Analysis.LatencyMS != 8 || !strings.Contains(client.user, "Previous output") {
		t.Fatalf("analysis=%#v prompt=%q", result.Analysis, client.user)
	}
}

func TestGeneratorRetryExplainsInvalidRequestPattern(t *testing.T) {
	client := &completionStub{completions: []llmclient.Completion{
		{Content: strings.Replace(validProfileCompletion(), `"request_pattern": "ONLINE"`, `"request_pattern": "batch_or_streaming"`, 1)},
		{Content: validProfileCompletion(), Provider: "test", ActualModel: "qwen-test"},
	}}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", client)
	result, err := generator.Generate(context.Background(), GenerateRequest{
		Request:     "온라인 추론 서비스",
		Application: ApplicationMetadata{AppID: "chat-service", AppVersion: "1.2.0"},
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if result.Analysis.Mode != AnalysisModeLLM || result.Analysis.Attempts != 2 ||
		result.ApplicationProfile.Workload.RequestPattern != "ONLINE" {
		t.Fatalf("result = %#v", result)
	}
	if !strings.Contains(client.user, "workload.request_pattern must be ONLINE, BATCH, or STREAMING") {
		t.Fatalf("retry does not explain the rejected field and its permitted values: %q", client.user)
	}
	if strings.Contains(client.user, "batch_or_streaming") {
		t.Fatalf("retry must not echo the untrusted model output: %q", client.user)
	}
}

func TestGeneratorUsesPolicySelectedCandidate(t *testing.T) {
	client := &completionStub{completion: llmclient.Completion{
		Content:     validProfileCompletion(),
		Provider:    "test",
		ActualModel: "gemma-test",
	}}
	candidatesPath := filepath.Join(t.TempDir(), "candidates.json")
	if err := os.WriteFile(candidatesPath, []byte(`{
  "version":"1",
  "candidates":[{
    "candidate_id":"gemma3-profile-generator",
    "provider":"test",
    "actual_model":"gemma-test",
    "endpoint":"http://127.0.0.1:1/v1/chat/completions",
    "enabled":true
  }]
}`), 0o600); err != nil {
		t.Fatalf("write candidates: %v", err)
	}
	selector := &selectorStub{result: llmselection.SelectionResponse{
		SelectedCandidateID: "gemma3-profile-generator",
	}}
	generator := NewGenerator(candidatesPath, "unused-default", client)
	generator.Selector = selector
	result, err := generator.Generate(context.Background(), GenerateRequest{
		Request:         "CPU 4코어, 메모리 8GiB가 필요한 온라인 추론 서비스",
		SelectionPolicy: "cost_first",
		Application:     ApplicationMetadata{AppID: "chat-service", AppVersion: "1.2.0"},
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if selector.policy != "cost_first" || len(client.candidateIDs) != 1 ||
		client.candidateIDs[0] != "gemma3-profile-generator" ||
		result.Analysis.SelectionPolicy != "cost_first" {
		t.Fatalf("selector=%#v client=%#v result=%#v", selector, client, result)
	}
}

func TestGeneratorRejectsUnknownSelectionPolicy(t *testing.T) {
	selector := &selectorStub{err: llmselection.ErrUnknownPolicy}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", &completionStub{})
	generator.Selector = selector
	_, err := generator.Generate(context.Background(), GenerateRequest{
		Request:         "CPU 2코어와 메모리 4GiB 추론 서비스",
		SelectionPolicy: "unknown",
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("Generate() error = %v, want ErrInvalidRequest", err)
	}
}

func TestGeneratorMapsStructuredAppSpecWithoutLLM(t *testing.T) {
	client := &completionStub{}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", client)
	result, err := generator.Generate(context.Background(), GenerateRequest{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:          "batch-embedder",
			AppVersion:     "1.0.0",
			WorkloadType:   "EMBEDDING",
			RequestPattern: "BATCH",
			CPUCores:       8,
			MemoryMiB:      32768,
			StorageGiB:     50,
		},
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if client.calls != 0 || result.Analysis.Mode != AnalysisModeStructured ||
		result.ApplicationProfile.Workload.RequestPattern != "BATCH" ||
		result.ApplicationProfile.Requirements.Compute.CPUCoresMin != 8 {
		t.Fatalf("calls=%d result=%#v", client.calls, result)
	}
}

func TestGeneratorRejectsMissingStructuredFields(t *testing.T) {
	generator := NewGenerator(writeCandidateConfig(t, false), "profile-generator", &completionStub{})
	_, err := generator.Generate(context.Background(), GenerateRequest{
		InputType: InputTypeStructured,
		AppSpec:   &StructuredAppSpec{AppID: "incomplete"},
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("Generate() error = %v, want ErrInvalidRequest", err)
	}
}

func TestGeneratorRejectsCredentialLikeInputBeforeCompletion(t *testing.T) {
	client := &completionStub{}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", client)

	_, err := generator.Generate(context.Background(), GenerateRequest{
		Request: "배포 요청 password=do-not-send",
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("Generate() error = %v, want ErrInvalidRequest", err)
	}
	if client.calls != 0 {
		t.Fatalf("completion calls = %d, want 0", client.calls)
	}
}

func TestParseProfileRejectsFieldsOutsideExactLLMContract(t *testing.T) {
	valid := validProfileCompletion()
	tests := []struct {
		name    string
		content string
	}{
		{
			name: "duplicate field",
			content: strings.Replace(
				valid,
				`"app_id": "chat-service",`,
				`"app_id": "chat-service", "app_id": "other",`,
				1,
			),
		},
		{
			name:    "model-created profile id",
			content: strings.Replace(valid, `"app_id":`, `"profile_id":"profile-forged","app_id":`, 1),
		},
		{
			name:    "model-created artifact",
			content: strings.Replace(valid, `"workload":`, `"artifact":{"type":"OCI_IMAGE","uri":"forged"},"workload":`, 1),
		},
		{
			name:    "case variant field",
			content: strings.Replace(valid, `"app_id":`, `"App_ID":`, 1),
		},
		{
			name:    "missing required field",
			content: strings.Replace(valid, ",\n    \"expected_rps\": 5", "", 1),
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := parseProfile(test.content); err == nil {
				t.Fatal("parseProfile() error = nil")
			}
		})
	}
}

func TestGeneratorFallsBackWhenModelChangesCallerMetadata(t *testing.T) {
	client := &completionStub{completion: llmclient.Completion{
		Content:     validProfileCompletion(),
		Provider:    "test",
		ActualModel: "test",
	}}
	generator := NewGenerator(writeCandidateConfig(t, true), "profile-generator", client)

	result, err := generator.Generate(context.Background(), GenerateRequest{
		Request: "GPU 1개, CPU 4코어, 메모리 8GiB, 스토리지 20GiB 추론 서비스",
		Application: ApplicationMetadata{
			AppID:      "different-service",
			AppVersion: "1.2.0",
		},
	})
	if err != nil {
		t.Fatalf("Generate() error = %v", err)
	}
	if client.calls != 2 || result.Analysis.Mode != AnalysisModeLocalRule ||
		result.ApplicationProfile.AppID != "different-service" {
		t.Fatalf("calls=%d result=%#v", client.calls, result)
	}
}

func writeCandidateConfig(t *testing.T, enabled bool) string {
	t.Helper()
	content := `{
  "version": "1",
  "candidates": [{
    "candidate_id": "profile-generator",
    "provider": "openai-compatible",
    "actual_model": "qwen-test",
    "endpoint": "http://127.0.0.1:1/v1/chat/completions",
    "enabled": ` + map[bool]string{true: "true", false: "false"}[enabled] + `
  }]
}`
	path := filepath.Join(t.TempDir(), "candidates.json")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write candidate config: %v", err)
	}
	return path
}

func validProfileCompletion() string {
	return `{
  "app_id": "chat-service",
  "app_version": "1.2.0",
  "workload": {
    "task_type": "LLM_INFERENCE",
    "request_pattern": "ONLINE",
    "expected_rps": 5
  },
  "requirements": {
    "compute": {
      "cpu_cores_min": 4,
      "memory_mib_min": 8192,
      "storage_gib_min": 20
    },
    "accelerator": {
      "required": true,
      "type": "GPU",
      "count_min": 1,
      "memory_mib_min_per_device": 16384
    },
    "deployment": {
      "replicas_min": 1,
      "replicas_max": 2,
      "isolation": "ONE_MAJOR_APP_PER_VM"
    },
    "slo": {
      "latency_p95_ms_max": 0,
      "throughput_rps_min": 0
    },
    "cost": {
      "currency": "",
      "cost_per_hour_max": 0
    }
  },
  "analysis": {
    "confidence": 0.91,
    "assumptions": ["Storage was inferred for the online inference workload."],
    "missing_fields": [],
    "warnings": []
  }
}`
}
