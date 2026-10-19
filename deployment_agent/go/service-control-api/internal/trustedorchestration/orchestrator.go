package trustedorchestration

import (
	"context"
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/llmopbridge"
)

const (
	StatusSafeguardStopped  = "SAFEGUARD_STOPPED"
	StatusGeonRejected      = "GEON_REJECTED"
	StatusApprovedFlowReady = "APPROVED_FLOW_READY"
)

type Reviewer interface {
	Review(context.Context, llmop.Request) (llmop.SafeguardStageResult, error)
}

type FlowRunner interface {
	RunAnalysisRequest(
		context.Context,
		agentcontrol.ApplicationAnalysisRequestEnvelope,
	) (agentcontrol.AutomationRun, bool, error)
}

type Input struct {
	Request         llmop.Request                                   `json:"request"`
	AnalysisRequest agentcontrol.ApplicationAnalysisRequestEnvelope `json:"analysis_request"`
}

type Result struct {
	Status             string                                     `json:"status"`
	Safeguard          llmop.SafeguardStageResult                 `json:"safeguard"`
	AutomationRun      *agentcontrol.AutomationRun                `json:"automation_run,omitempty"`
	IdempotentReplay   bool                                       `json:"idempotent_replay,omitempty"`
	ApprovedProjection *llmopbridge.ApprovedInitialFlowProjection `json:"approved_projection,omitempty"`
}

type Orchestrator struct {
	reviewer Reviewer
	runner   FlowRunner
	resolve  llmopbridge.TrustedAppVersionResolver
}

func New(
	reviewer Reviewer,
	runner FlowRunner,
	resolve llmopbridge.TrustedAppVersionResolver,
) *Orchestrator {
	return &Orchestrator{reviewer: reviewer, runner: runner, resolve: resolve}
}

func NewFlowOnly(reviewer Reviewer, runner FlowRunner) *Orchestrator {
	return &Orchestrator{reviewer: reviewer, runner: runner}
}

func (orchestrator *Orchestrator) Run(
	ctx context.Context,
	input Input,
) (Result, error) {
	if orchestrator == nil || orchestrator.resolve == nil {
		return Result{}, fmt.Errorf("trusted AppDeploy app version resolver is not configured")
	}
	result, err := orchestrator.RunApprovedFlow(ctx, input)
	if err != nil || result.Status != StatusApprovedFlowReady {
		return result, err
	}

	projection, err := llmopbridge.ProjectApprovedInitialFlow(
		llmopbridge.ApprovedInitialFlowInput{
			Request:         input.Request,
			Safeguard:       result.Safeguard,
			AnalysisRequest: input.AnalysisRequest,
			Flow:            *result.AutomationRun.Flow,
		},
		orchestrator.resolve,
	)
	if err != nil {
		return result, fmt.Errorf("project approved geon Flow: %w", err)
	}
	result.ApprovedProjection = &projection
	return result, nil
}

func (orchestrator *Orchestrator) RunApprovedFlow(
	ctx context.Context,
	input Input,
) (Result, error) {
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if orchestrator == nil || orchestrator.reviewer == nil || orchestrator.runner == nil {
		return Result{}, fmt.Errorf("trusted orchestration dependencies are not configured")
	}
	if err := validateInputIdentity(input); err != nil {
		return Result{}, err
	}

	safeguard, err := orchestrator.reviewer.Review(ctx, input.Request)
	result := Result{Safeguard: safeguard}
	if err != nil {
		if isAuditableSafeguardStop(safeguard) {
			result.Status = StatusSafeguardStopped
			return result, nil
		}
		return result, fmt.Errorf("review untrusted request: %w", err)
	}
	if !safeguard.Approved {
		result.Status = StatusSafeguardStopped
		return result, nil
	}
	if err := validateSafeguardApproval(safeguard); err != nil {
		return result, err
	}

	run, replayed, err := orchestrator.runner.RunAnalysisRequest(ctx, input.AnalysisRequest)
	result.AutomationRun = &run
	result.IdempotentReplay = replayed
	if err != nil {
		return result, fmt.Errorf("run geon automation Flow: %w", err)
	}
	if run.Flow == nil {
		return result, fmt.Errorf("geon automation run did not retain a Flow")
	}
	if run.Status != agentcontrol.AutomationRunStatusCompleted {
		return result, fmt.Errorf("geon automation run did not complete")
	}
	if run.Flow.State != agentcontrol.StateDecisionApproved {
		result.Status = StatusGeonRejected
		return result, nil
	}
	result.Status = StatusApprovedFlowReady
	return result, nil
}

func isAuditableSafeguardStop(stage llmop.SafeguardStageResult) bool {
	return stage.Status == llmop.StatusRequestRejected ||
		stage.Status == llmop.StatusClarificationNeeded
}

func validateSafeguardApproval(stage llmop.SafeguardStageResult) error {
	if stage.APIVersion != llmop.APIVersion ||
		stage.Stage != llmop.SafeguardStageName ||
		stage.Status != llmop.StatusSafeguardApproved ||
		stage.Decision.Action != llmop.SafeguardDecisionAllow ||
		!stage.RequestGuard.Valid ||
		stage.Review == nil ||
		stage.Review.Decision != llmop.SafeguardDecisionAllow ||
		stage.Continuation == nil {
		return fmt.Errorf("safeguard approval evidence is incomplete")
	}
	return nil
}

func validateInputIdentity(input Input) error {
	analysis := input.AnalysisRequest
	request := input.Request
	if strings.TrimSpace(request.Application.UserRequest) == "" {
		return fmt.Errorf("llmop user request is required")
	}
	if request.Application.UserRequest != analysis.Data.Application.UserRequest {
		return fmt.Errorf("llmop and analysis user requests must exactly match")
	}
	if request.CorrelationID != analysis.CorrelationID || request.TraceID != analysis.TraceID {
		return fmt.Errorf("llmop and analysis correlation and trace identifiers must exactly match")
	}
	return nil
}
