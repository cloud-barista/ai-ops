package api

import (
	"context"
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/automation"
	"kyunghee-aiops/service-control-api/internal/autonomy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

const (
	autonomyAgentName          = "AIApplicationAutomationAgent"
	autonomyCandidateID        = "qwen3.5-ops-planner"
	autonomyRequiredCapability = "ai_application_deployment_control"
)

var autonomyAllowedActions = []string{
	string(autonomy.ActionObserve),
	string(autonomy.ActionScaleOut),
	string(autonomy.ActionRestart),
	string(autonomy.ActionRollback),
	string(autonomy.ActionStop),
}

type autonomyDecisionPlanner struct {
	config  ServerConfig
	planner automation.Planner
}

func newAutonomyDecisionPlanner(config ServerConfig, planner automation.Planner) autonomyDecisionPlanner {
	return autonomyDecisionPlanner{config: config, planner: planner}
}

func (planner autonomyDecisionPlanner) Plan(ctx context.Context, input autonomy.DecisionInput) (autonomy.Decision, error) {
	candidateConfig, err := llmclient.LoadCandidateConfig(planner.config.LLMCandidatesPath)
	if err != nil {
		return autonomy.Decision{}, err
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, autonomyCandidateID)
	if err != nil {
		return autonomy.Decision{}, err
	}
	targetProfileID := strings.TrimSpace(input.Deployment.TargetProfileID)
	if targetProfileID == "" {
		targetProfileID = strings.TrimSpace(input.Deployment.Manifest.Spec.TargetProfileID)
	}
	if targetProfileID == "" {
		return autonomy.Decision{}, fmt.Errorf("deployment Target Profile is required for an autonomy decision")
	}
	checks := make([]map[string]string, 0, len(input.Evaluation.Violations))
	for _, violation := range input.Evaluation.Violations {
		checks = append(checks, map[string]string{"code": violation.Code, "status": "violated", "reason": violation.Reason})
	}
	decisionContext := automation.DecisionContext{
		Workload:            "llm-chat-inference",
		ServiceName:         input.Deployment.AppVersionID,
		TargetVMID:          targetProfileID,
		CompatibilityStatus: string(input.Evaluation.Status),
		Checks:              checks,
		AllowedActions:      append([]string(nil), autonomyAllowedActions...),
		RequiredCapability:  autonomyRequiredCapability,
		Observations: map[string]any{
			"deployment_id":          input.Deployment.DeploymentID,
			"deployment_status":      input.Deployment.Status,
			"target_profile_id":      targetProfileID,
			"evidence_fresh":         input.Evaluation.EvidenceFresh,
			"failure_evidence":       input.Evaluation.FailureEvidence,
			"consecutive_violations": input.Evaluation.ConsecutiveViolations,
			"slo":                    input.Config.SLO,
			"violations":             input.Evaluation.Violations,
		},
	}
	planned, err := planner.planner.Plan(ctx, candidate, decisionContext)
	if err != nil {
		return autonomy.Decision{}, err
	}
	proposal := planned.Proposal
	if !contains(autonomyAllowedActions, proposal.Action) {
		return autonomy.Decision{}, fmt.Errorf("Qwen proposed an Action outside the autonomy allowlist")
	}
	if proposal.RequiredCapability != autonomyRequiredCapability {
		return autonomy.Decision{}, fmt.Errorf("Qwen proposal required_capability does not match the autonomy capability")
	}
	if containsSecretLikeKey(proposal.Parameters) {
		return autonomy.Decision{}, fmt.Errorf("Qwen proposal contains a forbidden secret-shaped parameter")
	}
	return autonomy.Decision{
		Action: autonomy.Action(proposal.Action), Reason: proposal.Reason, Confidence: proposal.Confidence,
		Raw: map[string]any{"candidate_id": planned.CandidateID, "provider": planned.Provider, "actual_model": planned.ActualModel, "latency_ms": planned.LatencyMS},
	}, nil
}

type autonomyActionAuthorizer struct {
	config ServerConfig
}

func newAutonomyActionAuthorizer(config ServerConfig) autonomyActionAuthorizer {
	return autonomyActionAuthorizer{config: config}
}

func (authorizer autonomyActionAuthorizer) Validate(ctx context.Context, action string) (bool, string, error) {
	if err := ensureContext(ctx); err != nil {
		return false, "", err
	}
	registry, err := loadAgentRegistry(authorizer.config.path("config", "agent_registry.json"))
	if err != nil {
		return false, "", err
	}
	agent, err := findAgent(registry.Agents, autonomyAgentName)
	if err != nil {
		return false, "", err
	}
	if !agent.Enabled {
		return false, "the automation Agent is disabled in Agent Registry", nil
	}
	if !contains(agent.BoundedActions, action) || !contains(autonomyAllowedActions, action) {
		return false, "the proposed Action is not registered in the Agent Registry bounded action set", nil
	}
	return true, "the Agent Registry authorizes this bounded Action", nil
}

func containsSecretLikeKey(value any) bool {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			normalized := strings.ToLower(strings.ReplaceAll(strings.ReplaceAll(key, "-", "_"), " ", "_"))
			for _, fragment := range []string{"password", "passwd", "secret", "private_key", "api_key", "access_key", "credential", "token"} {
				if strings.Contains(normalized, fragment) {
					return true
				}
			}
			if containsSecretLikeKey(child) {
				return true
			}
		}
	case []any:
		for _, child := range typed {
			if containsSecretLikeKey(child) {
				return true
			}
		}
	}
	return false
}
