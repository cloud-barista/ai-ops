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
		ServerConfig{
			LLMCandidatesPath:       writeAgentControlCandidateConfig(t),
			RequirementAnalysisMode: requirementAnalysisModeQwen,
		},
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

func TestAgentControlRequirementAnalyzerKeepsDefaultScaleOutHeadroom(t *testing.T) {
	completion := &capturingRequirementCompletion{
		content: `{
			"app_id":"qwen-service",
			"app_version":"1.0.0",
			"workload_type":"LLM_INFERENCE",
			"cpu_cores":4,
			"memory_mib":8192,
			"storage_gib":20,
			"accelerator_type":"GPU",
			"accelerator_count":1,
			"accelerator_memory_mib":16384,
			"replicas_min":1,
			"replicas_max":1
		}`,
	}
	analyzer := newAgentControlRequirementAnalyzer(
		ServerConfig{
			LLMCandidatesPath:       writeAgentControlCandidateConfig(t),
			RequirementAnalysisMode: requirementAnalysisModeQwen,
		},
		completion,
	)

	result, err := analyzer.Analyze(context.Background(), agentcontrol.AutomationRunInput{
		InputType: agentcontrol.InputTypeNaturalLanguage,
		Request:   "GPU 1개, CPU 4코어로 AI 추론 서비스를 배포해 주세요.",
	})
	if err != nil {
		t.Fatalf("analyze with Qwen: %v", err)
	}
	deployment := result.ApplicationProfile.Requirements.Deployment
	if deployment.ReplicasMin != 1 || deployment.ReplicasMax != 2 {
		t.Fatalf("replica bounds = %d..%d, want 1..2", deployment.ReplicasMin, deployment.ReplicasMax)
	}
	if len(result.ApplicationProfile.Analysis.Assumptions) == 0 ||
		!strings.Contains(result.ApplicationProfile.Analysis.Assumptions[0], "scale-out") {
		t.Fatalf("normalization assumption missing: %#v", result.ApplicationProfile.Analysis.Assumptions)
	}
}

func TestAgentControlRequirementAnalyzerDoesNotFallbackAfterProviderFailure(t *testing.T) {
	analyzer := newAgentControlRequirementAnalyzer(
		ServerConfig{
			LLMCandidatesPath:       writeAgentControlCandidateConfig(t),
			RequirementAnalysisMode: requirementAnalysisModeQwen,
		},
		&capturingRequirementCompletion{err: errors.New("provider unavailable")},
	)

	_, err := analyzer.Analyze(context.Background(), agentcontrol.AutomationRunInput{
		InputType: agentcontrol.InputTypeNaturalLanguage,
		Request:   "CPU 2코어, 메모리 4GiB, 스토리지 20GiB 서비스 배포",
	})
	if err == nil {
		t.Fatal("expected provider failure, got nil")
	}
	if !strings.Contains(err.Error(), "qwen requirement analysis failed") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAgentControlRequirementAnalyzerUsesExplicitLocalRuleMode(t *testing.T) {
	analyzer := newAgentControlRequirementAnalyzer(
		ServerConfig{RequirementAnalysisMode: requirementAnalysisModeLocalRule},
		&capturingRequirementCompletion{err: errors.New("must not be called")},
	)

	result, err := analyzer.Analyze(context.Background(), agentcontrol.AutomationRunInput{
		InputType: agentcontrol.InputTypeNaturalLanguage,
		Request:   "CPU 2코어, 메모리 4GiB, 스토리지 20GiB 서비스 배포",
	})
	if err != nil {
		t.Fatalf("explicit local analysis: %v", err)
	}
	if result.Mode != agentcontrol.AnalysisModeLocalRule {
		t.Fatalf("analysis mode = %q, want local_rule", result.Mode)
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
