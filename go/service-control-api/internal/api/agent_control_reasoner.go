package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

const agentControlReasoningSystemPrompt = `You are the reasoning component of the KHU AI Application Automation Agent. Analyze only the supplied ApplicationProfile and ResourceRecommendation. Return exactly one JSON object with action (DEPLOY, REJECT, or RETRY), selected_candidate_id, reason, and confidence from 0 to 1. Use DEPLOY only when a listed feasible candidate satisfies all minimum requirements. Use RETRY when the resource recommendation must be regenerated. Use REJECT when the application requirements are invalid. Never invent a candidate ID or operational command. Return JSON only.`

type agentControlCompletionClient interface {
	Complete(context.Context, llmclient.Candidate, string, string) (llmclient.Completion, error)
}

type agentControlReasoner struct {
	config ServerConfig
	client agentControlCompletionClient
}

func newAgentControlReasoner(
	config ServerConfig,
	client agentControlCompletionClient,
) agentControlReasoner {
	return agentControlReasoner{config: config, client: client}
}

func (reasoner agentControlReasoner) Propose(
	ctx context.Context,
	candidateID string,
	input agentcontrol.ReasoningInput,
) (agentcontrol.ModelReasoningResult, error) {
	result := agentcontrol.ModelReasoningResult{
		ExecutionStatus: "not_executed",
		CandidateID:     candidateID,
	}
	if reasoner.client == nil {
		return result, fmt.Errorf("LLM completion client is required")
	}
	candidateConfig, err := llmclient.LoadCandidateConfig(reasoner.config.LLMCandidatesPath)
	if err != nil {
		return result, err
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, candidateID)
	if err != nil {
		return result, err
	}
	result.CandidateID = candidate.CandidateID
	result.Provider = candidate.Provider
	result.ActualModel = candidate.ActualModel

	prompt, err := json.Marshal(map[string]any{
		"application_profile":     input.ApplicationProfile,
		"resource_recommendation": input.ResourceRecommendation,
		"required_output": map[string]any{
			"action":                "DEPLOY|REJECT|RETRY",
			"selected_candidate_id": "candidate_id from resource_recommendation or empty",
			"reason":                "short evidence-based reason",
			"confidence":            "number from 0 to 1",
		},
	})
	if err != nil {
		return result, err
	}
	completion, err := reasoner.client.Complete(
		ctx,
		candidate,
		agentControlReasoningSystemPrompt,
		"Agent Control reasoning input: "+string(prompt),
	)
	result.LatencyMS = completion.LatencyMS
	if err != nil {
		result.ExecutionStatus = agentcontrol.ReasoningProviderUnavailable
		return result, err
	}
	proposal, err := parseReasoningProposal(completion.Content)
	if err != nil {
		result.ExecutionStatus = "rejected"
		return result, err
	}
	result.ExecutionStatus = agentcontrol.ReasoningExecutionExecuted
	result.Proposal = proposal
	return result, nil
}

func parseReasoningProposal(content string) (agentcontrol.ReasoningProposal, error) {
	var proposal agentcontrol.ReasoningProposal
	decoder := json.NewDecoder(bytes.NewBufferString(stripReasoningFence(content)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&proposal); err != nil {
		return proposal, fmt.Errorf("parse reasoning proposal: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return proposal, fmt.Errorf("parse reasoning proposal: multiple JSON values are not allowed")
		}
		return proposal, fmt.Errorf("parse reasoning proposal: %w", err)
	}
	return proposal, nil
}

func stripReasoningFence(content string) string {
	content = strings.TrimSpace(content)
	if !strings.HasPrefix(content, "```") {
		return content
	}
	firstNewline := strings.IndexByte(content, '\n')
	if firstNewline < 0 {
		return content
	}
	content = strings.TrimSpace(content[firstNewline+1:])
	content = strings.TrimSuffix(content, "```")
	return strings.TrimSpace(content)
}
