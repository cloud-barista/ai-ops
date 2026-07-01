package server

import (
	"log"
	"os"
	"strings"
	"time"

	appsvc "github.com/khu/ai-app-deployer/internal/app"
	"github.com/khu/ai-app-deployer/internal/config"
	depsvc "github.com/khu/ai-app-deployer/internal/deployment"
	"github.com/khu/ai-app-deployer/internal/external/etri"
	"github.com/khu/ai-app-deployer/internal/handler"
	infsvc "github.com/khu/ai-app-deployer/internal/inference"
	monsvc "github.com/khu/ai-app-deployer/internal/monitoring"
	profilesvc "github.com/khu/ai-app-deployer/internal/profile"
	"github.com/khu/ai-app-deployer/internal/requestid"
	ressvc "github.com/khu/ai-app-deployer/internal/resource"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/runtime/aiinfra"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
	"github.com/khu/ai-app-deployer/internal/runtime/gpuvm"
	mockruntime "github.com/khu/ai-app-deployer/internal/runtime/mock"
	"github.com/khu/ai-app-deployer/internal/store"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
)

func New() *echo.Echo {
	e := echo.New()
	e.HideBanner = true
	e.Use(middleware.Recover())
	e.Use(requestid.Middleware)

	repo := newRepository()
	mockAdapter := mockruntime.New()
	cpuAdapter := cpuvm.New(cpuVMRunner())
	gpuAdapter := gpuvm.New(gpuVMRunner())
	aiInfraAdapter := aiinfra.New(etri.NewMockClient())
	adapter := runtime.NewRouter(mockAdapter)
	adapter.RegisterAdapterType("mock", mockAdapter)
	adapter.RegisterRuntimeType("mock", mockAdapter)
	adapter.RegisterAdapterType("cpu_vm", cpuAdapter)
	adapter.RegisterRuntimeType("cpu", cpuAdapter)
	adapter.RegisterAdapterType("gpu_vm", gpuAdapter)
	adapter.RegisterRuntimeType("gpu", gpuAdapter)
	adapter.RegisterAdapterType("etri_aiinfra", aiInfraAdapter)
	adapter.RegisterRuntimeType("aiinfra", aiInfraAdapter)
	apps := appsvc.NewService(repo)
	profiles := profilesvc.NewService(repo)
	matcher := ressvc.NewMatcher()
	deployments := depsvc.NewService(repo, repo, repo, matcher, adapter)
	resources := ressvc.NewService(repo, repo, adapter)
	monitoring := monsvc.NewService(repo)
	inference := infsvc.NewService(repo, repo, repo, cpuvm.NewSSHRunner(config.NewEnvCredentialResolver(), 30*time.Second))

	handler.New(apps, profiles, deployments, resources, monitoring, inference).Register(e)
	return e
}

type repository interface {
	store.AppRepository
	store.ProfileRepository
	store.DeploymentRepository
	store.MetricRepository
}

func newRepository() repository {
	path := strings.TrimSpace(os.Getenv("AIAPP_STORE_PATH"))
	if path == "" {
		return store.NewMemory()
	}
	repo, err := store.NewFile(path)
	if err != nil {
		log.Fatalf("open AIAPP_STORE_PATH: %v", err)
	}
	return repo
}

func cpuVMRunner() cpuvm.Runner {
	if strings.EqualFold(os.Getenv("AIAPP_CPUVM_RUNNER"), "ssh") {
		return cpuvm.NewSSHRunner(config.NewEnvCredentialResolver(), 30*time.Second)
	}
	return cpuvm.NewDryRunRunner()
}

func gpuVMRunner() cpuvm.Runner {
	if strings.EqualFold(os.Getenv("AIAPP_GPUVM_RUNNER"), "ssh") {
		return cpuvm.NewSSHRunner(config.NewEnvCredentialResolver(), 30*time.Second)
	}
	return gpuvm.NewDryRunRunner()
}
