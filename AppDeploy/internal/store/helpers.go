package store

import (
	"context"
	"net/http"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

func contextError(ctx context.Context) error {
	if ctx == nil {
		return nil
	}
	return ctx.Err()
}

func appNameVersionKey(name, version string) string {
	return name + ":" + version
}

func mapValues[K comparable, V any](source map[K]V) []V {
	items := make([]V, 0, len(source))
	for _, item := range source {
		items = append(items, item)
	}
	return items
}

func ensureMap[K comparable, V any](target *map[K]V) {
	if *target == nil {
		*target = make(map[K]V)
	}
}

func mapValue[K comparable, V any](source map[K]V, key K, message string) (V, error) {
	value, ok := source[key]
	if ok {
		return value, nil
	}
	var zero V
	return zero, apperrors.New("NOT_FOUND", message, http.StatusNotFound, false)
}

func validateAppDeletion(deployments map[string]model.DeploymentResponse, app model.AppResponse) error {
	count := 0
	for _, deployment := range deployments {
		if (deployment.AppID == app.AppID || deployment.AppVersionID == app.AppVersionID) && deployment.Status != model.StatusStopped {
			count++
		}
	}
	if count == 0 {
		return nil
	}
	return apperrors.WithDetails(
		model.ErrAppSpecInvalid,
		"all deployments must be STOPPED before the app can be deleted",
		http.StatusConflict,
		false,
		map[string]any{"deployment_count": count},
	)
}

func validateRuntimeProfileDeletion(deployments map[string]model.DeploymentResponse, profileID string) error {
	return validateProfileDeletion(
		deployments,
		func(deployment model.DeploymentResponse) bool { return deployment.RuntimeProfileID == profileID },
		model.ErrRuntimeProfileInvalid,
		"all deployments must be STOPPED before the runtime profile can be deleted",
	)
}

func validateTargetProfileDeletion(deployments map[string]model.DeploymentResponse, profileID string) error {
	return validateProfileDeletion(
		deployments,
		func(deployment model.DeploymentResponse) bool { return deployment.TargetProfileID == profileID },
		model.ErrTargetProfileInvalid,
		"all deployments must be STOPPED before the target profile can be deleted",
	)
}

func validateProfileDeletion(deployments map[string]model.DeploymentResponse, referencesProfile func(model.DeploymentResponse) bool, errorCode, message string) error {
	count := 0
	for _, deployment := range deployments {
		if referencesProfile(deployment) && deployment.Status != model.StatusStopped {
			count++
		}
	}
	if count == 0 {
		return nil
	}
	return apperrors.WithDetails(
		errorCode,
		message,
		http.StatusConflict,
		false,
		map[string]any{"deployment_count": count},
	)
}

func filterEvents(events []model.DeploymentEvent, stage string) []model.DeploymentEvent {
	items := make([]model.DeploymentEvent, 0, len(events))
	for _, event := range events {
		if stage == "" || event.Stage == stage {
			items = append(items, event)
		}
	}
	return items
}

func cloneSlice[T any](source []T) []T {
	items := make([]T, len(source))
	copy(items, source)
	return items
}

func flattenSlices[K comparable, V any](source map[K][]V) []V {
	count := 0
	for _, items := range source {
		count += len(items)
	}
	result := make([]V, 0, count)
	for _, items := range source {
		result = append(result, items...)
	}
	return result
}
