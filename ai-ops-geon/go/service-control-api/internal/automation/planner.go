package automation

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"kyunghee-aiops/service-control-api/internal/llmclient"
)

const systemPrompt = `You are an AI application service-control planning agent. Return one compact JSON object only. The action value must exactly match one item from allowed_actions. Use only the supplied target_vm_id and required_capability. Do not invent credentials, endpoints, resources, or execution results.`

type DecisionContext struct {
	Workload            string              `json:"workload"`
	ServiceName         string              `json:"service_name"`
	TargetVMID          string              `json:"target_vm_id"`
	CompatibilityStatus string              `json:"compatibility_status"`
	Checks              []map[string]string `json:"checks"`
	Observations        map[string]any      `json:"observations,omitempty"`
	AllowedActions      []string            `json:"allowed_actions"`
	RequiredCapability  string              `json:"required_capability"`
}

type ActionProposal struct {
	Action             string         `json:"action"`
	Reason             string         `json:"reason"`
	Confidence         float64        `json:"confidence"`
	RequiredCapability string         `json:"required_capability"`
	TargetVMID         string         `json:"target_vm_id"`
	Parameters         map[string]any `json:"parameters,omitempty"`
}

type DecisionResult struct {
	ExecutionStatus string         `json:"decision_execution_status"`
	CandidateID     string         `json:"candidate_id"`
	Provider        string         `json:"provider"`
	ActualModel     string         `json:"actual_model"`
	LatencyMS       int64          `json:"latency_ms"`
	Proposal        ActionProposal `json:"proposal"`
}

type completionClient interface {
	Complete(ctx context.Context, candidate llmclient.Candidate, systemPrompt string, userPrompt string) (llmclient.Completion, error)
}

type Planner struct {
	client completionClient
}

func NewPlanner(client completionClient) Planner {
	return Planner{client: client}
}

func (planner Planner) Plan(ctx context.Context, candidate llmclient.Candidate, input DecisionContext) (DecisionResult, error) {
	result := DecisionResult{
		ExecutionStatus: "not_executed",
		CandidateID:     candidate.CandidateID,
		Provider:        candidate.Provider,
		ActualModel:     candidate.ActualModel,
	}
	if planner.client == nil {
		return result, fmt.Errorf("LLM completion client is required")
	}
	if strings.TrimSpace(input.TargetVMID) == "" {
		return result, fmt.Errorf("target VM id is required")
	}
	if len(input.AllowedActions) == 0 {
		return result, fmt.Errorf("at least one allowed action is required")
	}
	if strings.TrimSpace(input.RequiredCapability) == "" {
		return result, fmt.Errorf("required capability is required")
	}
	contextJSON, err := json.Marshal(input)
	if err != nil {
		return result, err
	}
	userPrompt := `Return one JSON object with exactly these six keys and this shape: {"action":"<one allowed action>","reason":"<short operational reason>","confidence":0.0,"required_capability":"ai_application_deployment_control","target_vm_id":"<supplied target VM ID>","parameters":{}}. Choose exactly one action string verbatim from allowed_actions. Do not return allow, deny, approve, or reject as the action. Do not repeat the Decision context or add keys such as checks or allowed_actions. If compatibility is provisional and there is no measured performance or incident observation, prefer observe_status. Decision context: ` + string(contextJSON)
	completion, err := planner.client.Complete(ctx, candidate, systemPrompt, userPrompt)
	result.LatencyMS = completion.LatencyMS
	if err != nil {
		result.ExecutionStatus = "llm_failed"
		return result, fmt.Errorf("LLM decision call failed: %w", err)
	}
	proposal, err := parseActionProposal(completion.Content)
	if err != nil {
		result.ExecutionStatus = "rejected"
		return result, err
	}
	result.Proposal = proposal
	if proposal.Confidence < 0 || proposal.Confidence > 1 {
		result.ExecutionStatus = "rejected"
		return result, fmt.Errorf("action proposal confidence must be between 0 and 1")
	}
	if proposal.TargetVMID != input.TargetVMID {
		result.ExecutionStatus = "rejected"
		return result, fmt.Errorf("action proposal target VM does not match the validated target VM")
	}
	result.ExecutionStatus = "executed"
	return result, nil
}

func parseActionProposal(content string) (ActionProposal, error) {
	var proposal ActionProposal
	content = stripMarkdownFence(content)
	decoder := json.NewDecoder(bytes.NewBufferString(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&proposal); err != nil {
		return proposal, fmt.Errorf("parse action proposal: %w", err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return proposal, fmt.Errorf("parse action proposal: %w", err)
	}
	if strings.TrimSpace(proposal.Action) == "" {
		return proposal, fmt.Errorf("parse action proposal: action is required")
	}
	if strings.TrimSpace(proposal.Reason) == "" {
		return proposal, fmt.Errorf("parse action proposal: reason is required")
	}
	if strings.TrimSpace(proposal.RequiredCapability) == "" {
		return proposal, fmt.Errorf("parse action proposal: required_capability is required")
	}
	if strings.TrimSpace(proposal.TargetVMID) == "" {
		return proposal, fmt.Errorf("parse action proposal: target_vm_id is required")
	}
	return proposal, nil
}

func stripMarkdownFence(content string) string {
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

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	err := decoder.Decode(&extra)
	if err == io.EOF {
		return nil
	}
	if err == nil {
		return fmt.Errorf("multiple JSON values are not allowed")
	}
	return err
}
