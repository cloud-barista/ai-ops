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

const agentControlRequirementSystemPrompt = `You are the bounded Requirement Analyzer for the KHU AI Application Automation Agent PoC. Convert only the supplied natural-language application request into exactly one JSON object with these fields: app_id, app_version, workload_type, cpu_cores, memory_mib, storage_gib, accelerator_type, accelerator_count, accelerator_memory_mib, replicas_min, replicas_max. Use integers for every numeric field. When the request does not specify replica bounds, use replicas_min=1 and replicas_max=2 so one bounded scale-out step remains available. Use an empty accelerator_type and zero accelerator values when no accelerator is requested. Do not create VM IDs, provider IDs, credentials, secrets, shell commands, manifests, or deployment actions. Return JSON only.`

const requirementAnalyzerCandidateID = "qwen3.5-ops-planner"

type agentControlRequirementAnalyzer struct {
	config        ServerConfig
	client        agentControlCompletionClient
	localAnalyzer agentcontrol.LocalRequirementAnalyzer
}

func newAgentControlRequirementAnalyzer(
	config ServerConfig,
	client agentControlCompletionClient,
) agentControlRequirementAnalyzer {
	return agentControlRequirementAnalyzer{
		config:        config,
		client:        client,
		localAnalyzer: agentcontrol.LocalRequirementAnalyzer{},
	}
}

func (analyzer agentControlRequirementAnalyzer) Analyze(
	ctx context.Context,
	input agentcontrol.AutomationRunInput,
) (agentcontrol.RequirementAnalysisResult, error) {
	if input.InputType != agentcontrol.InputTypeNaturalLanguage {
		return analyzer.localAnalyzer.Analyze(ctx, input)
	}
	if analyzer.config.RequirementAnalysisMode == requirementAnalysisModeLocalRule {
		return analyzer.localAnalyzer.Analyze(ctx, input)
	}
	if analyzer.config.RequirementAnalysisMode != requirementAnalysisModeQwen {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"requirement analysis mode is not configured",
		)
	}
	if analyzer.client == nil {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"qwen requirement analysis failed: completion client is not configured",
		)
	}
	candidateConfig, err := llmclient.LoadCandidateConfig(analyzer.config.LLMCandidatesPath)
	if err != nil {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"qwen requirement analysis failed: load candidate configuration: %w",
			err,
		)
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, requirementAnalyzerCandidateID)
	if err != nil {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"qwen requirement analysis failed: select candidate: %w",
			err,
		)
	}
	completion, err := analyzer.client.Complete(
		ctx,
		candidate,
		agentControlRequirementSystemPrompt,
		"Application request: "+strings.TrimSpace(input.Request),
	)
	if err != nil {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"qwen requirement analysis failed: complete request: %w",
			err,
		)
	}
	spec, err := parseStructuredAppSpec(completion.Content)
	if err != nil {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"qwen requirement analysis failed: %w",
			err,
		)
	}
	replicaBoundsNormalized := normalizeNaturalLanguageReplicaBounds(&spec)
	result, err := analyzer.localAnalyzer.Analyze(ctx, agentcontrol.AutomationRunInput{
		InputType:   agentcontrol.InputTypeStructured,
		RequestedBy: input.RequestedBy,
		AppSpec:     &spec,
	})
	if err != nil {
		return agentcontrol.RequirementAnalysisResult{}, fmt.Errorf(
			"qwen requirement analysis failed: validate structured result: %w",
			err,
		)
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
	if replicaBoundsNormalized {
		const assumption = "Maximum replica count defaulted to 2 for one bounded scale-out step."
		result.Evidence.Assumptions = append(result.Evidence.Assumptions, assumption)
		result.ApplicationProfile.Analysis.Assumptions = append(
			result.ApplicationProfile.Analysis.Assumptions,
			assumption,
		)
	}
	return result, nil
}

func normalizeNaturalLanguageReplicaBounds(
	spec *agentcontrol.StructuredAppSpec,
) bool {
	normalized := false
	if spec.ReplicasMin <= 0 {
		spec.ReplicasMin = 1
		normalized = true
	}
	if spec.ReplicasMax < 2 {
		spec.ReplicasMax = 2
		normalized = true
	}
	if spec.ReplicasMax < spec.ReplicasMin {
		spec.ReplicasMax = spec.ReplicasMin
		normalized = true
	}
	return normalized
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
