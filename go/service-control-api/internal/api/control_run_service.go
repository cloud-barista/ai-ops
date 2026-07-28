package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/autonomy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type controlRunManifestGenerator interface {
	Generate(context.Context, llmclient.Candidate, deploymentplanner.GenerateInput) (deploymentplanner.GenerateResult, error)
}

type controlRunDeploymentClient interface {
	CreateDeployment(context.Context, appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error)
	GetDeployment(context.Context, string) (appdeploy.DeploymentResponse, error)
	GetDeploymentLogs(context.Context, string) (appdeploy.DeploymentLogsResponse, error)
}

func controlRunRuntimeType(requirements *appdeploy.DeploymentRequirements) string {
	if requirements == nil {
		return ""
	}
	return requirements.Runtime
}

func (service Service) ListControlRuns() []controlrun.Run {
	return service.controlRuns.List()
}

func (service Service) GetControlRun(runID string) (controlrun.Run, bool) {
	return service.controlRuns.Get(normalizeControlRunID(runID))
}

func (service Service) DeleteControlRun(runID string) (controlrun.Run, bool) {
	return service.controlRuns.Delete(normalizeControlRunID(runID))
}

func (service Service) ClearControlRuns() int {
	return service.controlRuns.Clear()
}

func (service Service) ConfigureAutonomy(config autonomy.Config) error {
	runID := normalizeControlRunID(config.RunID)
	if runID != "" {
		run, ok := service.controlRuns.Get(runID)
		if !ok {
			return fmt.Errorf("ControlRun was not found: %s", runID)
		}
		if run.Status != controlrun.StatusDeployed || run.Deployment == nil || strings.TrimSpace(run.Deployment.DeploymentID) == "" {
			return fmt.Errorf("ControlRun is not linked to a deployed application: %s", runID)
		}
		if strings.TrimSpace(config.DeploymentID) != "" && config.DeploymentID != run.Deployment.DeploymentID {
			return fmt.Errorf("Autonomy deployment_id does not match the ControlRun deployment")
		}
		config.RunID = runID
		config.DeploymentID = run.Deployment.DeploymentID
	}
	return service.autonomyManager.Configure(config)
}

func (service Service) SubmitControlRun(
	ctx context.Context,
	runID string,
	request SubmitControlRunRequest,
) (controlrun.Run, error) {
	client, err := appdeploy.NewClient(service.config.AppDeployBaseURL, nil)
	if err != nil {
		return service.failControlRunSubmission(runID, err)
	}
	return service.SubmitControlRunWithDeployer(ctx, runID, request, client)
}

func (service Service) SubmitControlRunWithDeployer(
	ctx context.Context,
	runID string,
	request SubmitControlRunRequest,
	deployer controlRunDeploymentClient,
) (controlrun.Run, error) {
	run, result, err := service.submitControlRunWithDeployerResult(ctx, runID, request, deployer)
	if err != nil {
		return run, err
	}
	if !result.Valid {
		return run, fmt.Errorf("AppDeploy did not reach a successful terminal state: %s", result.Status)
	}
	return run, nil
}

func (service Service) submitControlRunWithDeployerResult(
	ctx context.Context,
	runID string,
	request SubmitControlRunRequest,
	deployer controlRunDeploymentClient,
) (controlrun.Run, deploymentplanner.Response, error) {
	if err := ensureContext(ctx); err != nil {
		return controlrun.Run{}, deploymentplanner.Response{}, err
	}
	runID = normalizeControlRunID(runID)
	run, ok := service.controlRuns.Get(runID)
	if !ok {
		return controlrun.Run{}, deploymentplanner.Response{}, fmt.Errorf("ControlRun was not found: %s", runID)
	}
	if run.Status != controlrun.StatusManifestApproved && run.Status != controlrun.StatusAppDeployFailed {
		return run, deploymentplanner.Response{}, fmt.Errorf("ControlRun status %s cannot be submitted", run.Status)
	}

	registry, err := loadAgentRegistry(service.config.path("config", "agent_registry.json"))
	if err != nil {
		return run, deploymentplanner.Response{}, err
	}
	selection, err := resolvePlannerAgent(registry, run.SelectedAgent.Name, actionSubmitDeploymentManifest)
	if err != nil {
		run, updateErr := service.controlRuns.Update(runID, func(run *controlrun.Run) error {
			run.Stages = append(run.Stages, completedControlRunStage(
				"agent_registry_submit",
				"rejected",
				err.Error(),
				map[string]any{"agent": run.SelectedAgent.Name, "action": actionSubmitDeploymentManifest},
			))
			return nil
		})
		if updateErr != nil {
			return run, deploymentplanner.Response{}, updateErr
		}
		return run, deploymentplanner.Response{}, err
	}
	run, err = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Status = controlrun.StatusSubmitting
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_registry_submit",
			"approved",
			selection.Reason,
			map[string]any{"agent": selection.Name, "action": selection.Action},
		))
		return nil
	})
	if err != nil {
		return run, deploymentplanner.Response{}, err
	}

	pollInterval := time.Duration(request.PollIntervalMS) * time.Millisecond
	if request.PollIntervalMS == 0 {
		pollInterval = time.Second
	}
	maxPollAttempts := request.MaxPollAttempts
	if maxPollAttempts == 0 {
		maxPollAttempts = 60
	}
	planner := deploymentplanner.NewPlanner(nil, deployer)
	result, deployErr := planner.DeployApprovedManifest(ctx, deploymentplanner.DeployRequest{
		Manifest:        run.Manifest,
		PollInterval:    pollInterval,
		MaxPollAttempts: maxPollAttempts,
	})
	success := deployErr == nil && result.Valid
	stageStatus := "approved"
	stageReason := "AppDeploy accepted the approved Manifest and reached RUNNING"
	runStatus := controlrun.StatusDeployed
	if !success {
		stageStatus = "rejected"
		stageReason = result.Status
		runStatus = controlrun.StatusAppDeployFailed
		if deployErr != nil {
			stageReason = deployErr.Error()
		}
	}
	run, err = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Status = runStatus
		run.Polling = &result.Polling
		run.Logs = append([]appdeploy.DeploymentLog(nil), result.Logs...)
		run.RetryRecommended = result.RetryRecommended
		run.RetryReason = result.RetryReason
		if result.Deployment.DeploymentID != "" || result.Deployment.Status != "" {
			deployment := result.Deployment
			run.Deployment = &deployment
		}
		run.Stages = append(run.Stages, completedControlRunStage(
			"appdeploy_submit",
			stageStatus,
			stageReason,
			map[string]any{
				"deployment_id": result.Deployment.DeploymentID,
				"status":        result.Status,
			},
		))
		return nil
	})
	if err != nil {
		return run, result, err
	}
	if deployErr != nil {
		return run, result, deployErr
	}
	return run, result, nil
}

func (service Service) failControlRunSubmission(runID string, cause error) (controlrun.Run, error) {
	runID = normalizeControlRunID(runID)
	run, ok := service.controlRuns.Get(runID)
	if !ok {
		return controlrun.Run{}, fmt.Errorf("ControlRun was not found: %s", runID)
	}
	if run.Status != controlrun.StatusManifestApproved && run.Status != controlrun.StatusAppDeployFailed {
		return run, fmt.Errorf("ControlRun status %s cannot be submitted", run.Status)
	}
	run, err := service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Status = controlrun.StatusAppDeployFailed
		run.Stages = append(run.Stages, completedControlRunStage(
			"appdeploy_submit",
			"rejected",
			cause.Error(),
			nil,
		))
		return nil
	})
	if err != nil {
		return run, err
	}
	return run, cause
}

func (service Service) CreateControlRun(ctx context.Context, request CreateControlRunRequest) (controlrun.Run, error) {
	return service.CreateControlRunWithDependencies(
		ctx,
		request,
		service.config.LLMCandidatesPath,
		service.config.PlannerGuardPolicyPath,
		deploymentplanner.NewGenerator(llmclient.NewClient(nil)),
	)
}

func (service Service) CreateControlRunWithDependencies(
	ctx context.Context,
	request CreateControlRunRequest,
	candidatesPath string,
	guardPolicyPath string,
	generator controlRunManifestGenerator,
) (controlrun.Run, error) {
	if err := ensureContext(ctx); err != nil {
		return controlrun.Run{}, err
	}
	if generator == nil {
		return controlrun.Run{}, fmt.Errorf("deployment Manifest generator is required")
	}
	if strings.TrimSpace(request.RequestedBy) == "" {
		request.RequestedBy = "ai-ops-geon-planner"
	}
	if err := appdeploy.ValidateRequirementsSecretKeys(request.Requirements); err != nil {
		return controlrun.Run{}, fmt.Errorf("deployment requirements rejected before Qwen: %w", err)
	}
	runID, err := newControlRunID()
	if err != nil {
		return controlrun.Run{}, err
	}
	run := service.controlRuns.Create(controlrun.CreateInput{
		RunID: runID,
		Request: controlrun.SafeRequest{
			NaturalLanguageRequest: request.NaturalLanguageRequest,
			AppVersionID:           request.AppVersionID,
			TargetProfileID:        request.TargetProfileID,
			CandidateID:            request.CandidateID,
			RequestedBy:            request.RequestedBy,
			AgentName:              request.AgentName,
		},
	})

	policy, err := plannerguard.LoadPolicy(guardPolicyPath)
	if err != nil {
		return service.rejectControlRun(run.RunID, controlrun.StatusRequestRejected, "request_guard", err.Error(), nil)
	}
	requestGuard := plannerguard.ValidateRequest(plannerguard.Request{
		NaturalLanguageRequest: request.NaturalLanguageRequest,
		AppVersionID:           request.AppVersionID,
		CandidateID:            request.CandidateID,
		RequestedBy:            request.RequestedBy,
		Parameters:             request.Parameters,
	}, policy)
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.RequestGuard = requestGuard
		run.Stages = append(run.Stages, completedControlRunStage(
			"request_guard",
			requestGuard.Status,
			requestGuard.Reason,
			map[string]any{"policy_version": requestGuard.PolicyVersion},
		))
		if !requestGuard.Valid {
			run.Status = controlrun.StatusRequestRejected
		}
		return nil
	})
	if err != nil {
		return run, err
	}
	if !requestGuard.Valid {
		return run, fmt.Errorf("Go Request Guard rejected the deployment request: %s", requestGuard.Reason)
	}

	registry, err := loadAgentRegistry(service.config.path("config", "agent_registry.json"))
	if err != nil {
		return service.rejectControlRun(run.RunID, controlrun.StatusAgentRejected, "agent_registry", err.Error(), nil)
	}
	selection, err := resolvePlannerAgent(registry, request.AgentName, actionGenerateDeploymentManifest)
	if err != nil {
		return service.rejectControlRun(run.RunID, controlrun.StatusAgentRejected, "agent_registry", err.Error(), nil)
	}
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.SelectedAgent = selection
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_registry",
			"approved",
			selection.Reason,
			map[string]any{"agent": selection.Name, "capability": selection.Capability, "action": selection.Action},
		))
		run.Status = controlrun.StatusPlanning
		return nil
	})
	if err != nil {
		return run, err
	}

	selectedAgent, err := findAgent(registry.Agents, selection.Name)
	if err != nil {
		return service.rejectControlRun(run.RunID, controlrun.StatusManifestRejected, "agent_dispatch", err.Error(), nil)
	}
	selectedAgent.Source = selection.Source
	dispatcher := newAgentDispatcher(
		map[string]agentExecutor{
			"AIApplicationAutomationAgent": newManifestAgentExecutor(candidatesPath, generator),
		},
		nil,
	)
	execution, generationErr := dispatcher.Dispatch(ctx, selectedAgent, AgentDispatchRequest{
		RunID:      run.RunID,
		Agent:      selection.Name,
		Capability: selection.Capability,
		Action:     selection.Action,
		Input: map[string]any{
			"natural_language_request": request.NaturalLanguageRequest,
			"app_version_id":           request.AppVersionID,
			"candidate_id":             request.CandidateID,
			"target_profile_id":        request.TargetProfileID,
			"requested_by":             request.RequestedBy,
			"parameters":               request.Parameters,
			"requirements":             request.Requirements,
		},
	})
	dispatchStatus := "approved"
	dispatchReason := "Agent Dispatcher completed AIApplicationAutomationAgent"
	if generationErr != nil {
		dispatchStatus = "rejected"
		dispatchReason = generationErr.Error()
	}
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.Execution = controlRunAgentExecution(execution, "")
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_dispatch",
			dispatchStatus,
			dispatchReason,
			map[string]any{"agent": selection.Name, "source": selection.Source},
		))
		if generationErr != nil {
			run.Status = controlrun.StatusManifestRejected
		}
		return nil
	})
	if err != nil {
		return run, err
	}
	if generationErr != nil && execution.Generation == nil {
		return run, generationErr
	}
	generation := deploymentplanner.GenerateResult{}
	if execution.Generation != nil {
		generation = *execution.Generation
	}
	requirementsSafe := true
	if err := appdeploy.ValidateRequirementsSecretKeys(generation.Manifest.Spec.Requirements); err != nil {
		requirementsSafe = false
		generation.ExecutionStatus = "rejected"
		generation.GuardValid = false
		generation.GuardReason = err.Error()
		if generationErr == nil {
			generationErr = fmt.Errorf("deployment manifest Go Guard rejected the proposal: %w", err)
		}
	}
	qwenStatus := "approved"
	qwenReason := "Qwen generated a DeploymentManifest candidate"
	if generationErr != nil {
		qwenStatus = "rejected"
		qwenReason = generationErr.Error()
	}
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		if requirementsSafe {
			run.Generation = generation
			run.Manifest = generation.Manifest
		}
		run.Stages = append(run.Stages, completedControlRunStage(
			"qwen_planner",
			qwenStatus,
			qwenReason,
			map[string]any{
				"candidate_id": generation.CandidateID,
				"actual_model": generation.ActualModel,
				"latency_ms":   generation.LatencyMS,
			},
		))
		return nil
	})
	if err != nil {
		return run, err
	}
	if generationErr != nil {
		run, updateErr := service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusManifestRejected
			if run.Execution != nil {
				run.Execution.GuardStatus = "rejected"
				run.Execution.DomainValidation = "manifest_guard"
			}
			if generation.Manifest.Kind != "" {
				run.Stages = append(run.Stages, completedControlRunStage(
					"manifest_guard",
					"rejected",
					generation.GuardReason,
					nil,
				))
			}
			return nil
		})
		if updateErr != nil {
			return run, updateErr
		}
		return run, generationErr
	}

	manifestErr := appdeploy.ValidateManifest(generation.Manifest, appdeploy.ManifestConstraints{
		AppVersionID:    request.AppVersionID,
		TargetProfileID: request.TargetProfileID,
		RequestedBy:     request.RequestedBy,
		RuntimeType:     controlRunRuntimeType(request.Requirements),
		Requirements:    request.Requirements,
	})
	guardStatus := "approved"
	guardReason := "DeploymentManifest matches the AppDeploy contract and trusted request fields"
	runStatus := controlrun.StatusManifestApproved
	if manifestErr != nil {
		guardStatus = "rejected"
		guardReason = manifestErr.Error()
		runStatus = controlrun.StatusManifestRejected
	}
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.Status = runStatus
		if run.Execution != nil {
			run.Execution.GuardStatus = guardStatus
			run.Execution.DomainValidation = "manifest_guard"
		}
		run.Stages = append(run.Stages, completedControlRunStage(
			"manifest_guard",
			guardStatus,
			guardReason,
			nil,
		))
		return nil
	})
	if err != nil {
		return run, err
	}
	if manifestErr != nil {
		return run, fmt.Errorf("DeploymentManifest Go Guard rejected the proposal: %w", manifestErr)
	}
	return run, nil
}

func (service Service) rejectControlRun(
	runID string,
	status controlrun.Status,
	stageName string,
	reason string,
	details map[string]any,
) (controlrun.Run, error) {
	run, err := service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Status = status
		run.Stages = append(run.Stages, completedControlRunStage(stageName, "rejected", reason, details))
		return nil
	})
	if err != nil {
		return run, err
	}
	return run, fmt.Errorf("%s: %s", stageName, reason)
}

func completedControlRunStage(name string, status string, reason string, details map[string]any) controlrun.Stage {
	now := time.Now().UTC()
	return controlrun.Stage{
		Name:      name,
		Status:    status,
		Reason:    reason,
		StartedAt: now,
		EndedAt:   &now,
		Details:   details,
	}
}

func newControlRunID() (string, error) {
	bytes := make([]byte, 12)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate ControlRun ID: %w", err)
	}
	return "run-" + hex.EncodeToString(bytes), nil
}
