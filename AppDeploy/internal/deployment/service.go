package deployment

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/google/uuid"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/requestid"
	"github.com/khu/ai-app-deployer/internal/resource"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/store"
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
	if req.AppVersionID == "" || req.RuntimeProfileID == "" || req.TargetProfileID == "" {
		return model.DeploymentResponse{}, apperrors.New(model.ErrDeploymentFailed, "app_version_id, runtime_profile_id, and target_profile_id are required", http.StatusBadRequest, false)
	}
	now := time.Now().UTC()
	deployment := model.DeploymentResponse{
		DeploymentID:     "dep-" + uuid.NewString(),
		AppVersionID:     req.AppVersionID,
		RuntimeProfileID: req.RuntimeProfileID,
		TargetProfileID:  req.TargetProfileID,
		Status:           model.StatusRequested,
		CreatedAt:        now,
		UpdatedAt:        now,
	}
	if err := s.deployments.CreateDeployment(ctx, deployment); err != nil {
		return model.DeploymentResponse{}, err
	}
	s.record(ctx, deployment.DeploymentID, model.StatusRequested, "INFO", "orchestrator", "deployment created", "", false)

	app, err := s.apps.GetAppByVersionID(ctx, req.AppVersionID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusValidationFailed, model.ErrAppSpecInvalid, err.Error(), false)
	}
	deployment.AppID = app.AppID
	_ = s.deployments.UpdateDeployment(ctx, deployment)
	runtimeProfile, err := s.profiles.GetRuntimeProfile(ctx, req.RuntimeProfileID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusValidationFailed, model.ErrRuntimeProfileInvalid, err.Error(), false)
	}
	target, err := s.profiles.GetTargetProfile(ctx, req.TargetProfileID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusValidationFailed, model.ErrTargetProfileInvalid, err.Error(), false)
	}

	deployment = s.transition(ctx, deployment, model.StatusValidating, "validator", "app, runtime profile, and target profile validation started")
	if err := s.adapter.ValidateTarget(ctx, target); err != nil {
		return s.fail(ctx, deployment, model.StatusValidationFailed, model.ErrTargetProfileInvalid, err.Error(), false)
	}
	if err := s.adapter.HealthCheck(ctx, runtimeProfile, target); err != nil {
		return s.fail(ctx, deployment, model.StatusValidationFailed, model.ErrRuntimeFailed, err.Error(), true)
	}
	deployment = s.transition(ctx, deployment, model.StatusValidated, "validator", "app, runtime profile, and target profile validation passed")

	deployment = s.transition(ctx, deployment, model.StatusScheduling, "resource-matcher", "resource matching started")
	if err := s.matcher.Match(ctx, app, runtimeProfile, target); err != nil {
		code := model.ErrResourceInsufficient
		if appErr, ok := err.(*apperrors.AppError); ok {
			code = appErr.Code
		}
		return s.fail(ctx, deployment, model.StatusSchedulingFailed, code, err.Error(), false)
	}

	deployment = s.transition(ctx, deployment, model.StatusDeploying, "orchestrator", "runtime deploy requested")
	if _, err := s.adapter.Prepare(ctx, app, target); err != nil {
		return s.failFromError(ctx, deployment, model.StatusDeploymentFailed, model.ErrDeploymentFailed, err, false)
	}
	_, err = s.adapter.Deploy(ctx, runtime.DeploymentPlan{
		DeploymentID: deployment.DeploymentID,
		RequestID:    requestID,
		App:          app,
		Runtime:      runtimeProfile,
		Target:       target,
		Parameters:   req.Parameters,
	})
	if err != nil {
		return s.failFromError(ctx, deployment, model.StatusDeploymentFailed, model.ErrDeploymentFailed, err, false)
	}
	runtimeStatus, err := s.adapter.GetStatus(ctx, deployment.DeploymentID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeFailed, err.Error(), true)
	}
	if runtimeStatus.Status != model.StatusRunning {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeFailed, runtimeStatus.Message, true)
	}
	deployment = s.transition(ctx, deployment, model.StatusRunning, "runtime-adapter", "app healthcheck passed")
	return deployment, nil
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
	app, err := s.apps.GetAppByVersionID(ctx, deployment.AppVersionID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrAppSpecInvalid, err.Error(), false)
	}
	runtimeProfile, err := s.profiles.GetRuntimeProfile(ctx, deployment.RuntimeProfileID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeProfileInvalid, err.Error(), false)
	}
	target, err := s.profiles.GetTargetProfile(ctx, deployment.TargetProfileID)
	if err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrTargetProfileInvalid, err.Error(), false)
	}
	deployment = s.transition(ctx, deployment, model.StatusStopping, "orchestrator", "stop requested")
	if err := s.adapter.Stop(ctx, runtime.StopPlan{
		DeploymentID: deploymentID,
		RequestID:    requestid.FromContext(ctx),
		App:          app,
		Runtime:      runtimeProfile,
		Target:       target,
	}); err != nil {
		return s.fail(ctx, deployment, model.StatusRuntimeFailed, model.ErrRuntimeFailed, err.Error(), true)
	}
	deployment = s.transition(ctx, deployment, model.StatusStopped, "runtime-adapter", "app stopped")
	return deployment, nil
}

func (s *Service) transition(ctx context.Context, deployment model.DeploymentResponse, status, component, message string) model.DeploymentResponse {
	deployment.Status = status
	deployment.UpdatedAt = time.Now().UTC()
	_ = s.deployments.UpdateDeployment(ctx, deployment)
	s.record(ctx, deployment.DeploymentID, status, "INFO", component, message, "", false)
	return deployment
}

func (s *Service) fail(ctx context.Context, deployment model.DeploymentResponse, status, code, message string, retryable bool) (model.DeploymentResponse, error) {
	return s.failWithHTTPStatus(ctx, deployment, status, code, message, http.StatusBadRequest, retryable)
}

func (s *Service) failWithHTTPStatus(ctx context.Context, deployment model.DeploymentResponse, status, code, message string, httpStatus int, retryable bool) (model.DeploymentResponse, error) {
	deployment.Status = status
	deployment.UpdatedAt = time.Now().UTC()
	_ = s.deployments.UpdateDeployment(ctx, deployment)
	s.record(ctx, deployment.DeploymentID, status, "ERROR", "orchestrator", message, code, retryable)
	return deployment, apperrors.New(code, message, httpStatus, retryable)
}

func (s *Service) failFromError(ctx context.Context, deployment model.DeploymentResponse, defaultStatus, defaultCode string, err error, retryable bool) (model.DeploymentResponse, error) {
	status := defaultStatus
	code := defaultCode
	message := err.Error()
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
	_ = s.deployments.AddEvent(ctx, model.DeploymentEvent{
		EventID:      "evt-" + uuid.NewString(),
		Timestamp:    time.Now().UTC(),
		Level:        level,
		RequestID:    requestid.FromContext(ctx),
		DeploymentID: deploymentID,
		Component:    component,
		Stage:        stage,
		Message:      message,
		ErrorCode:    errorCode,
		Retryable:    retryable,
	})
}
