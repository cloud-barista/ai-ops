package deploymentplanner

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

const manifestSystemPrompt = `You are the deployment requirement planner for the KHU AppDeploy API. Convert the user's application requirement into exactly one JSON DeploymentManifest. Do not select a VM, runtime adapter, credential, endpoint, command, container, or Kubernetes resource. Use schema_version deployment.khu.ai/v1alpha1 and kind DeploymentManifest. Preserve the supplied app_version_id and optional target_profile_id exactly. Choose accelerator none or nvidia and provide cpu, memory, gpu, and storage as strings. Return JSON only.`

type completionClient interface {
	Complete(context.Context, llmclient.Candidate, string, string) (llmclient.Completion, error)
}

type GenerateInput struct {
	NaturalLanguageRequest string         `json:"natural_language_request"`
	AppVersionID           string         `json:"app_version_id"`
	TargetProfileID        string         `json:"target_profile_id,omitempty"`
	RequestedBy            string         `json:"requested_by"`
	Parameters             map[string]any `json:"parameters,omitempty"`
}

type GenerateResult struct {
	ExecutionStatus string                       `json:"execution_status"`
	CandidateID     string                       `json:"candidate_id"`
	Provider        string                       `json:"provider"`
	ActualModel     string                       `json:"actual_model"`
	LatencyMS       int64                        `json:"latency_ms"`
	GuardValid      bool                         `json:"guard_valid"`
	GuardReason     string                       `json:"guard_reason"`
	Manifest        appdeploy.DeploymentManifest `json:"manifest"`
}

type Generator struct {
	client completionClient
}

func NewGenerator(client completionClient) Generator {
	return Generator{client: client}
}

func (generator Generator) Generate(ctx context.Context, candidate llmclient.Candidate, input GenerateInput) (GenerateResult, error) {
	result := GenerateResult{
		ExecutionStatus: "not_executed",
		CandidateID:     candidate.CandidateID,
		Provider:        candidate.Provider,
		ActualModel:     candidate.ActualModel,
		GuardReason:     "deployment manifest has not been validated",
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if generator.client == nil {
		return result, fmt.Errorf("LLM completion client is required")
	}
	if strings.TrimSpace(input.NaturalLanguageRequest) == "" {
		return result, fmt.Errorf("natural language deployment request is required")
	}
	if len(input.NaturalLanguageRequest) > 8000 {
		return result, fmt.Errorf("natural language deployment request exceeds 8000 characters")
	}
	if strings.TrimSpace(input.AppVersionID) == "" {
		return result, fmt.Errorf("app_version_id is required")
	}
	if strings.TrimSpace(input.RequestedBy) == "" {
		input.RequestedBy = "ai-ops-geon-planner"
	}

	promptInput := map[string]any{
		"natural_language_request": input.NaturalLanguageRequest,
		"app_version_id":           input.AppVersionID,
		"target_profile_id":        input.TargetProfileID,
		"required_manifest_shape": map[string]any{
			"schema_version": appdeploy.ManifestSchemaVersion,
			"kind":           appdeploy.ManifestKind,
			"spec": map[string]any{
				"app_version_id":    input.AppVersionID,
				"target_profile_id": input.TargetProfileID,
				"accelerator":       "none|nvidia",
				"resources": map[string]string{
					"cpu":     "positive integer string",
					"memory":  "Mi|Gi|Ti quantity",
					"gpu":     "non-negative integer string",
					"storage": "Mi|Gi|Ti quantity",
				},
			},
		},
	}
	promptJSON, err := json.Marshal(promptInput)
	if err != nil {
		return result, err
	}
	completion, err := generator.client.Complete(ctx, candidate, manifestSystemPrompt, "Deployment planning input: "+string(promptJSON))
	result.LatencyMS = completion.LatencyMS
	if err != nil {
		result.ExecutionStatus = "llm_failed"
		return result, fmt.Errorf("LLM deployment manifest call failed: %w", err)
	}

	manifest, err := parseManifest(completion.Content)
	if err != nil {
		result.ExecutionStatus = "rejected"
		result.GuardReason = "LLM output is not a valid DeploymentManifest JSON object"
		return result, err
	}
	manifest.Spec.RequestedBy = input.RequestedBy
	manifest.Spec.Parameters = copyMap(input.Parameters)
	result.Manifest = manifest
	if err := appdeploy.ValidateManifest(manifest, appdeploy.ManifestConstraints{
		AppVersionID:    input.AppVersionID,
		TargetProfileID: input.TargetProfileID,
		RequestedBy:     input.RequestedBy,
	}); err != nil {
		result.ExecutionStatus = "rejected"
		result.GuardReason = err.Error()
		return result, fmt.Errorf("deployment manifest Go Guard rejected the proposal: %w", err)
	}
	result.ExecutionStatus = "executed"
	result.GuardValid = true
	result.GuardReason = "deployment manifest matches the AppDeploy contract and trusted request fields"
	return result, nil
}

func parseManifest(content string) (appdeploy.DeploymentManifest, error) {
	var manifest appdeploy.DeploymentManifest
	decoder := json.NewDecoder(bytes.NewBufferString(stripMarkdownFence(content)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&manifest); err != nil {
		return manifest, fmt.Errorf("parse deployment manifest: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return manifest, fmt.Errorf("parse deployment manifest: multiple JSON values are not allowed")
		}
		return manifest, fmt.Errorf("parse deployment manifest: %w", err)
	}
	return manifest, nil
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

func copyMap(source map[string]any) map[string]any {
	if source == nil {
		return nil
	}
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}
