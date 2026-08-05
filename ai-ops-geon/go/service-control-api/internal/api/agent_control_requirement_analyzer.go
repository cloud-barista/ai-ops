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

const agentControlRequirementSystemPrompt = `You are the bounded Requirement Analyzer for the KHU AI Application Automation Agent PoC. Convert only the supplied natural-language application request into exactly one JSON object with these fields: app_id, app_version, workload_type, cpu_cores, memory_mib, storage_gib, accelerator_type, accelerator_count, accelerator_memory_mib, replicas_min, replicas_max. Use integers for every numeric field. Use an empty accelerator_type and zero accelerator values when no accelerator is requested. Do not create VM IDs, provider IDs, credentials, secrets, shell commands, manifests, or deployment actions. Return JSON only.`

const requirementAnalyzerCandidateID = "qwen3.5-ops-planner"

type agentControlRequirementAnalyzer struct {
	config   ServerConfig
	client   agentControlCompletionClient
	fallback agentcontrol.LocalRequirementAnalyzer
}

func newAgentControlRequirementAnalyzer(
	config ServerConfig,
	client agentControlCompletionClient,
) agentControlRequirementAnalyzer {
	return agentControlRequirementAnalyzer{
		config:   config,
		client:   client,
		fallback: agentcontrol.LocalRequirementAnalyzer{},
	}
}

func (analyzer agentControlRequirementAnalyzer) Analyze(
	ctx context.Context,
	input agentcontrol.AutomationRunInput,
) (agentcontrol.RequirementAnalysisResult, error) {
	if input.InputType != agentcontrol.InputTypeNaturalLanguage {
		return analyzer.fallback.Analyze(ctx, input)
	}

	fallback, err := analyzer.fallback.Analyze(ctx, input)
	if err != nil {
		return agentcontrol.RequirementAnalysisResult{}, err
	}
	if analyzer.client == nil {
		return labelRequirementFallback(fallback), nil
	}
	candidateConfig, err := llmclient.LoadCandidateConfig(analyzer.config.LLMCandidatesPath)
	if err != nil {
		return labelRequirementFallback(fallback), nil
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, requirementAnalyzerCandidateID)
	if err != nil {
		return labelRequirementFallback(fallback), nil
	}
	completion, err := analyzer.client.Complete(
		ctx,
		candidate,
		agentControlRequirementSystemPrompt,
		"Application request: "+strings.TrimSpace(input.Request),
	)
	if err != nil {
		return labelRequirementFallback(fallback), nil
	}
	spec, err := parseStructuredAppSpec(completion.Content)
	if err != nil {
		return labelRequirementFallback(fallback), nil
	}
	result, err := analyzer.fallback.Analyze(ctx, agentcontrol.AutomationRunInput{
		InputType:   agentcontrol.InputTypeStructured,
		RequestedBy: input.RequestedBy,
		AppSpec:     &spec,
	})
	if err != nil {
		return labelRequirementFallback(fallback), nil
	}
	result.Mode = agentcontrol.AnalysisModeQwen
	result.Evidence = agentcontrol.RequirementAnalysisEvidence{
		Mode:        agentcontrol.AnalysisModeQwen,
		SourceInput: input.Request,
		CandidateID: candidate.CandidateID,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		LatencyMS:   completion.LatencyMS,
	}
	result.ApplicationProfile.Analysis.Confidence = 0.9
	return result, nil
}

func parseStructuredAppSpec(content string) (agentcontrol.StructuredAppSpec, error) {
	var spec agentcontrol.StructuredAppSpec
	decoder := json.NewDecoder(bytes.NewBufferString(stripReasoningFence(content)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&spec); err != nil {
		return spec, fmt.Errorf("parse structured App Spec: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return spec, fmt.Errorf("parse structured App Spec: multiple JSON values are not allowed")
		}
		return spec, fmt.Errorf("parse structured App Spec: %w", err)
	}
	return spec, nil
}

func labelRequirementFallback(
	result agentcontrol.RequirementAnalysisResult,
) agentcontrol.RequirementAnalysisResult {
	result.Mode = agentcontrol.AnalysisModeLocalRule
	result.Evidence.Mode = agentcontrol.AnalysisModeLocalRule
	result.Evidence.Assumptions = append(
		result.Evidence.Assumptions,
		"Qwen was unavailable or disabled; local rule analysis was used.",
	)
	result.ApplicationProfile.Analysis.Assumptions = append(
		result.ApplicationProfile.Analysis.Assumptions,
		"Qwen was unavailable or disabled; local rule analysis was used.",
	)
	return result
}
