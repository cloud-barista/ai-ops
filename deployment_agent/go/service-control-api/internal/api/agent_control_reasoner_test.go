package api

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func TestAgentControlReasonerUsesConfiguredQwenCandidate(t *testing.T) {
	candidates := writeAgentControlCandidateConfig(t)
	completion := &capturingReasoningCompletion{
		content: `{
			"action": "DEPLOY",
			"selected_candidate_id": "candidate-api-001",
			"reason": "The candidate satisfies the requirements.",
			"confidence": 0.91
		}`,
	}
	reasoner := newAgentControlReasoner(
		ServerConfig{LLMCandidatesPath: candidates},
		completion,
	)
	input := agentcontrol.ReasoningInput{
		ApplicationProfile:     apiApplicationContextEnvelope().Data.ApplicationProfile,
		ResourceRecommendation: apiResourceRecommendationEnvelope().Data.ResourceRecommendation,
	}

	result, err := reasoner.Propose(context.Background(), "qwen3.5-ops-planner", input)
	if err != nil {
		t.Fatalf("propose: %v", err)
	}
	if result.Proposal.Action != agentcontrol.ActionDeploy ||
		result.Proposal.SelectedCandidateID != "candidate-api-001" {
		t.Fatalf("proposal = %#v", result.Proposal)
	}
	if result.ActualModel != "qwen3.5:4b" || result.Provider != "test-provider" {
		t.Fatalf("model metadata = %#v", result)
	}
	for _, required := range []string{"profile-api-001", "candidate-api-001", "DEPLOY", "REJECT", "RETRY"} {
		if !strings.Contains(completion.userPrompt+completion.systemPrompt, required) {
			t.Fatalf("reasoning prompt is missing %q", required)
		}
	}
}

func TestAgentControlReasonerRejectsNonContractOutput(t *testing.T) {
	reasoner := newAgentControlReasoner(
		ServerConfig{LLMCandidatesPath: writeAgentControlCandidateConfig(t)},
		&capturingReasoningCompletion{
			content: `{"action":"DEPLOY","reason":"ok","confidence":0.9,"shell_command":"rm -rf /"}`,
		},
	)

	_, err := reasoner.Propose(
		context.Background(),
		"qwen3.5-ops-planner",
		agentcontrol.ReasoningInput{
			ApplicationProfile:     apiApplicationContextEnvelope().Data.ApplicationProfile,
			ResourceRecommendation: apiResourceRecommendationEnvelope().Data.ResourceRecommendation,
		},
	)
	if err == nil {
		t.Fatal("unknown model output fields must be rejected")
	}
}

type capturingReasoningCompletion struct {
	content      string
	systemPrompt string
	userPrompt   string
}

func (completion *capturingReasoningCompletion) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	systemPrompt string,
	userPrompt string,
) (llmclient.Completion, error) {
	completion.systemPrompt = systemPrompt
	completion.userPrompt = userPrompt
	return llmclient.Completion{
		Status:      "executed",
		Content:     completion.content,
		LatencyMS:   25,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, nil
}

func writeAgentControlCandidateConfig(t *testing.T) string {
	return writeAgentControlCandidateConfigAt(t, "http://127.0.0.1:1/v1/chat/completions")
}

func writeAgentControlCandidateConfigAt(t *testing.T, endpoint string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "candidates.json")
	content := fmt.Sprintf(`{
		"candidates": [{
			"candidate_id": "qwen3.5-ops-planner",
			"role_label": "primary-ops-llm",
			"provider": "test-provider",
			"actual_model": "qwen3.5:4b",
			"endpoint": %q,
			"enabled": true,
			"json_mode": true,
			"reasoning_effort": "none",
			"timeout_seconds": 120
		}]
	}`, endpoint)
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write candidate config: %v", err)
	}
	return path
}
