package api

import (
	"context"
	"fmt"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func (service Service) RunAppDeployPlanner(ctx context.Context, request AppDeployPlannerRequest) (deploymentplanner.Response, error) {
	return service.RunAppDeployPlannerWithConfig(
		ctx,
		request,
		service.config.LLMCandidatesPath,
		service.config.AppDeployBaseURL,
	)
}

func (service Service) RunAppDeployPlannerWithConfig(
	ctx context.Context,
	request AppDeployPlannerRequest,
	candidatesPath string,
	appDeployBaseURL string,
) (deploymentplanner.Response, error) {
	if err := ensureContext(ctx); err != nil {
		return deploymentplanner.Response{}, err
	}
	if strings.TrimSpace(appDeployBaseURL) == "" {
		return deploymentplanner.Response{}, fmt.Errorf("AppDeploy base URL is required")
	}
	candidates, err := llmclient.LoadCandidateConfig(candidatesPath)
	if err != nil {
		return deploymentplanner.Response{}, err
	}
	candidate, err := llmclient.FindEnabledCandidate(candidates, request.CandidateID)
	if err != nil {
		return deploymentplanner.Response{}, err
	}
	deployer, err := appdeploy.NewClient(appDeployBaseURL, nil)
	if err != nil {
		return deploymentplanner.Response{}, err
	}
	pollInterval := time.Duration(request.PollIntervalMS) * time.Millisecond
	if request.PollIntervalMS == 0 {
		pollInterval = time.Second
	}
	maxPollAttempts := request.MaxPollAttempts
	if maxPollAttempts == 0 {
		maxPollAttempts = 60
	}
	planner := deploymentplanner.NewPlanner(
		deploymentplanner.NewGenerator(llmclient.NewClient(nil)),
		deployer,
	)
	return planner.PlanAndDeploy(ctx, deploymentplanner.Request{
		Candidate: candidate,
		GenerateInput: deploymentplanner.GenerateInput{
			NaturalLanguageRequest: request.NaturalLanguageRequest,
			AppVersionID:           request.AppVersionID,
			TargetProfileID:        request.TargetProfileID,
			RequestedBy:            request.RequestedBy,
			Parameters:             request.Parameters,
		},
		PollInterval:    pollInterval,
		MaxPollAttempts: maxPollAttempts,
	})
}
