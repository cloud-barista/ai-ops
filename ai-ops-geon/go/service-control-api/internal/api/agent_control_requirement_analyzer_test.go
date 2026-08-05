package api

import (
	"context"
	"errors"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func TestAgentControlRequirementAnalyzerUsesConfiguredQwen(t *testing.T) {
	completion := &capturingRequirementCompletion{
		content: `{
			"app_id":"qwen-service",
			"app_version":"1.0.0",
			"workload_type":"LLM_INFERENCE",
			"cpu_cores":4,
			"memory_mib":16384,
			"storage_gib":20,
			"accelerator_type":"GPU",
			"accelerator_count":1,
			"accelerator_memory_mib":16384,
			"replicas_min":1,
			"replicas_max":2
		}`,
	}
	analyzer := newAgentControlRequirementAnalyzer(
		ServerConfig{LLMCandidatesPath: writeAgentControlCandidateConfig(t)},
		completion,
	)

	result, err := analyzer.Analyze(context.Background(), agentcontrol.AutomationRunInput{
		InputType: agentcontrol.InputTypeNaturalLanguage,
		Request:   "Qwen 추론 서비스를 GPU 1개로 배포해 주세요.",
	})
	if err != nil {
		t.Fatalf("analyze with Qwen: %v", err)
	}
	if result.Mode != agentcontrol.AnalysisModeQwen {
		t.Fatalf("mode = %q, want qwen", result.Mode)
	}
	if result.ApplicationProfile.Requirements.Compute.CPUCoresMin != 4 ||
		result.ApplicationProfile.Requirements.Accelerator.CountMin != 1 {
		t.Fatalf("Qwen requirements = %#v", result.ApplicationProfile.Requirements)
	}
	if !strings.Contains(completion.systemPrompt+completion.userPrompt, "memory_mib") {
		t.Fatal("Qwen prompt does not define the structured App Spec contract")
	}
}

func TestAgentControlRequirementAnalyzerLabelsLocalFallback(t *testing.T) {
	analyzer := newAgentControlRequirementAnalyzer(
		ServerConfig{LLMCandidatesPath: writeAgentControlCandidateConfig(t)},
		&capturingRequirementCompletion{err: errors.New("provider unavailable")},
	)

	result, err := analyzer.Analyze(context.Background(), agentcontrol.AutomationRunInput{
		InputType: agentcontrol.InputTypeNaturalLanguage,
		Request:   "CPU 2코어, 메모리 4GiB, 스토리지 20GiB 서비스 배포",
	})
	if err != nil {
		t.Fatalf("fallback analysis: %v", err)
	}
	if result.Mode != agentcontrol.AnalysisModeLocalRule {
		t.Fatalf("fallback mode = %q, want local_rule", result.Mode)
	}
	found := false
	for _, assumption := range result.Evidence.Assumptions {
		if strings.Contains(assumption, "Qwen") {
			found = true
		}
	}
	if !found {
		t.Fatalf("Qwen fallback was not disclosed: %#v", result.Evidence)
	}
}

type capturingRequirementCompletion struct {
	content      string
	err          error
	systemPrompt string
	userPrompt   string
}

func (completion *capturingRequirementCompletion) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	systemPrompt string,
	userPrompt string,
) (llmclient.Completion, error) {
	completion.systemPrompt = systemPrompt
	completion.userPrompt = userPrompt
	if completion.err != nil {
		return llmclient.Completion{}, completion.err
	}
	return llmclient.Completion{
		Status:      "executed",
		Content:     completion.content,
		LatencyMS:   30,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, nil
}
