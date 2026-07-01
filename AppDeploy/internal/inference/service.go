package inference

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
	"github.com/khu/ai-app-deployer/internal/store"
)

type Service struct {
	apps        store.AppRepository
	profiles    store.ProfileRepository
	deployments store.DeploymentRepository
	runner      cpuvm.Runner
}

func NewService(apps store.AppRepository, profiles store.ProfileRepository, deployments store.DeploymentRepository, runner cpuvm.Runner) *Service {
	return &Service{
		apps:        apps,
		profiles:    profiles,
		deployments: deployments,
		runner:      runner,
	}
}

func (s *Service) Health(ctx context.Context, deploymentID string) (model.InferenceInvokeResponse, error) {
	return s.Invoke(ctx, deploymentID, model.InferenceInvokeRequest{
		Method: "GET",
		Path:   "/health",
	})
}

func (s *Service) Invoke(ctx context.Context, deploymentID string, req model.InferenceInvokeRequest) (model.InferenceInvokeResponse, error) {
	if strings.TrimSpace(deploymentID) == "" {
		return model.InferenceInvokeResponse{}, apperrors.New(model.ErrRuntimeFailed, "deployment_id is required", http.StatusBadRequest, false)
	}
	deployment, err := s.deployments.GetDeployment(ctx, deploymentID)
	if err != nil {
		return model.InferenceInvokeResponse{}, err
	}
	if deployment.Status != model.StatusRunning {
		return model.InferenceInvokeResponse{}, apperrors.New(model.ErrRuntimeFailed, "deployment must be RUNNING for inference", http.StatusConflict, true)
	}
	app, err := s.apps.GetAppByVersionID(ctx, deployment.AppVersionID)
	if err != nil {
		return model.InferenceInvokeResponse{}, err
	}
	target, err := s.profiles.GetTargetProfile(ctx, deployment.TargetProfileID)
	if err != nil {
		return model.InferenceInvokeResponse{}, err
	}
	if target.Runtime.OperatingMode != "" && target.Runtime.OperatingMode != "vm_process" {
		return model.InferenceInvokeResponse{}, apperrors.New(model.ErrRuntimeFailed, "inference proxy supports vm_process targets only", http.StatusBadRequest, false)
	}

	method := normalizeMethod(req.Method)
	path, err := normalizePath(req.Path)
	if err != nil {
		return model.InferenceInvokeResponse{}, err
	}
	port := req.Port
	if port == 0 {
		port = firstAppPort(app.AppSpec)
	}
	if port < 1 || port > 65535 {
		return model.InferenceInvokeResponse{}, apperrors.New(model.ErrRuntimeFailed, "inference port is not configured", http.StatusBadRequest, false)
	}
	timeout := req.TimeoutSeconds
	if timeout == 0 {
		timeout = 30
	}
	if timeout < 1 || timeout > 300 {
		return model.InferenceInvokeResponse{}, apperrors.New(model.ErrRuntimeFailed, "timeout_seconds must be between 1 and 300", http.StatusBadRequest, false)
	}

	start := time.Now().UTC()
	output, err := s.callVM(ctx, target, method, port, path, timeout, req.Headers, req.Body)
	if err != nil {
		return model.InferenceInvokeResponse{}, err
	}
	statusCode, rawBody, err := splitCurlOutput(output.Output)
	if err != nil {
		return model.InferenceInvokeResponse{}, err
	}
	body, raw := parseBody(rawBody)
	return model.InferenceInvokeResponse{
		DeploymentID:     deployment.DeploymentID,
		AppID:            deployment.AppID,
		AppVersionID:     deployment.AppVersionID,
		RuntimeProfileID: deployment.RuntimeProfileID,
		TargetProfileID:  deployment.TargetProfileID,
		Method:           method,
		Path:             path,
		Port:             port,
		StatusCode:       statusCode,
		Body:             body,
		RawBody:          raw,
		DurationMS:       time.Since(start).Milliseconds(),
		InvokedAt:        start,
	}, nil
}

func (s *Service) callVM(ctx context.Context, target model.TargetProfile, method string, port int, path string, timeout int, headers map[string]string, body json.RawMessage) (cpuvm.Result, error) {
	args := []string{
		"-sS",
		"--max-time", strconv.Itoa(timeout),
		"-X", method,
		"-w", "\n__AIAPP_HTTP_STATUS__:%{http_code}",
	}
	if method != http.MethodGet {
		args = append(args, "-H", "content-type: application/json")
		if len(body) == 0 {
			body = json.RawMessage(`{}`)
		}
		args = append(args, "--data-binary", string(body))
	}
	for key, value := range headers {
		if isSafeHeader(key) {
			args = append(args, "-H", key+": "+value)
		}
	}
	args = append(args, fmt.Sprintf("http://127.0.0.1:%d%s", port, path))

	result, err := s.runner.Run(ctx, target, cpuvm.Command{
		Stage: model.StatusRunning,
		Name:  "curl",
		Args:  args,
	})
	if err != nil {
		return cpuvm.Result{}, apperrors.New(model.ErrRuntimeFailed, err.Error(), http.StatusBadGateway, true)
	}
	return result, nil
}

func normalizeMethod(method string) string {
	method = strings.ToUpper(strings.TrimSpace(method))
	if method == "" {
		return http.MethodPost
	}
	if method == http.MethodGet {
		return http.MethodGet
	}
	return http.MethodPost
}

func normalizePath(path string) (string, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		path = "/generate"
	}
	if !strings.HasPrefix(path, "/") || strings.Contains(path, "://") || strings.ContainsAny(path, "\r\n\t") {
		return "", apperrors.New(model.ErrRuntimeFailed, "inference path must be a relative HTTP path", http.StatusBadRequest, false)
	}
	return path, nil
}

func firstAppPort(spec model.AppSpec) int {
	if spec.Network == nil || len(spec.Network.Ports) == 0 {
		return 0
	}
	return spec.Network.Ports[0].AppPort
}

func splitCurlOutput(output string) (int, string, error) {
	marker := "\n__AIAPP_HTTP_STATUS__:"
	idx := strings.LastIndex(output, marker)
	if idx < 0 {
		return 0, "", apperrors.New(model.ErrRuntimeFailed, "inference response did not include HTTP status", http.StatusBadGateway, true)
	}
	rawStatus := strings.TrimSpace(output[idx+len(marker):])
	statusCode, err := strconv.Atoi(rawStatus)
	if err != nil {
		return 0, "", apperrors.New(model.ErrRuntimeFailed, "inference response HTTP status is invalid", http.StatusBadGateway, true)
	}
	return statusCode, strings.TrimSpace(output[:idx]), nil
}

func parseBody(rawBody string) (any, string) {
	if rawBody == "" {
		return nil, ""
	}
	var body any
	if err := json.Unmarshal([]byte(rawBody), &body); err != nil {
		return nil, rawBody
	}
	return body, ""
}

func isSafeHeader(key string) bool {
	key = strings.ToLower(strings.TrimSpace(key))
	if key == "" || key == "host" || key == "content-length" {
		return false
	}
	return !strings.ContainsAny(key, "\r\n:")
}
