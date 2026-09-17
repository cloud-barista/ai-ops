package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

const (
	shadowPolicyModeEnv    = "KHU_POLICY_MODE"
	shadowPolicyURLEnv     = "KHU_POLICY_URL"
	shadowPolicyTimeoutEnv = "KHU_POLICY_TIMEOUT_SECONDS"
	shadowPolicyPath       = "/api/v1/khu-policy/evaluate"
)

type shadowPolicyResponse struct {
	Mode           string `json:"mode"`
	ModelVersion   string `json:"model_version"`
	DatasetSHA256  string `json:"dataset_sha256"`
	ArtifactSHA256 string `json:"artifact_sha256"`
	Proposal       struct {
		Action              string  `json:"action"`
		SelectedCandidateID string  `json:"selected_candidate_id"`
		DesiredReplicas     *int    `json:"desired_replicas"`
		ExpectedUtility     float64 `json:"expected_utility"`
		Confidence          float64 `json:"confidence"`
		OODRatio            float64 `json:"ood_ratio"`
		AbstainReason       string  `json:"abstain_reason"`
	} `json:"proposal"`
	Warnings []string `json:"warnings"`
}

func (handler restHandler) maybeEvaluateShadowPolicy(
	ctx context.Context,
	flow agentcontrol.Flow,
	phase string,
) agentcontrol.Flow {
	if !strings.EqualFold(strings.TrimSpace(os.Getenv(shadowPolicyModeEnv)), "shadow") {
		return flow
	}
	assessment := agentcontrol.ShadowPolicyAssessment{
		Phase:       phase,
		Mode:        "shadow",
		GuardStatus: agentcontrol.GuardNotApplied,
		GuardReason: "Shadow policy evidence never changes the deterministic Go decision.",
		CreatedAt:   time.Now().UTC().Format(time.RFC3339Nano),
	}
	assessment.RuleAction, assessment.RuleCandidateID = shadowRuleDecision(flow, phase)
	baseURL := strings.TrimRight(strings.TrimSpace(os.Getenv(shadowPolicyURLEnv)), "/")
	if baseURL == "" {
		assessment.ProposedAction = "ABSTAIN"
		assessment.GuardReason = "KHU_POLICY_URL is not configured; deterministic result retained."
		assessment.Warnings = []string{"policy_unavailable"}
		return handler.recordShadowAssessment(ctx, flow, assessment)
	}
	payload, err := shadowPolicyPayload(flow, phase)
	if err != nil {
		assessment.ProposedAction = "ABSTAIN"
		assessment.GuardReason = "Shadow policy input was incomplete; deterministic result retained."
		assessment.Warnings = []string{"policy_input_invalid", err.Error()}
		return handler.recordShadowAssessment(ctx, flow, assessment)
	}
	response, err := invokeShadowPolicy(ctx, baseURL+shadowPolicyPath, payload)
	if err != nil {
		assessment.ProposedAction = "ABSTAIN"
		assessment.GuardReason = "Shadow policy was unavailable; deterministic result retained."
		assessment.Warnings = []string{"policy_unavailable", err.Error()}
		return handler.recordShadowAssessment(ctx, flow, assessment)
	}
	assessment.ModelVersion = response.ModelVersion
	assessment.DatasetSHA256 = response.DatasetSHA256
	assessment.ArtifactSHA256 = response.ArtifactSHA256
	assessment.ProposedAction = response.Proposal.Action
	assessment.SelectedCandidateID = response.Proposal.SelectedCandidateID
	if response.Proposal.DesiredReplicas != nil {
		assessment.DesiredReplicas = *response.Proposal.DesiredReplicas
	}
	assessment.ExpectedUtility = response.Proposal.ExpectedUtility
	assessment.Confidence = response.Proposal.Confidence
	assessment.OODRatio = response.Proposal.OODRatio
	assessment.Warnings = append([]string(nil), response.Warnings...)
	if response.Proposal.AbstainReason != "" {
		assessment.Warnings = append(assessment.Warnings, response.Proposal.AbstainReason)
	}
	return handler.recordShadowAssessment(ctx, flow, assessment)
}

func (handler restHandler) recordShadowAssessment(
	ctx context.Context,
	flow agentcontrol.Flow,
	assessment agentcontrol.ShadowPolicyAssessment,
) agentcontrol.Flow {
	updated, err := handler.service.agentControl.RecordShadowPolicyAssessment(ctx, flow.CorrelationID, assessment)
	if err != nil {
		return flow
	}
	return updated
}

func shadowRuleDecision(flow agentcontrol.Flow, phase string) (string, string) {
	if phase == "operation" && flow.ScalingDecision != nil {
		return flow.ScalingDecision.Action, ""
	}
	if flow.Decision != nil {
		return flow.Decision.Action, flow.Decision.SelectedCandidateID
	}
	return "", ""
}

func shadowPolicyPayload(flow agentcontrol.Flow, phase string) (map[string]any, error) {
	if flow.ApplicationContext == nil || flow.ResourceRecommendation == nil {
		return nil, fmt.Errorf("application context and resource recommendation are required")
	}
	candidates := make([]map[string]any, 0, len(flow.ResourceRecommendation.Data.ResourceRecommendation.Candidates))
	for _, candidate := range flow.ResourceRecommendation.Data.ResourceRecommendation.Candidates {
		infrastructure := candidate.DesiredInfrastructure
		candidates = append(candidates, map[string]any{
			"candidate_id":          candidate.CandidateID,
			"node_count":            infrastructure.NodeCount,
			"cpu_cores":             infrastructure.CPUCoresPerNode,
			"cpu_available_cores":   infrastructure.CPUCoresPerNode,
			"memory_mib":            infrastructure.MemoryMiBPerNode,
			"memory_available_mib":  infrastructure.MemoryMiBPerNode,
			"storage_gib":           infrastructure.StorageGiBPerNode,
			"storage_available_gib": infrastructure.StorageGiBPerNode,
			"gpu_count":             infrastructure.Accelerator.Count,
			"gpu_memory_mib":        infrastructure.Accelerator.MemoryMiBMinPerDevice,
			"gpu_available_mib":     infrastructure.Accelerator.MemoryMiBMinPerDevice,
			"availability":          candidate.Scores.Availability,
			"cost_per_hour":         1 - candidate.Scores.CostEfficiency,
			"healthy":               candidate.Feasible,
		})
	}
	contextPayload := map[string]any{}
	if flow.DeploymentContext != nil {
		contextPayload = map[string]any{
			"deployment_ref":      flow.DeploymentContext.DeploymentRef,
			"current_spec_digest": flow.DeploymentContext.CurrentSpecDigest,
			"current_replicas":    flow.DeploymentContext.CurrentReplicas,
			"spec_drift":          flow.DeploymentContext.SpecDrift,
			"cooldown_active":     flow.DeploymentContext.CooldownActive,
		}
	}
	if flow.DeploymentPlan != nil && contextPayload["current_replicas"] == nil {
		contextPayload["current_replicas"] = flow.DeploymentPlan.InferenceConfiguration.Replicas
	}
	payload := map[string]any{
		"schema_version":          "khu-policy/v1",
		"phase":                   phase,
		"application_profile":     flow.ApplicationContext.Data.ApplicationProfile,
		"resource_recommendation": map[string]any{"candidates": candidates},
		"deployment_context":      contextPayload,
	}
	if phase == "operation" && flow.OptimizationFeedback != nil {
		payload["optimization_feedback"] = flow.OptimizationFeedback.Data.OptimizationFeedback
	}
	return payload, nil
}

func invokeShadowPolicy(ctx context.Context, endpoint string, payload map[string]any) (shadowPolicyResponse, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return shadowPolicyResponse{}, fmt.Errorf("marshal shadow policy request: %w", err)
	}
	timeout := 2 * time.Second
	if raw := strings.TrimSpace(os.Getenv(shadowPolicyTimeoutEnv)); raw != "" {
		seconds, parseErr := strconv.ParseFloat(raw, 64)
		if parseErr == nil && seconds > 0 {
			timeout = time.Duration(seconds * float64(time.Second))
		}
	}
	requestContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(requestContext, http.MethodPost, endpoint, bytes.NewReader(encoded))
	if err != nil {
		return shadowPolicyResponse{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: timeout}).Do(request)
	if err != nil {
		return shadowPolicyResponse{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return shadowPolicyResponse{}, fmt.Errorf("shadow policy returned HTTP %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return shadowPolicyResponse{}, err
	}
	var result shadowPolicyResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return shadowPolicyResponse{}, fmt.Errorf("invalid shadow policy response: %w", err)
	}
	if strings.TrimSpace(result.Proposal.Action) == "" {
		return shadowPolicyResponse{}, fmt.Errorf("shadow policy response omitted proposal.action")
	}
	return result, nil
}
