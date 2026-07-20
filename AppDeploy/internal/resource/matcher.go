package resource

import (
	"context"
	"net/http"
	"strconv"
	"strings"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type Matcher struct{}

func NewMatcher() *Matcher {
	return &Matcher{}
}

func (m *Matcher) Match(ctx context.Context, app model.AppResponse, runtimeConfig model.RuntimeConfig, target model.TargetProfile) error {
	return m.match(ctx, app, runtimeConfig, target, app.AppSpec.Runtime.Accelerator, app.AppSpec.Resources)
}

// MatchManifest applies the resource envelope selected by the deployment
// planner. AppSpec requirements remain the fallback for legacy requests.
func (m *Matcher) MatchManifest(ctx context.Context, manifest model.DeploymentManifest, app model.AppResponse, runtimeConfig model.RuntimeConfig, target model.TargetProfile) error {
	return m.match(ctx, app, runtimeConfig, target, manifest.Spec.Accelerator, manifest.Spec.Resources)
}

func (m *Matcher) match(ctx context.Context, app model.AppResponse, runtimeConfig model.RuntimeConfig, target model.TargetProfile, accelerator string, resources model.Resources) error {
	_ = ctx
	_ = runtimeConfig // retained in the adapter boundary for adapter checks
	appRuntime := app.AppSpec.Runtime.Type
	// A mock Target is an adapter test boundary, not a physical capacity
	// boundary. It intentionally accepts any App runtime so callers can run
	// CPU/GPU/AI-Infra App Specs through the in-process mock adapter without
	// creating a separate runtime registration. The Target Profile still selects the
	// adapter and carries the runtime settings used by the orchestrator.
	if target.CSP == "mock" || target.Runtime.RuntimeType == "mock" {
		return nil
	}
	if accelerator == "nvidia" && appRuntime != "gpu" && appRuntime != "aiinfra" {
		return apperrors.New(model.ErrResourceInsufficient, "nvidia accelerator requires gpu or aiinfra app runtime", http.StatusBadRequest, false)
	}
	if accelerator != "" && accelerator != "none" {
		if target.Runtime.RuntimeType != "aiinfra" && target.Runtime.Accelerator != accelerator {
			return apperrors.New(model.ErrResourceInsufficient, "requested accelerator is not available on the selected target", http.StatusBadRequest, false)
		}
	}
	if appRuntime == "gpu" {
		if accelerator == "none" {
			return apperrors.New(model.ErrResourceInsufficient, "gpu app requires nvidia accelerator", http.StatusBadRequest, false)
		}
		if target.Runtime.RuntimeType != "gpu" && target.Runtime.RuntimeType != "aiinfra" {
			return apperrors.New(model.ErrGPURuntimeNotFound, "gpu app requires gpu or aiinfra target runtime", http.StatusBadRequest, false)
		}
		required := parseGPU(resources.GPU)
		if accelerator == "nvidia" && required < 1 {
			return apperrors.New(model.ErrResourceInsufficient, "nvidia accelerator requires at least one GPU", http.StatusBadRequest, false)
		}
		if required < 1 {
			return apperrors.New(model.ErrResourceInsufficient, "gpu app requires resources.gpu >= 1", http.StatusBadRequest, false)
		}
		if target.Runtime.RuntimeType == "gpu" {
			if target.GPU == nil || target.GPU.Count < required {
				return apperrors.New(model.ErrResourceInsufficient, "target gpu count is insufficient", http.StatusBadRequest, false)
			}
		}
		return nil
	}
	if appRuntime == "mock" {
		if runtimeConfig.AdapterType != "mock" || target.Runtime.RuntimeType != "mock" {
			return apperrors.New(model.ErrResourceInsufficient, "mock app requires mock runtime and target", http.StatusBadRequest, false)
		}
		return nil
	}
	if target.Runtime.RuntimeType != appRuntime {
		return apperrors.New(model.ErrResourceInsufficient, "target runtime does not match app runtime type", http.StatusBadRequest, false)
	}
	return nil
}

func parseGPU(value string) int {
	parsed, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || parsed < 1 {
		return 1
	}
	return parsed
}
