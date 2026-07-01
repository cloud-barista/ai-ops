package app

import (
	"context"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

type Service struct {
	repo store.AppRepository
}

func NewService(repo store.AppRepository) *Service {
	return &Service{repo: repo}
}

func (s *Service) Register(ctx context.Context, req model.AppCreateRequest) (model.AppResponse, error) {
	if err := ValidateSpec(req.AppSpec); err != nil {
		return model.AppResponse{}, err
	}
	exists, err := s.repo.ExistsNameVersion(ctx, req.AppSpec.Metadata.Name, req.AppSpec.Metadata.Version)
	if err != nil {
		return model.AppResponse{}, err
	}
	if exists {
		return model.AppResponse{}, apperrors.New(model.ErrAppSpecInvalid, "app name/version already exists", http.StatusBadRequest, false)
	}

	now := time.Now().UTC()
	appID := "app-" + uuid.NewString()
	appVersionID := "appver-" + uuid.NewString()
	resp := model.AppResponse{
		AppID:        appID,
		AppVersionID: appVersionID,
		Name:         req.AppSpec.Metadata.Name,
		Version:      req.AppSpec.Metadata.Version,
		AppSpec:      req.AppSpec,
		CreatedAt:    now,
	}
	if err := s.repo.CreateApp(ctx, resp); err != nil {
		return model.AppResponse{}, err
	}
	return resp, nil
}

func (s *Service) List(ctx context.Context) ([]model.AppResponse, error) {
	return s.repo.ListApps(ctx)
}

func (s *Service) Get(ctx context.Context, appID string) (model.AppResponse, error) {
	return s.repo.GetApp(ctx, appID)
}

func (s *Service) GetByVersion(ctx context.Context, appVersionID string) (model.AppResponse, error) {
	return s.repo.GetAppByVersionID(ctx, appVersionID)
}

var appNamePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,62}$`)

func ValidateSpec(spec model.AppSpec) error {
	if spec.SchemaVersion != "appspec.khu.ai/v1alpha1" {
		return invalid("schema_version must be appspec.khu.ai/v1alpha1")
	}
	if spec.Kind != "AIApp" {
		return invalid("kind must be AIApp")
	}
	if !appNamePattern.MatchString(spec.Metadata.Name) {
		return invalid("metadata.name must match ^[a-z0-9][a-z0-9-]{1,62}$")
	}
	if strings.TrimSpace(spec.Metadata.Version) == "" {
		return invalid("metadata.version is required")
	}
	if !isAllowed(spec.Artifact.Type, "package", "git", "binary", "script") {
		return invalid("artifact.type must be one of package, git, binary, script")
	}
	if spec.Artifact.Type == "container" {
		return invalid("container artifact is excluded from year 1 scope")
	}
	if strings.TrimSpace(spec.Artifact.URI) == "" {
		return invalid("artifact.uri is required")
	}
	if strings.TrimSpace(spec.Entrypoint.Command) == "" {
		return apperrors.New(model.ErrEntrypointInvalid, "entrypoint.command is required", http.StatusBadRequest, false)
	}
	if !isAllowed(spec.Runtime.Type, "cpu", "gpu", "aiinfra", "mock") {
		return invalid("runtime.type must be one of cpu, gpu, aiinfra, mock")
	}
	if spec.Runtime.Accelerator != "" && !isAllowed(spec.Runtime.Accelerator, "none", "nvidia") {
		return invalid("runtime.accelerator must be none or nvidia")
	}
	if spec.Runtime.Type == "gpu" && parsePositiveInt(spec.Resources.GPU) < 1 {
		return invalid("runtime.type=gpu requires resources.gpu >= 1")
	}
	if spec.Network != nil {
		for _, port := range spec.Network.Ports {
			if port.AppPort < 1 || port.AppPort > 65535 {
				return invalid("network.ports.app_port must be between 1 and 65535")
			}
			if port.Protocol != "" && !isAllowed(port.Protocol, "TCP", "UDP") {
				return invalid("network.ports.protocol must be TCP or UDP")
			}
		}
	}
	if spec.Healthcheck != nil && spec.Healthcheck.Type != "" && !isAllowed(spec.Healthcheck.Type, "http", "command") {
		return invalid("healthcheck.type must be http or command")
	}
	return nil
}

func invalid(message string) error {
	return apperrors.New(model.ErrAppSpecInvalid, message, http.StatusBadRequest, false)
}

func isAllowed(value string, allowed ...string) bool {
	for _, item := range allowed {
		if value == item {
			return true
		}
	}
	return false
}

func parsePositiveInt(value string) int {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 0 {
		return 0
	}
	return parsed
}
