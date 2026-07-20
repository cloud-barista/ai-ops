package deploymentplanner

import (
	"context"
	"errors"
	"fmt"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type manifestGenerator interface {
	Generate(context.Context, llmclient.Candidate, GenerateInput) (GenerateResult, error)
}

type deploymentClient interface {
	CreateDeployment(context.Context, appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error)
	GetDeployment(context.Context, string) (appdeploy.DeploymentResponse, error)
	GetDeploymentLogs(context.Context, string) (appdeploy.DeploymentLogsResponse, error)
}

type Request struct {
	Candidate       llmclient.Candidate `json:"-"`
	GenerateInput   GenerateInput       `json:"-"`
	PollInterval    time.Duration       `json:"-"`
	MaxPollAttempts int                 `json:"-"`
}

type PollingResult struct {
	Attempts      int    `json:"attempts"`
	MaxAttempts   int    `json:"max_attempts"`
	FinalStatus   string `json:"final_status"`
	Terminal      bool   `json:"terminal"`
	LogsCollected bool   `json:"logs_collected"`
}

type Response struct {
	Valid            bool                         `json:"valid"`
	Status           string                       `json:"status"`
	RequestGuard     plannerguard.Decision        `json:"request_guard"`
	Generation       GenerateResult               `json:"generation"`
	Manifest         appdeploy.DeploymentManifest `json:"manifest"`
	Deployment       appdeploy.DeploymentResponse `json:"deployment"`
	Polling          PollingResult                `json:"polling"`
	Logs             []appdeploy.DeploymentLog    `json:"logs,omitempty"`
	RetryRecommended bool                         `json:"retry_recommended"`
	RetryReason      string                       `json:"retry_reason,omitempty"`
}

type Planner struct {
	generator manifestGenerator
	deployer  deploymentClient
}

func NewPlanner(generator manifestGenerator, deployer deploymentClient) Planner {
	return Planner{generator: generator, deployer: deployer}
}

func (planner Planner) PlanAndDeploy(ctx context.Context, request Request) (Response, error) {
	result := Response{Status: "NOT_EXECUTED"}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if planner.generator == nil {
		return result, fmt.Errorf("deployment manifest generator is required")
	}
	if planner.deployer == nil {
		return result, fmt.Errorf("AppDeploy client is required")
	}
	if request.MaxPollAttempts <= 0 {
		request.MaxPollAttempts = 60
	}
	if request.PollInterval <= 0 {
		request.PollInterval = time.Second
	}
	result.Polling.MaxAttempts = request.MaxPollAttempts

	generation, err := planner.generator.Generate(ctx, request.Candidate, request.GenerateInput)
	result.Generation = generation
	result.Manifest = generation.Manifest
	if err != nil {
		result.Status = "MANIFEST_REJECTED"
		return result, err
	}

	deployment, err := planner.deployer.CreateDeployment(ctx, generation.Manifest)
	if err != nil {
		result.Status = "APPDEPLOY_REQUEST_FAILED"
		setRetryRecommendation(&result, err)
		return result, err
	}
	result.Deployment = deployment
	result.Status = deployment.Status
	if terminal := classifyTerminalStatus(deployment.Status); terminal != 0 {
		return planner.finish(ctx, result, terminal)
	}

	for attempt := 1; attempt <= request.MaxPollAttempts; attempt++ {
		if attempt > 1 {
			if err := waitForPoll(ctx, request.PollInterval); err != nil {
				return result, err
			}
		}
		result.Polling.Attempts = attempt
		deployment, err = planner.deployer.GetDeployment(ctx, result.Deployment.DeploymentID)
		if err != nil {
			setRetryRecommendation(&result, err)
			var apiError *appdeploy.APIError
			if errors.As(err, &apiError) && apiError.Retryable {
				continue
			}
			result.Status = "STATUS_QUERY_FAILED"
			return result, err
		}
		result.Deployment = deployment
		result.Status = deployment.Status
		if terminal := classifyTerminalStatus(deployment.Status); terminal != 0 {
			return planner.finish(ctx, result, terminal)
		}
	}
	result.Status = "POLL_TIMEOUT"
	result.Polling.FinalStatus = result.Deployment.Status
	return result, nil
}

func (planner Planner) finish(ctx context.Context, result Response, terminal int) (Response, error) {
	result.Polling.Terminal = true
	result.Polling.FinalStatus = result.Deployment.Status
	logs, err := planner.deployer.GetDeploymentLogs(ctx, result.Deployment.DeploymentID)
	if err != nil {
		result.Status = "LOG_QUERY_FAILED"
		setRetryRecommendation(&result, err)
		return result, err
	}
	result.Logs = append([]appdeploy.DeploymentLog(nil), logs.Items...)
	result.Polling.LogsCollected = true
	if terminal > 0 {
		result.Valid = true
		result.Status = "RUNNING"
	}
	return result, nil
}

func classifyTerminalStatus(status string) int {
	switch status {
	case "RUNNING":
		return 1
	case "VALIDATION_FAILED", "SCHEDULING_FAILED", "DEPLOYMENT_FAILED", "RUNTIME_FAILED", "EXTERNAL_API_FAILED", "UNKNOWN":
		return -1
	default:
		return 0
	}
}

func setRetryRecommendation(result *Response, err error) {
	var apiError *appdeploy.APIError
	if !errors.As(err, &apiError) || !apiError.Retryable {
		return
	}
	result.RetryRecommended = true
	if apiError.Code != "" {
		result.RetryReason = "AppDeploy explicitly marked " + apiError.Code + " as retryable"
	} else {
		result.RetryReason = "AppDeploy explicitly marked the request as retryable"
	}
}

func waitForPoll(ctx context.Context, interval time.Duration) error {
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
