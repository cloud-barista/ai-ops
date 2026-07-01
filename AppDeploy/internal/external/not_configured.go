package external

import (
	"context"
	"fmt"

	"github.com/khu/ai-app-deployer/internal/model"
)

type NotConfiguredClient struct {
	provider Provider
}

func NewNotConfiguredClient(provider Provider) *NotConfiguredClient {
	return &NotConfiguredClient{provider: provider}
}

func (c *NotConfiguredClient) Provider() Provider {
	return c.provider
}

func (c *NotConfiguredClient) Check(ctx context.Context, target model.TargetProfile) (*CheckResult, error) {
	return nil, c.notConfigured()
}

func (c *NotConfiguredClient) Prepare(ctx context.Context, req PrepareRequest) (*PrepareResult, error) {
	return nil, c.notConfigured()
}

func (c *NotConfiguredClient) Deploy(ctx context.Context, req DeployRequest) (*DeployResult, error) {
	return nil, c.notConfigured()
}

func (c *NotConfiguredClient) Status(ctx context.Context, deploymentID string) (*StatusResult, error) {
	return nil, c.notConfigured()
}

func (c *NotConfiguredClient) Logs(ctx context.Context, deploymentID, stage string) ([]model.DeploymentLog, error) {
	return nil, c.notConfigured()
}

func (c *NotConfiguredClient) Stop(ctx context.Context, deploymentID string) error {
	return c.notConfigured()
}

func (c *NotConfiguredClient) notConfigured() error {
	return NewError(c.provider, ErrorKindProviderFailed, fmt.Sprintf("%s external API client is not configured", c.provider), true)
}
