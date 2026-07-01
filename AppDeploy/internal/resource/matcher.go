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

func (m *Matcher) Match(ctx context.Context, app model.AppResponse, runtimeProfile model.RuntimeProfile, target model.TargetProfile) error {
	appRuntime := app.AppSpec.Runtime.Type
	if appRuntime == "gpu" {
		if runtimeProfile.RuntimeType != "gpu" && runtimeProfile.RuntimeType != "aiinfra" {
			return apperrors.New(model.ErrResourceInsufficient, "gpu app requires gpu or aiinfra runtime profile", http.StatusBadRequest, false)
		}
		if target.Runtime.RuntimeType != "gpu" && target.Runtime.RuntimeType != "aiinfra" {
			return apperrors.New(model.ErrGPURuntimeNotFound, "gpu app requires gpu or aiinfra target runtime", http.StatusBadRequest, false)
		}
		required := parseGPU(app.AppSpec.Resources.GPU)
		if target.Runtime.RuntimeType == "gpu" {
			if target.GPU == nil || target.GPU.Count < required {
				return apperrors.New(model.ErrResourceInsufficient, "target gpu count is insufficient", http.StatusBadRequest, false)
			}
		}
		return nil
	}
	if appRuntime == "mock" {
		if runtimeProfile.AdapterType != "mock" || target.Runtime.RuntimeType != "mock" {
			return apperrors.New(model.ErrResourceInsufficient, "mock app requires mock runtime and target", http.StatusBadRequest, false)
		}
		return nil
	}
	if runtimeProfile.RuntimeType != appRuntime {
		return apperrors.New(model.ErrResourceInsufficient, "runtime profile does not match app runtime type", http.StatusBadRequest, false)
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
