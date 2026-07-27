package api

import (
	"context"
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func (service Service) RunAppDeployPlanner(ctx context.Context, request AppDeployPlannerRequest) (deploymentplanner.Response, error) {
	return service.RunAppDeployPlannerWithConfig(
		ctx,
		request,
		service.config.LLMCandidatesPath,
		service.config.PlannerGuardPolicyPath,
		service.config.AppDeployBaseURL,
	)
}

func (service Service) RunAppDeployPlannerWithConfig(
	ctx context.Context,
	request AppDeployPlannerRequest,
	candidatesPath string,
	guardPolicyPath string,
	appDeployBaseURL string,
) (deploymentplanner.Response, error) {
	if err := ensureContext(ctx); err != nil {
		return deploymentplanner.Response{}, err
	}
	if strings.TrimSpace(appDeployBaseURL) == "" {
		return deploymentplanner.Response{}, fmt.Errorf("AppDeploy base URL is required")
	}
	if strings.TrimSpace(request.RequestedBy) == "" {
		request.RequestedBy = "ai-ops-geon-planner"
	}
	run, err := service.CreateControlRunWithDependencies(ctx, CreateControlRunRequest{
		NaturalLanguageRequest: request.NaturalLanguageRequest,
		AppVersionID:           request.AppVersionID,
		CandidateID:            request.CandidateID,
		TargetProfileID:        request.TargetProfileID,
		RequestedBy:            request.RequestedBy,
		AgentName:              request.AgentName,
		Parameters:             request.Parameters,
	}, candidatesPath, guardPolicyPath, deploymentplanner.NewGenerator(llmclient.NewClient(nil)))
	response := plannerResponseFromControlRun(run)
	if err != nil {
		return response, err
	}
	deployer, err := appdeploy.NewClient(appDeployBaseURL, nil)
	if err != nil {
		failed, failErr := service.failControlRunSubmission(run.RunID, err)
		return plannerResponseFromControlRun(failed), failErr
	}
	submitted, result, err := service.submitControlRunWithDeployerResult(ctx, run.RunID, SubmitControlRunRequest{
		PollIntervalMS:  request.PollIntervalMS,
		MaxPollAttempts: request.MaxPollAttempts,
	}, deployer)
	result.RunID = submitted.RunID
	result.RequestGuard = submitted.RequestGuard
	result.Generation = submitted.Generation
	result.Manifest = submitted.Manifest
	return result, err
}

func plannerResponseFromControlRun(run controlrun.Run) deploymentplanner.Response {
	result := deploymentplanner.Response{
		RunID:            run.RunID,
		Valid:            run.Status == controlrun.StatusDeployed,
		Status:           string(run.Status),
		RequestGuard:     run.RequestGuard,
		Generation:       run.Generation,
		Manifest:         run.Manifest,
		Logs:             append([]appdeploy.DeploymentLog(nil), run.Logs...),
		RetryRecommended: run.RetryRecommended,
		RetryReason:      run.RetryReason,
	}
	if run.Deployment != nil {
		result.Deployment = *run.Deployment
		if run.Deployment.Status != "" {
			result.Status = run.Deployment.Status
		}
	}
	if run.Polling != nil {
		result.Polling = *run.Polling
	}
	if run.Status == controlrun.StatusDeployed {
		result.Status = "RUNNING"
	}
	return result
}
