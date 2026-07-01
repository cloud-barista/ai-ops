package external

import (
	"context"

	"github.com/khu/ai-app-deployer/internal/model"
)

type Provider string

const (
	ProviderETRI     Provider = "etri"
	ProviderInnogrid Provider = "innogrid"
	ProviderBespin   Provider = "bespin"
)

type Client interface {
	Provider() Provider
	Check(ctx context.Context, target model.TargetProfile) (*CheckResult, error)
	Prepare(ctx context.Context, req PrepareRequest) (*PrepareResult, error)
	Deploy(ctx context.Context, req DeployRequest) (*DeployResult, error)
	Status(ctx context.Context, deploymentID string) (*StatusResult, error)
	Logs(ctx context.Context, deploymentID, stage string) ([]model.DeploymentLog, error)
	Stop(ctx context.Context, deploymentID string) error
}

type CheckResult struct {
	Status  string
	Message string
	Details map[string]string
}

type PrepareRequest struct {
	App    model.AppResponse
	Target model.TargetProfile
}

type PrepareResult struct {
	ArtifactRef string
	Message     string
}

type DeployRequest struct {
	DeploymentID string
	RequestID    string
	App          model.AppResponse
	Runtime      model.RuntimeProfile
	Target       model.TargetProfile
	Parameters   map[string]any
}

type DeployResult struct {
	ExternalDeploymentID string
	Status               string
	Message              string
}

type StatusResult struct {
	Status  string
	Message string
}
