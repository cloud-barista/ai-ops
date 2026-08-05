package api

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/controlrun"
)

var (
	errAgentExecutionNotFound       = errors.New("Agent execution target was not found")
	errAgentExecutionUnauthorized   = errors.New("Agent execution request was not authorized")
	errAgentExecutionNotImplemented = errors.New("Agent execution is not implemented")
	errAgentExecutionTimeout        = errors.New("Agent execution timed out")
	errAgentExecutionUpstream       = errors.New("Agent execution endpoint failed")
	errAgentExecutionResultRejected = errors.New("Agent execution result was rejected")
)

func (service Service) ExecuteAgent(
	ctx context.Context,
	agentName string,
	request AgentExecutionRequest,
) (AgentExecutionResponse, error) {
	if agent, err := service.ShowAgent(ctx, agentName); err == nil &&
		agent.Source == agentSourceConfiguration &&
		agent.Name == "AIApplicationAutomationAgent" &&
		request.Capability == capabilityDeploymentManifestPlanning &&
		request.Action == actionGenerateDeploymentManifest {
		manifestRequest, decodeErr := decodeManifestAgentInput(request.Input)
		if decodeErr == nil {
			manifestRequest.AgentName = agent.Name
			run, runErr := service.CreateControlRun(ctx, manifestRequest)
			response := manifestAgentExecutionResponse(agent, request, run)
			if runErr != nil {
				switch run.Status {
				case controlrun.StatusRequestRejected, controlrun.StatusAgentRejected:
					return response, fmt.Errorf("%w: %v", errAgentExecutionUnauthorized, runErr)
				case controlrun.StatusManifestRejected:
					return response, fmt.Errorf("%w: %v", errAgentExecutionResultRejected, runErr)
				default:
					return response, fmt.Errorf("%w: %v", errAgentExecutionUpstream, runErr)
				}
			}
			return response, nil
		}
	}
	return service.ExecuteAgentWithDispatcher(ctx, agentName, request, service.agentDispatcher)
}

func (service Service) ExecuteAgentWithDispatcher(
	ctx context.Context,
	agentName string,
	request AgentExecutionRequest,
	dispatcher *agentDispatcher,
) (AgentExecutionResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentExecutionResponse{}, err
	}
	runID, err := newControlRunID()
	if err != nil {
		return AgentExecutionResponse{}, err
	}
	service.controlRuns.Create(controlrun.CreateInput{
		RunID: runID,
		Request: controlrun.SafeRequest{
			AgentName:  strings.TrimSpace(agentName),
			Capability: strings.TrimSpace(request.Capability),
			Action:     strings.TrimSpace(request.Action),
		},
	})
	response := AgentExecutionResponse{
		RequestID: "req-" + strings.TrimPrefix(runID, "run-"),
		RunID:     runID,
	}

	agent, err := service.ShowAgent(ctx, agentName)
	if err != nil {
		_, _ = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusAgentRejected
			run.Stages = append(run.Stages, completedControlRunStage(
				"agent_registry",
				"rejected",
				err.Error(),
				map[string]any{"agent": agentName},
			))
			return nil
		})
		return response, fmt.Errorf("%w: %v", errAgentExecutionNotFound, err)
	}
	response.SelectedAgent = agent
	selection := controlrun.AgentSelection{
		Name:       agent.Name,
		Capability: request.Capability,
		Action:     request.Action,
		Source:     agent.Source,
		Reason:     "Agent Registry selected the requested Agent",
	}
	_, err = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.SelectedAgent = selection
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_registry",
			"approved",
			selection.Reason,
			map[string]any{"agent": agent.Name, "source": agent.Source},
		))
		return nil
	})
	if err != nil {
		return response, err
	}

	requestGuard := validateAgentExecutionRequest(agent, request)
	response.RequestGuard = requestGuard
	_, err = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_request_guard",
			requestGuard.Status,
			requestGuard.Reason,
			map[string]any{"capability": request.Capability, "action": request.Action},
		))
		if !requestGuard.Valid {
			run.Status = controlrun.StatusAgentRejected
		} else {
			run.Status = controlrun.StatusAgentDispatching
		}
		return nil
	})
	if err != nil {
		return response, err
	}
	if !requestGuard.Valid {
		return response, fmt.Errorf("%w: %s", errAgentExecutionUnauthorized, requestGuard.Reason)
	}

	dispatchRequest := AgentDispatchRequest{
		RunID:      runID,
		Agent:      agent.Name,
		Capability: request.Capability,
		Action:     request.Action,
		Input:      request.Input,
		Context:    request.Context,
	}
	execution, dispatchErr := dispatcher.Dispatch(ctx, agent, dispatchRequest)
	if dispatchErr != nil {
		failedExecution := execution
		failedExecution.RunID = runID
		failedExecution.Agent = agent.Name
		failedExecution.Status = "failed"
		failedExecution.Message = dispatchErr.Error()
		failedExecution.DomainValidation = "not_registered"
		response.Execution = failedExecution
		_, _ = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusAgentFailed
			run.Execution = controlRunAgentExecution(failedExecution, "rejected")
			run.Stages = append(run.Stages, completedControlRunStage(
				"agent_dispatch",
				"rejected",
				dispatchErr.Error(),
				nil,
			))
			return nil
		})
		switch {
		case errors.Is(dispatchErr, context.DeadlineExceeded):
			return response, fmt.Errorf("%w: %v", errAgentExecutionTimeout, dispatchErr)
		case errors.Is(dispatchErr, errInternalAgentExecutorNotRegistered):
			return response, fmt.Errorf("%w: %v", errAgentExecutionNotImplemented, dispatchErr)
		default:
			return response, fmt.Errorf("%w: %v", errAgentExecutionUpstream, dispatchErr)
		}
	}
	response.Execution = execution
	_, err = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Execution = controlRunAgentExecution(execution, "")
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_dispatch",
			"approved",
			"Agent Dispatcher completed the selected Agent call",
			map[string]any{"latency_ms": execution.LatencyMS},
		))
		return nil
	})
	if err != nil {
		return response, err
	}

	resultGuard := validateAgentExecutionResult(agent, dispatchRequest, execution)
	response.ResultGuard = resultGuard
	_, err = service.controlRuns.Update(runID, func(run *controlrun.Run) error {
		run.Stages = append(run.Stages, completedControlRunStage(
			"agent_result_guard",
			resultGuard.Status,
			resultGuard.Reason,
			nil,
		))
		if run.Execution != nil {
			run.Execution.GuardStatus = resultGuard.Status
		}
		switch {
		case !resultGuard.Valid:
			run.Status = controlrun.StatusResultRejected
		case execution.Status == "completed":
			run.Status = controlrun.StatusAgentCompleted
		case execution.Status == "rejected":
			run.Status = controlrun.StatusResultRejected
		default:
			run.Status = controlrun.StatusAgentFailed
		}
		return nil
	})
	if err != nil {
		return response, err
	}
	if !resultGuard.Valid {
		return response, fmt.Errorf("%w: %s", errAgentExecutionResultRejected, resultGuard.Reason)
	}
	return response, nil
}

func controlRunAgentExecution(
	execution AgentExecutionResult,
	guardStatus string,
) *controlrun.AgentExecution {
	return &controlrun.AgentExecution{
		Status:    execution.Status,
		LatencyMS: execution.LatencyMS,
		Proposal: map[string]any{
			"action":     execution.Proposal.Action,
			"parameters": execution.Proposal.Parameters,
		},
		Result:           execution.Result,
		Evidence:         execution.Evidence,
		GuardStatus:      guardStatus,
		Message:          execution.Message,
		DomainValidation: execution.DomainValidation,
	}
}

func manifestAgentExecutionResponse(
	agent AgentProfile,
	request AgentExecutionRequest,
	run controlrun.Run,
) AgentExecutionResponse {
	response := AgentExecutionResponse{
		RequestID:     "req-" + strings.TrimPrefix(run.RunID, "run-"),
		RunID:         run.RunID,
		SelectedAgent: agent,
		RequestGuard: GuardDecision{
			Valid:  run.RequestGuard.Valid,
			Status: run.RequestGuard.Status,
			Reason: run.RequestGuard.Reason,
		},
		Execution: AgentExecutionResult{
			RunID:  run.RunID,
			Agent:  agent.Name,
			Status: "rejected",
			Proposal: AgentProposal{
				Action: request.Action,
			},
			DomainValidation: "manifest_guard",
		},
		ResultGuard: rejectedGuardDecision("DeploymentManifest did not pass Go Manifest Guard"),
	}
	if run.Execution != nil {
		response.Execution.Status = run.Execution.Status
		response.Execution.LatencyMS = run.Execution.LatencyMS
		response.Execution.Result = run.Execution.Result
		response.Execution.Evidence = run.Execution.Evidence
		response.Execution.Message = run.Execution.Message
		response.Execution.DomainValidation = run.Execution.DomainValidation
	}
	if run.Manifest.Kind != "" {
		manifest := run.Manifest
		generation := run.Generation
		response.Execution.Manifest = &manifest
		response.Execution.Generation = &generation
	}
	if run.Status == controlrun.StatusManifestApproved {
		response.Execution.Status = "completed"
		response.ResultGuard = GuardDecision{
			Valid:  true,
			Status: "approved",
			Reason: "DeploymentManifest passed Go Manifest Guard",
		}
	}
	return response
}
