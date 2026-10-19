package llmop

import (
	"context"
	"errors"
	"net/http"

	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

// ProviderOptions keeps live completion disabled unless an integration layer
// makes an explicit, reviewable choice to allow up to two network completions.
type ProviderOptions struct {
	AllowLiveCompletion bool
}

// ReviewWithConfig resolves the caller-supplied candidate exactly and runs
// one explicitly authorized safeguard completion. It never runs the separate
// Manifest proposal completion and never returns an AppDeploy handoff.
func ReviewWithConfig(
	ctx context.Context,
	request Request,
	candidateConfigPath string,
	guardPolicyPath string,
	options ProviderOptions,
	httpClient *http.Client,
) (SafeguardStageResult, error) {
	return reviewWithConfig(
		ctx,
		request,
		candidateConfigPath,
		guardPolicyPath,
		NewNormalizer(),
		options,
		httpClient,
	)
}

// PrepareApprovedWithConfig resumes an exact ReviewWithConfig approval after
// trusted in-process orchestration has joined and enriched an approved geon
// Flow. It is a Go integration API, not an HTTP request contract: a route must
// never accept caller-authored stage or PlanningConstraints values. A future
// cross-process route must atomically verify a server-stored/HMAC-sealed
// continuation before calling this function.
func PrepareApprovedWithConfig(
	ctx context.Context,
	request Request,
	approvedStage SafeguardStageResult,
	candidateConfigPath string,
	guardPolicyPath string,
	options ProviderOptions,
	httpClient *http.Client,
) (Result, error) {
	return prepareApprovedWithConfigUsingNormalizer(
		ctx,
		request,
		approvedStage,
		candidateConfigPath,
		guardPolicyPath,
		NewNormalizer(),
		options,
		httpClient,
	)
}

// PrepareWithConfig resolves the caller-supplied candidate exactly; it does not
// rank or select a model. When explicitly authorized, the same bound candidate
// performs the safeguard review and the separate Manifest proposal completion.
func PrepareWithConfig(
	ctx context.Context,
	request Request,
	candidateConfigPath string,
	guardPolicyPath string,
	options ProviderOptions,
	httpClient *http.Client,
) (Result, error) {
	return prepareWithConfig(
		ctx,
		request,
		candidateConfigPath,
		guardPolicyPath,
		NewNormalizer(),
		options,
		httpClient,
	)
}

// prepareWithConfig permits a deterministic clock only inside this package's
// tests. Production callers use PrepareWithConfig and cannot replace the
// freshness clock.
func prepareWithConfig(
	ctx context.Context,
	request Request,
	candidateConfigPath string,
	guardPolicyPath string,
	normalizer Normalizer,
	options ProviderOptions,
	httpClient *http.Client,
) (Result, error) {
	configured, result, err := configureExecution(
		ctx,
		request,
		candidateConfigPath,
		guardPolicyPath,
		normalizer,
		options,
		httpClient,
	)
	if err != nil {
		return result, err
	}
	return configured.pipeline.prepareNormalized(
		ctx,
		configured.candidate,
		configured.guardPolicy,
		configured.request,
		result,
		configured.normalized,
		configured.prompt,
	)
}

// reviewWithConfig permits a deterministic freshness clock only for package
// tests, matching prepareWithConfig's production clock boundary.
func reviewWithConfig(
	ctx context.Context,
	request Request,
	candidateConfigPath string,
	guardPolicyPath string,
	normalizer Normalizer,
	options ProviderOptions,
	httpClient *http.Client,
) (SafeguardStageResult, error) {
	if request.Application.PlanningConstraints != nil {
		result := NewResult(request)
		rejectRequest(
			&result,
			"guard-first review requires planning constraints to be absent before trusted enrichment",
		)
		return newSafeguardStageResult(result), &StageError{
			Status: result.Status,
			Cause:  errors.New("pre-geon planning constraints must be absent"),
		}
	}
	configured, result, err := configureExecution(
		ctx,
		request,
		candidateConfigPath,
		guardPolicyPath,
		normalizer,
		options,
		httpClient,
	)
	if err != nil {
		return newSafeguardStageResult(result), err
	}
	return configured.pipeline.reviewStageNormalized(
		ctx,
		configured.candidate,
		configured.guardPolicy,
		configured.request,
		result,
		configured.normalized,
	)
}

func prepareApprovedWithConfigUsingNormalizer(
	ctx context.Context,
	request Request,
	approvedStage SafeguardStageResult,
	candidateConfigPath string,
	guardPolicyPath string,
	normalizer Normalizer,
	options ProviderOptions,
	httpClient *http.Client,
) (Result, error) {
	configured, result, err := configureExecution(
		ctx,
		request,
		candidateConfigPath,
		guardPolicyPath,
		normalizer,
		options,
		httpClient,
	)
	if err != nil {
		return result, err
	}
	return configured.pipeline.prepareApprovedNormalized(
		ctx,
		configured.candidate,
		configured.guardPolicy,
		configured.request,
		approvedStage,
		result,
		configured.normalized,
		configured.prompt,
	)
}

type configuredExecution struct {
	request     Request
	normalized  NormalizedContext
	prompt      []byte
	guardPolicy plannerguard.Policy
	candidate   llmclient.Candidate
	pipeline    SafeguardedPlanner
}

func configureExecution(
	ctx context.Context,
	request Request,
	candidateConfigPath string,
	guardPolicyPath string,
	normalizer Normalizer,
	options ProviderOptions,
	httpClient *http.Client,
) (configuredExecution, Result, error) {
	requestSnapshot, err := cloneRequest(request)
	if err != nil {
		result := NewResult(request)
		rejectRequest(&result, "request could not be snapshotted into the bounded JSON contract")
		return configuredExecution{}, result, &StageError{Status: result.Status, Cause: err}
	}
	request = requestSnapshot
	result := NewResult(request)

	guardPolicy, err := plannerguard.LoadPolicy(guardPolicyPath)
	if err != nil {
		result.Status = StatusConfigurationError
		result.Decision.Reason = "LLM operation policy configuration could not be loaded"
		return configuredExecution{}, result, &StageError{Status: result.Status, Cause: err}
	}
	result, normalized, prompt, err := preflightRequest(
		ctx,
		normalizer,
		guardPolicy,
		request,
	)
	if err != nil {
		return configuredExecution{}, result, err
	}
	candidateConfig, err := llmclient.LoadCandidateConfig(candidateConfigPath)
	if err != nil {
		rejectProviderConfiguration(
			&result,
			normalized,
			"Qwen candidate configuration could not be loaded",
		)
		return configuredExecution{}, result, &StageError{Status: result.Status, Cause: err}
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, request.CandidateID)
	if err != nil {
		rejectModel(&result, normalized, "configured Qwen candidate is unavailable")
		return configuredExecution{}, result, &StageError{
			Status: result.Status,
			Cause:  err,
		}
	}
	if !options.AllowLiveCompletion {
		rejectProviderConfiguration(&result, normalized, "live Qwen completion is disabled")
		return configuredExecution{}, result, &StageError{
			Status: result.Status,
			Cause:  errors.New("live Qwen completion requires explicit authorization"),
		}
	}
	client := llmclient.NewClient(httpClient)
	return configuredExecution{
		request:     request,
		normalized:  normalized,
		prompt:      prompt,
		guardPolicy: guardPolicy,
		candidate:   candidate,
		pipeline:    newSafeguardedPlanner(client, client, normalizer),
	}, result, nil
}

func rejectProviderConfiguration(
	result *Result,
	normalized NormalizedContext,
	publicReason string,
) {
	clearPreparedHandoff(result)
	result.Status = StatusConfigurationError
	result.Decision = Decision{
		Action:            "none",
		Reason:            publicReason,
		ObservationStatus: normalized.ObservationStatus,
	}
}
