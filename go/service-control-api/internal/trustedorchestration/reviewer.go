package trustedorchestration

import (
	"context"
	"fmt"
	"net/http"
	"strings"

	"kyunghee-aiops/service-control-api/internal/llmop"
)

type ConfigReviewer struct {
	CandidateConfigPath string
	GuardPolicyPath     string
	AllowLiveCompletion bool
	HTTPClient          *http.Client
}

func (reviewer ConfigReviewer) Review(
	ctx context.Context,
	request llmop.Request,
) (llmop.SafeguardStageResult, error) {
	if strings.TrimSpace(reviewer.CandidateConfigPath) == "" {
		return llmop.SafeguardStageResult{}, fmt.Errorf("candidate config path is required")
	}
	if strings.TrimSpace(reviewer.GuardPolicyPath) == "" {
		return llmop.SafeguardStageResult{}, fmt.Errorf("guard policy path is required")
	}
	return llmop.ReviewWithConfig(
		ctx,
		request,
		reviewer.CandidateConfigPath,
		reviewer.GuardPolicyPath,
		llmop.ProviderOptions{AllowLiveCompletion: reviewer.AllowLiveCompletion},
		reviewer.HTTPClient,
	)
}
