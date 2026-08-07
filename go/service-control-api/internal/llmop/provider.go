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
	requestSnapshot, err := cloneRequest(request)
	if err != nil {
		result := NewResult(request)
		rejectRequest(&result, "request could not be snapshotted into the bounded JSON contract")
		return result, &StageError{Status: result.Status, Cause: err}
	}
	request = requestSnapshot
	result := NewResult(request)

	guardPolicy, err := plannerguard.LoadPolicy(guardPolicyPath)
	if err != nil {
		result.Status = StatusConfigurationError
		result.Decision.Reason = "LLM operation policy configuration could not be loaded"
		return result, &StageError{Status: result.Status, Cause: err}
	}
	result, normalized, prompt, err := preflightRequest(
		ctx,
		normalizer,
		guardPolicy,
		request,
	)
	if err != nil {
		return result, err
	}
	candidateConfig, err := llmclient.LoadCandidateConfig(candidateConfigPath)
	if err != nil {
		rejectProviderConfiguration(
			&result,
			normalized,
			"Qwen candidate configuration could not be loaded",
		)
		return result, &StageError{Status: result.Status, Cause: err}
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, request.CandidateID)
	if err != nil {
		rejectModel(&result, normalized, "configured Qwen candidate is unavailable")
		return result, &StageError{
			Status: result.Status,
			Cause:  err,
		}
	}
	if !options.AllowLiveCompletion {
		rejectProviderConfiguration(&result, normalized, "live Qwen completion is disabled")
		return result, &StageError{
			Status: result.Status,
			Cause:  errors.New("live Qwen completion requires explicit authorization"),
		}
	}
	client := llmclient.NewClient(httpClient)
	return newSafeguardedPlanner(client, client, normalizer).prepareNormalized(
		ctx,
		candidate,
		guardPolicy,
		request,
		result,
		normalized,
		prompt,
	)
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
