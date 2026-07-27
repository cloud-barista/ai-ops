package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type controlRunManifestGenerator interface {
	Generate(context.Context, llmclient.Candidate, deploymentplanner.GenerateInput) (deploymentplanner.GenerateResult, error)
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

	candidates, err := llmclient.LoadCandidateConfig(candidatesPath)
	if err != nil {
		return service.rejectControlRun(run.RunID, controlrun.StatusManifestRejected, "qwen_planner", err.Error(), nil)
	}
	candidate, err := llmclient.FindEnabledCandidate(candidates, request.CandidateID)
	if err != nil {
		return service.rejectControlRun(run.RunID, controlrun.StatusManifestRejected, "qwen_planner", err.Error(), nil)
	}
	generation, generationErr := generator.Generate(ctx, candidate, deploymentplanner.GenerateInput{
		NaturalLanguageRequest: request.NaturalLanguageRequest,
		AppVersionID:           request.AppVersionID,
		TargetProfileID:        request.TargetProfileID,
		RequestedBy:            request.RequestedBy,
		Parameters:             request.Parameters,
	})
	qwenStatus := "approved"
	qwenReason := "Qwen generated a DeploymentManifest candidate"
	if generationErr != nil {
		qwenStatus = "rejected"
		qwenReason = generationErr.Error()
	}
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.Generation = generation
		run.Manifest = generation.Manifest
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
