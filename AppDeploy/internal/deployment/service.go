package deployment

import (
	"context"
	"errors"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/requestid"
	"github.com/khu/ai-app-deployer/internal/resource"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/store"
	"github.com/rs/zerolog/log"
)

type Service struct {
	apps        store.AppRepository
	profiles    store.ProfileRepository
	deployments store.DeploymentRepository
	matcher     *resource.Matcher
	adapter     runtime.Adapter
}

func NewService(apps store.AppRepository, profiles store.ProfileRepository, deployments store.DeploymentRepository, matcher *resource.Matcher, adapter runtime.Adapter) *Service {
	return &Service{
		apps:        apps,
		profiles:    profiles,
		deployments: deployments,
		matcher:     matcher,
		adapter:     adapter,
	}
}

func (s *Service) Create(ctx context.Context, req model.DeploymentCreateRequest) (model.DeploymentResponse, error) {
	requestID := requestid.FromContext(ctx)
	deploymentID := "dep-" + uuid.NewString()
	manifest, err := normalizeManifest(req, deploymentID)
	if err != nil {
		return model.DeploymentResponse{}, err
	}
	now := time.Now().UTC()
	deployment := model.DeploymentResponse{
		DeploymentID:    deploymentID,
		AppVersionID:    manifest.Spec.AppVersionID,
		TargetProfileID: manifest.Spec.TargetProfileID,
		Status:          model.StatusRequested,
		CreatedAt:       now,
		UpdatedAt:       now,
		Manifest:        &manifest,
	}
	if err := s.deployments.CreateDeployment(ctx, deployment); err != nil {
		return model.DeploymentResponse{}, err
	}
	s.record(ctx, deployment.DeploymentID, model.StatusRequested, "INFO", "orchestrator", "deployment created", "", false)
	log.Info().
		Str("request_id", requestID).
		Str("deployment_id", deployment.DeploymentID).
		Str("manifest_schema_version", manifest.SchemaVersion).
		Str("manifest_kind", manifest.Kind).
		Msg("deployment manifest created")

	app, err := s.apps.GetAppByVersionID(ctx, manifest.Spec.AppVersionID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusValidationFailed, model.ErrAppSpecInvalid, apperrors.PublicMessage(err, "app version lookup failed"), false)
	}
	deployment.AppID = app.AppID
	completeManifestRequirements(&manifest, app.AppSpec)
	deployment.Manifest = &manifest
	if err := s.deployments.UpdateDeployment(ctx, deployment); err != nil {
		log.Error().
			Err(err).
			Str("request_id", requestID).
			Str("deployment_id", deployment.DeploymentID).
			Str("component", "orchestrator").
			Msg("deployment update failed")
	}
	deployment = s.transition(ctx, deployment, model.StatusValidating, "target-selector", "app validation and target selection started")
	target, runtimeConfig, err := s.selectTarget(ctx, manifest, app)
	if err != nil {
		code := model.ErrResourceInsufficient
		status := model.StatusSchedulingFailed
		if appErr, ok := err.(*apperrors.AppError); ok {
			code = appErr.Code
			if code == model.ErrTargetProfileInvalid || code == "NOT_FOUND" {
				status = model.StatusValidationFailed
			}
		}
		return s.fail(ctx, deployment, status, code, apperrors.PublicMessage(err, "no suitable target profile is ready"), false)
	}
	manifest.Spec.TargetProfileID = target.TargetProfileID
	deployment.TargetProfileID = target.TargetProfileID
	deployment.Manifest = &manifest
	if err := s.deployments.UpdateDeployment(ctx, deployment); err != nil {
		log.Error().Err(err).Str("request_id", requestID).Str("deployment_id", deployment.DeploymentID).Msg("selected target update failed")
	}
	deployment = s.transition(ctx, deployment, model.StatusValidated, "target-selector", "app and target profile validation passed; target selected")
	deployment = s.transition(ctx, deployment, model.StatusScheduling, "resource-matcher", "resource matching completed for selected target")

	deployment = s.transition(ctx, deployment, model.StatusDeploying, "orchestrator", "runtime deploy requested")
	if _, err := s.adapter.Prepare(ctx, app, target); err != nil {
		return s.failFromError(ctx, deployment, model.StatusDeploymentFailed, model.ErrDeploymentFailed, err, false)
	}
	_, err = s.adapter.Deploy(ctx, runtime.DeploymentPlan{
		DeploymentID: deployment.DeploymentID,
		RequestID:    requestID,
		Manifest:     deployment.Manifest,
		App:          app,
		Runtime:      runtimeConfig,
		Target:       target,
		Parameters:   manifest.Spec.Parameters,
	})
	if err != nil {
		return s.failFromError(ctx, deployment, model.StatusDeploymentFailed, model.ErrDeploymentFailed, err, false)
	}
	runtimeStatus, err := s.adapter.GetStatus(ctx, deployment.DeploymentID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeFailed, apperrors.PublicMessage(err, "runtime status check failed"), true)
	}
	if runtimeStatus.Status != model.StatusRunning {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeFailed, runtimeStatus.Message, true)
	}
	deployment = s.transition(ctx, deployment, model.StatusRunning, "runtime-adapter", "app healthcheck passed")
	return deployment, nil
}

// selectTarget lets the App Deployer choose a ready Target Profile from the
// planner's resource/runtime requirements. A target_profile_id, when present,
// is treated as a compatibility hint and still goes through the same checks.
func (s *Service) selectTarget(ctx context.Context, manifest model.DeploymentManifest, app model.AppResponse) (model.TargetProfile, model.RuntimeConfig, error) {
	var candidates []model.TargetProfile
	var err error
	if hint := strings.TrimSpace(manifest.Spec.TargetProfileID); hint != "" {
		var target model.TargetProfile
		target, err = s.profiles.GetTargetProfile(ctx, hint)
		if err != nil {
			return model.TargetProfile{}, model.RuntimeConfig{}, apperrors.WithDetails(
				model.ErrTargetProfileInvalid,
				"target profile hint was not found",
				http.StatusNotFound,
				false,
				map[string]any{"target_profile_id": hint},
			)
		}
		candidates = []model.TargetProfile{target}
	} else {
		candidates, err = s.profiles.ListTargetProfiles(ctx)
		if err != nil {
			return model.TargetProfile{}, model.RuntimeConfig{}, err
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].TargetProfileID < candidates[j].TargetProfileID
	})
	if len(candidates) == 0 {
		return model.TargetProfile{}, model.RuntimeConfig{}, apperrors.New(
			model.ErrTargetProfileInvalid,
			"no target profiles are registered",
			http.StatusBadRequest,
			false,
		)
	}

	rejected := make([]string, 0, len(candidates))
	for _, target := range candidates {
		runtimeConfig := runtime.ConfigFromTarget(target)
		if err := s.adapter.ValidateTarget(ctx, target); err != nil {
			rejected = append(rejected, target.TargetProfileID+": "+apperrors.PublicMessage(err, "target validation failed"))
			continue
		}
		if err := s.adapter.HealthCheck(ctx, runtimeConfig, target); err != nil {
			rejected = append(rejected, target.TargetProfileID+": "+apperrors.PublicMessage(err, "runtime health check failed"))
			continue
		}
		if err := s.matcher.MatchManifest(ctx, manifest, app, runtimeConfig, target); err != nil {
			rejected = append(rejected, target.TargetProfileID+": "+apperrors.PublicMessage(err, "resource requirements are not satisfied"))
			continue
		}
		log.Info().
			Str("request_id", requestid.FromContext(ctx)).
			Str("target_profile_id", target.TargetProfileID).
			Str("component", "target-selector").
			Msg("ready target profile selected")
		return target, runtimeConfig, nil
	}
	return model.TargetProfile{}, model.RuntimeConfig{}, apperrors.WithDetails(
		model.ErrResourceInsufficient,
		"no target profile satisfies the app requirements and readiness checks",
		http.StatusBadRequest,
		false,
		map[string]any{"candidate_count": len(candidates), "rejected_targets": rejected},
	)
}

func (s *Service) List(ctx context.Context) ([]model.DeploymentResponse, error) {
	return s.deployments.ListDeployments(ctx)
}

func (s *Service) Get(ctx context.Context, deploymentID string) (model.DeploymentResponse, error) {
	return s.deployments.GetDeployment(ctx, deploymentID)
}

func (s *Service) Logs(ctx context.Context, deploymentID, stage string) ([]model.DeploymentLog, error) {
	events, err := s.deployments.ListEvents(ctx, deploymentID, stage)
	if err != nil {
		return nil, err
	}
	logs := make([]model.DeploymentLog, 0, len(events))
	for _, event := range events {
		logs = append(logs, model.DeploymentLog{
			Timestamp:    event.Timestamp,
			Level:        event.Level,
			RequestID:    event.RequestID,
			DeploymentID: event.DeploymentID,
			Component:    event.Component,
			Stage:        event.Stage,
			Message:      event.Message,
			ErrorCode:    event.ErrorCode,
		})
	}
	runtimeLogs, err := s.adapter.GetLogs(ctx, deploymentID, runtime.LogQuery{Stage: stage})
	if err != nil {
		return nil, err
	}
	logs = append(logs, runtimeLogs...)
	return logs, nil
}

func (s *Service) Stop(ctx context.Context, deploymentID string) (model.DeploymentResponse, error) {
	deployment, err := s.deployments.GetDeployment(ctx, deploymentID)
	if err != nil {
		return model.DeploymentResponse{}, err
	}
	if deployment.Status == model.StatusStopped {
		return deployment, nil
	}
	// Validation and scheduling failures never start a VM process. Do not issue
	// a remote stop command for those records: the target may be unreachable or
	// have no GPU driver, while the deployment still needs a terminal status.
	events, err := s.deployments.ListEvents(ctx, deploymentID, "")
	if err != nil {
		return model.DeploymentResponse{}, err
	}
	if !runtimeProcessStarted(events) {
		return s.transition(ctx, deployment, model.StatusStopped, "orchestrator", "no runtime process was started; deployment marked stopped"), nil
	}
	app, err := s.apps.GetAppByVersionID(ctx, deployment.AppVersionID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrAppSpecInvalid, apperrors.PublicMessage(err, "app version lookup failed"), false)
	}
	target, err := s.profiles.GetTargetProfile(ctx, deployment.TargetProfileID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrTargetProfileInvalid, apperrors.PublicMessage(err, "target profile lookup failed"), false)
	}
	runtimeConfig := runtime.ConfigFromTarget(target)
	deployment = s.transition(ctx, deployment, model.StatusStopping, "orchestrator", "stop requested")
	if err := s.adapter.Stop(ctx, runtime.StopPlan{
		DeploymentID: deploymentID,
		RequestID:    requestid.FromContext(ctx),
		Manifest:     deployment.Manifest,
		App:          app,
		Runtime:      runtimeConfig,
		Target:       target,
	}); err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeFailed, apperrors.PublicMessage(err, "runtime stop failed"), true)
	}
	deployment = s.transition(ctx, deployment, model.StatusStopped, "runtime-adapter", "app stopped")
	return deployment, nil
}

func runtimeProcessStarted(events []model.DeploymentEvent) bool {
	for _, event := range events {
		if event.Stage == model.StatusDeploying || event.Stage == model.StatusRunning {
			return true
		}
	}
	return false
}

func (s *Service) transition(ctx context.Context, deployment model.DeploymentResponse, status, component, message string) model.DeploymentResponse {
	deployment.Status = status
	deployment.UpdatedAt = time.Now().UTC()
	if err := s.deployments.UpdateDeployment(ctx, deployment); err != nil {
		log.Error().
			Err(err).
			Str("request_id", requestid.FromContext(ctx)).
			Str("deployment_id", deployment.DeploymentID).
			Str("component", component).
			Str("stage", status).
			Msg("deployment transition update failed")
	}
	s.record(ctx, deployment.DeploymentID, status, "INFO", component, message, "", false)
	return deployment
}

func (s *Service) fail(ctx context.Context, deployment model.DeploymentResponse, status, code, message string, retryable bool) (model.DeploymentResponse, error) {
	return s.failWithHTTPStatus(ctx, deployment, status, code, message, http.StatusBadRequest, retryable)
}

func (s *Service) failWithHTTPStatus(ctx context.Context, deployment model.DeploymentResponse, status, code, message string, httpStatus int, retryable bool) (model.DeploymentResponse, error) {
	deployment.Status = status
	deployment.UpdatedAt = time.Now().UTC()
	if err := s.deployments.UpdateDeployment(ctx, deployment); err != nil {
		log.Error().
			Err(err).
			Str("request_id", requestid.FromContext(ctx)).
			Str("deployment_id", deployment.DeploymentID).
			Str("component", "orchestrator").
			Str("stage", status).
			Str("error_code", code).
			Bool("retryable", retryable).
			Msg("deployment failure update failed")
	}
	s.record(ctx, deployment.DeploymentID, status, "ERROR", "orchestrator", message, code, retryable)
	return deployment, apperrors.New(code, message, httpStatus, retryable)
}

func (s *Service) failFromError(ctx context.Context, deployment model.DeploymentResponse, defaultStatus, defaultCode string, err error, retryable bool) (model.DeploymentResponse, error) {
	status := defaultStatus
	code := defaultCode
	message := apperrors.PublicMessage(err, "deployment operation failed")
	httpStatus := http.StatusBadRequest
	var appErr *apperrors.AppError
	if errors.As(err, &appErr) {
		code = appErr.Code
		message = appErr.Message
		httpStatus = appErr.HTTPStatus
		retryable = appErr.Retryable
		if isExternalErrorCode(code) {
			status = model.StatusExternalAPIFailed
		}
	}
	return s.failWithHTTPStatus(ctx, deployment, status, code, message, httpStatus, retryable)
}

func isExternalErrorCode(code string) bool {
	switch code {
	case model.ErrAIInfraAPITimeout, model.ErrAIInfraAPIFailed, model.ErrGatewayAuthFailed, model.ErrBespinAPIFailed:
		return true
	default:
		return false
	}
}

func (s *Service) record(ctx context.Context, deploymentID, stage, level, component, message, errorCode string, retryable bool) {
	requestID := requestid.FromContext(ctx)
	event := model.DeploymentEvent{
		EventID:      "evt-" + uuid.NewString(),
		Timestamp:    time.Now().UTC(),
		Level:        level,
		RequestID:    requestID,
		DeploymentID: deploymentID,
		Component:    component,
		Stage:        stage,
		Message:      message,
		ErrorCode:    errorCode,
		Retryable:    retryable,
	}
	if err := s.deployments.AddEvent(ctx, event); err != nil {
		log.Error().
			Err(err).
			Str("request_id", requestID).
			Str("deployment_id", deploymentID).
			Str("component", component).
			Str("stage", stage).
			Msg("deployment event persistence failed")
	}
	logEvent := log.Info()
	if level == "ERROR" {
		logEvent = log.Error()
	}
	logEvent.
		Str("request_id", requestID).
		Str("deployment_id", deploymentID).
		Str("component", component).
		Str("stage", stage).
		Str("status", stage).
		Str("error_code", errorCode).
		Bool("retryable", retryable).
		Msg(message)
}
