package server

import (
	"fmt"
	"strings"
	"time"

	appsvc "github.com/khu/ai-app-deployer/internal/app"
	artifactsvc "github.com/khu/ai-app-deployer/internal/artifact"
	"github.com/khu/ai-app-deployer/internal/config"
	credentialsvc "github.com/khu/ai-app-deployer/internal/credential"
	depsvc "github.com/khu/ai-app-deployer/internal/deployment"
	"github.com/khu/ai-app-deployer/internal/external/etri"
	"github.com/khu/ai-app-deployer/internal/handler"
	infsvc "github.com/khu/ai-app-deployer/internal/inference"
	monsvc "github.com/khu/ai-app-deployer/internal/monitoring"
	profilesvc "github.com/khu/ai-app-deployer/internal/profile"
	"github.com/khu/ai-app-deployer/internal/provider"
	"github.com/khu/ai-app-deployer/internal/requestid"
	ressvc "github.com/khu/ai-app-deployer/internal/resource"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/runtime/aiinfra"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
	"github.com/khu/ai-app-deployer/internal/runtime/gpuvm"
	localruntime "github.com/khu/ai-app-deployer/internal/runtime/local"
	mockruntime "github.com/khu/ai-app-deployer/internal/runtime/mock"
	"github.com/khu/ai-app-deployer/internal/store"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	"github.com/rs/zerolog/log"
)

func New() (*echo.Echo, error) {
	settings, err := config.Load()
	if err != nil {
		return nil, err
	}
	return NewWithConfig(settings)
}

func NewWithConfig(settings config.Settings) (*echo.Echo, error) {
	return newWithConfig(settings, true)
}

// NewCLIWithConfig builds the same API handler used by the HTTP server without
// the per-request access logger. CLI commands still produce deployment and
// runtime event logs, while terminal output remains readable.
func NewCLIWithConfig(settings config.Settings) (*echo.Echo, error) {
	return newWithConfig(settings, false)
}

func newWithConfig(settings config.Settings, requestLogging bool) (*echo.Echo, error) {
	e := echo.New()
	e.HideBanner = true
	e.Server.Addr = listenAddress(settings.ServerPort)
	e.Use(middleware.Recover())
	e.Use(requestid.Middleware)
	if requestLogging {
		e.Use(structuredRequestLogger)
	}

	repo, err := newRepository(settings.StorePath)
	if err != nil {
		return nil, err
	}
	credentials := credentialsvc.NewService(config.NewEnvCredentialResolver(), settings.SSHDefaultTimeout)
	mockAdapter := mockruntime.New()
	localAdapter := localruntime.New(settings.LocalRuntimeWorkDir)
	cpuAdapter := cpuvm.New(cpuVMRunner(settings, credentials))
	gpuAdapter := gpuvm.New(gpuVMRunner(settings, credentials))
	aiInfraAdapter := aiinfra.New(etri.NewMockClient())
	adapter := runtime.NewRouter(mockAdapter)
	adapter.RegisterAdapterType("mock", mockAdapter)
	adapter.RegisterRuntimeType("mock", mockAdapter)
	adapter.RegisterAdapterType("local_process", localAdapter)
	adapter.RegisterRuntimeType("local", localAdapter)
	adapter.RegisterAdapterType("cpu_vm", cpuAdapter)
	adapter.RegisterRuntimeType("cpu", cpuAdapter)
	adapter.RegisterAdapterType("gpu_vm", gpuAdapter)
	adapter.RegisterRuntimeType("gpu", gpuAdapter)
	adapter.RegisterAdapterType("etri_aiinfra", aiInfraAdapter)
	adapter.RegisterRuntimeType("aiinfra", aiInfraAdapter)
	apps := appsvc.NewService(repo)
	packages := artifactsvc.New(artifactsvc.Config{
		AIOpsRoot:      settings.AIOpsRoot,
		OutputDir:      settings.PackageOutputDir,
		BuildTimeout:   settings.PackageTimeout,
		MaxUploadBytes: settings.PackageMaxUpload,
	})
	profiles := profilesvc.NewService(repo)
	matcher := ressvc.NewMatcher()
	resourceProvider, placementProvider, err := buildProviders(settings, repo)
	if err != nil {
		return nil, err
	}
	deployments := depsvc.NewServiceWithProviders(repo, repo, repo, matcher, adapter, resourceProvider, placementProvider)
	resources := ressvc.NewService(repo, repo, adapter)
	monitoring := monsvc.NewService(repo)
	inference := infsvc.NewService(repo, repo, repo, cpuvm.NewSSHRunner(credentials, settings.SSHDefaultTimeout))

	handler.New(apps, packages, profiles, deployments, resources, monitoring, inference, credentials, settings.CredentialRemote).Register(e)
	return e, nil
}

func buildProviders(settings config.Settings, profiles store.ProfileRepository) (provider.ResourceInformationProvider, provider.PlacementProvider, error) {
	resourceName := strings.ToLower(strings.TrimSpace(settings.ResourceProvider))
	if resourceName == "" {
		resourceName = "local"
	}
	placementName := strings.ToLower(strings.TrimSpace(settings.PlacementProvider))
	if placementName == "" {
		placementName = "local"
	}
	etriConfig := provider.ETRIConfig{
		ResourceEndpoint:  settings.ETRIResourceEndpoint,
		PlacementEndpoint: settings.ETRIPlacementEndpoint,
		CredentialRef:     settings.ETRICredentialRef,
		Timeout:           settings.ETRITimeout,
	}
	if resourceName != "local" && resourceName != "etri" {
		return nil, nil, fmt.Errorf("unsupported RESOURCE_PROVIDER %q; use local or etri", resourceName)
	}
	if placementName != "local" && placementName != "etri" {
		return nil, nil, fmt.Errorf("unsupported PLACEMENT_PROVIDER %q; use local or etri", placementName)
	}

	var localProvider *provider.LocalPlacementProvider
	if resourceName == "local" || placementName == "local" {
		var err error
		localProvider, err = provider.NewLocalPlacementProvider(profiles)
		if err != nil {
			return nil, nil, err
		}
	}

	var resourceProvider provider.ResourceInformationProvider
	if resourceName == "local" {
		resourceProvider = localProvider
	} else {
		remoteProvider, err := provider.NewETRIResourceMetadataAdapter(etriConfig)
		if err != nil {
			return nil, nil, err
		}
		resourceProvider = remoteProvider
	}

	var placementProvider provider.PlacementProvider
	if placementName == "local" {
		placementProvider = localProvider
	} else {
		remoteProvider, err := provider.NewETRIPlacementAdapter(etriConfig)
		if err != nil {
			return nil, nil, err
		}
		placementProvider = remoteProvider
	}
	return resourceProvider, placementProvider, nil
}

type repository interface {
	store.AppRepository
	store.ProfileRepository
	store.DeploymentRepository
	store.MetricRepository
}

func newRepository(path string) (repository, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return store.NewMemory(), nil
	}
	repo, err := store.NewFile(path)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", config.KeyStorePath, err)
	}
	return repo, nil
}

func cpuVMRunner(settings config.Settings, credentials config.SSHCredentialResolver) cpuvm.Runner {
	if strings.EqualFold(settings.CPUVMRunner, "ssh") {
		return cpuvm.NewSSHRunner(credentials, settings.SSHDefaultTimeout)
	}
	return cpuvm.NewDryRunRunner()
}

func gpuVMRunner(settings config.Settings, credentials config.SSHCredentialResolver) cpuvm.Runner {
	if strings.EqualFold(settings.GPUVMRunner, "ssh") {
		return cpuvm.NewSSHRunner(credentials, settings.SSHDefaultTimeout)
	}
	return gpuvm.NewDryRunRunner()
}

func listenAddress(port string) string {
	port = strings.TrimSpace(port)
	if port == "" {
		return ":8080"
	}
	if strings.HasPrefix(port, ":") {
		return port
	}
	return ":" + port
}

func structuredRequestLogger(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		start := time.Now()
		err := next(c)
		if err != nil {
			return err
		}
		log.Info().
			Str("request_id", requestid.FromContext(c.Request().Context())).
			Str("method", c.Request().Method).
			Str("path", c.Path()).
			Int("status", c.Response().Status).
			Dur("duration", time.Since(start)).
			Msg("api request completed")
		return nil
	}
}
