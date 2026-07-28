package api

import (
	"context"
	"encoding/json"
	"fmt"

	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type manifestAgentExecutor struct {
	candidatesPath string
	generator      controlRunManifestGenerator
}

func newManifestAgentExecutor(
	candidatesPath string,
	generator controlRunManifestGenerator,
) agentExecutor {
	return &manifestAgentExecutor{
		candidatesPath: candidatesPath,
		generator:      generator,
	}
}

func (executor *manifestAgentExecutor) Execute(
	ctx context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	result := AgentExecutionResult{
		RunID:  request.RunID,
		Agent:  agent.Name,
		Status: "failed",
		Proposal: AgentProposal{
			Action: request.Action,
		},
		DomainValidation: "pending_manifest_guard",
	}
	if executor == nil || executor.generator == nil {
		return result, fmt.Errorf("deployment Manifest generator is required")
	}

	input, err := decodeManifestAgentInput(request.Input)
	if err != nil {
		result.Message = err.Error()
		return result, err
	}
	candidates, err := llmclient.LoadCandidateConfig(executor.candidatesPath)
	if err != nil {
		result.Message = err.Error()
		return result, err
	}
	candidate, err := llmclient.FindEnabledCandidate(candidates, input.CandidateID)
	if err != nil {
		result.Message = err.Error()
		return result, err
	}
	generation, generationErr := executor.generator.Generate(ctx, candidate, deploymentplanner.GenerateInput{
		NaturalLanguageRequest: input.NaturalLanguageRequest,
		AppVersionID:           input.AppVersionID,
		TargetProfileID:        input.TargetProfileID,
		RequestedBy:            input.RequestedBy,
		Parameters:             input.Parameters,
		Requirements:           input.Requirements,
	})
	result.Generation = &generation
	result.Manifest = &generation.Manifest
	result.LatencyMS = generation.LatencyMS
	result.Evidence = map[string]any{
		"candidate_id": generation.CandidateID,
		"actual_model": generation.ActualModel,
	}
	if generationErr != nil {
		result.Message = generationErr.Error()
		return result, generationErr
	}
	result.Status = "completed"
	result.Message = "Qwen generated a DeploymentManifest candidate"
	result.Result = map[string]any{
		"kind":           generation.Manifest.Kind,
		"app_version_id": generation.Manifest.Spec.AppVersionID,
	}
	return result, nil
}

func decodeManifestAgentInput(values map[string]any) (CreateControlRunRequest, error) {
	var request CreateControlRunRequest
	content, err := json.Marshal(values)
	if err != nil {
		return request, fmt.Errorf("encode Manifest Agent input: %w", err)
	}
	if err := json.Unmarshal(content, &request); err != nil {
		return request, fmt.Errorf("decode Manifest Agent input: %w", err)
	}
	if request.NaturalLanguageRequest == "" || request.AppVersionID == "" || request.CandidateID == "" {
		return request, fmt.Errorf(
			"Manifest Agent input requires natural_language_request, app_version_id, and candidate_id",
		)
	}
	return request, nil
}
