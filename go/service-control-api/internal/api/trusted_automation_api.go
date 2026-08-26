package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/audittrail"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/llmopbridge"
	"kyunghee-aiops/service-control-api/internal/trustedorchestration"
)

type TrustedAutomationRunRequest struct {
	AppVersionID string                          `json:"app_version_id" validate:"required"`
	CandidateID  string                          `json:"candidate_id" validate:"required"`
	Input        agentcontrol.AutomationRunInput `json:"input" validate:"required"`
}

type TrustedAutomationRunErrorResponse struct {
	Message   string                       `json:"message"`
	ErrorCode string                       `json:"error_code"`
	Result    TrustedAutomationRunResponse `json:"result"`
}

type TrustedAutomationRunResponse struct {
	Status             string                                     `json:"status"`
	Safeguard          llmop.SafeguardStageResult                 `json:"safeguard"`
	AutomationRun      *agentcontrol.AutomationRun                `json:"automation_run,omitempty"`
	IdempotentReplay   bool                                       `json:"idempotent_replay,omitempty"`
	ApprovedProjection *llmopbridge.ApprovedInitialFlowProjection `json:"approved_projection,omitempty"`
	Audit              audittrail.Reference                       `json:"audit"`
}

// RestPostTrustedAutomationRun godoc
// @ID PostTrustedAutomationRun
// @Summary Run LLM_Op Safeguard before the geon Revision 1 workflow
// @Description Review the exact request with LLM_Op, stop non-approved requests, and run geon requirement analysis, resource recommendation, Agent decision, and Guards only after approval. This endpoint does not submit to AppDeploy.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body TrustedAutomationRunRequest true "Guard-first automation request"
// @Success 201 {object} TrustedAutomationRunResponse
// @Success 200 {object} TrustedAutomationRunResponse "Safeguard or geon terminal decision"
// @Failure 400 {object} TrustedAutomationRunErrorResponse
// @Failure 500 {object} TrustedAutomationRunErrorResponse
// @Router /api/v1/agent-control/trusted-automation-runs [post]
func (handler restHandler) RestPostTrustedAutomationRun(context echo.Context) error {
	var request TrustedAutomationRunRequest
	if _, err := bindAndValidate(context, &request); err != nil {
		result := TrustedAutomationRunResponse{
			Status: "ERROR",
			Audit:  degradedAuditReference(),
		}
		log.Warn().
			Str("error_code", "TRUSTED_AUTOMATION_FAILED").
			Str("audit_id", result.Audit.AuditID).
			Msg("trusted automation request binding failed")
		return context.JSON(http.StatusBadRequest, TrustedAutomationRunErrorResponse{
			Message:   "Guard-first automation request could not be bound",
			ErrorCode: "TRUSTED_AUTOMATION_FAILED",
			Result:    result,
		})
	}
	result, err := handler.service.RunTrustedAutomation(
		context.Request().Context(),
		request,
	)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, audittrail.ErrPersistence) {
			status = http.StatusInternalServerError
		}
		log.Error().
			Str("error_code", trustedAutomationErrorCode(err)).
			Str("audit_id", result.Audit.AuditID).
			Str("audit_persistence_status", result.Audit.PersistenceStatus).
			Str("correlation_id", result.Safeguard.CorrelationID).
			Msg("trusted automation request failed")
		return context.JSON(status, TrustedAutomationRunErrorResponse{
			Message: "Guard-first automation request could not be processed",
			ErrorCode: trustedAutomationErrorCode(err),
			Result:    result,
		})
	}
	log.Info().
		Str("audit_id", result.Audit.AuditID).
		Str("audit_persistence_status", result.Audit.PersistenceStatus).
		Str("correlation_id", result.Safeguard.CorrelationID).
		Str("status", result.Status).
		Msg("trusted automation request completed")
	if result.Status == trustedorchestration.StatusApprovedFlowReady {
		return context.JSON(http.StatusCreated, result)
	}
	return context.JSON(http.StatusOK, result)
}

func (service Service) RunTrustedAutomation(
	ctx context.Context,
	request TrustedAutomationRunRequest,
) (TrustedAutomationRunResponse, error) {
	startedAt := time.Now().UTC()
	response := TrustedAutomationRunResponse{
		Status: "ERROR",
		Audit:  degradedAuditReference(),
	}
	requestSummary, err := buildTrustedAuditRequestSummary(request)
	if err != nil {
		return response, err
	}
	var session *audittrail.Session
	if service.trustedAuditStore != nil {
		var reference audittrail.Reference
		session, reference, err = service.trustedAuditStore.Start(audittrail.StartMetadata{
			StartedAt: startedAt,
			Request:   requestSummary,
		})
		response.Audit = reference
	} else {
		err = fmt.Errorf("%w: audit store is not configured", audittrail.ErrPersistence)
	}
	if err != nil && service.trustedAuditRequired {
		return response, err
	}
	var recorder audittrail.Recorder
	if session != nil {
		recorder = session
		if !service.trustedAuditRequired {
			recorder = audittrail.BestEffort(recorder)
		}
	}
	finishAudit := func(
		result trustedorchestration.Result,
		runErr error,
		failureStage string,
	) error {
		if session == nil {
			return nil
		}
		reference, finalizeErr := finalizeTrustedAudit(
			session,
			result,
			runErr,
			failureStage,
			time.Now().UTC(),
		)
		response.Audit = reference
		if finalizeErr != nil && service.trustedAuditRequired {
			return finalizeErr
		}
		return nil
	}
	if service.trustedOrchestration == nil {
		runErr := fmt.Errorf("trusted orchestration is not configured")
		if finalizeErr := finishAudit(trustedorchestration.Result{}, runErr, "configuration"); finalizeErr != nil {
			return response, finalizeErr
		}
		return response, runErr
	}
	input, err := buildTrustedOrchestrationInput(request)
	if err != nil {
		if recorder != nil {
			if recordErr := recorder.Record(audittrail.Event{
				Stage:     audittrail.StageRequest,
				Action:    "request_binding_completed",
				Outcome:   "rejected",
				ErrorCode: "REQUEST_BINDING_REJECTED",
			}); recordErr != nil {
				if finalizeErr := finishAudit(trustedorchestration.Result{}, recordErr, "audit_persistence"); finalizeErr != nil {
					return response, finalizeErr
				}
				return response, recordErr
			}
		}
		if finalizeErr := finishAudit(trustedorchestration.Result{}, err, "request_binding"); finalizeErr != nil {
			return response, finalizeErr
		}
		return response, err
	}
	if recorder != nil {
		if err := recorder.BindIdentity(trustedAuditInputIdentity(input)); err != nil {
			if finalizeErr := finishAudit(trustedorchestration.Result{}, err, "request_binding"); finalizeErr != nil {
				return response, finalizeErr
			}
			return response, err
		}
		inputDigest, digestErr := audittrail.DigestJSON(input)
		if digestErr != nil {
			if finalizeErr := finishAudit(trustedorchestration.Result{}, digestErr, "request_binding"); finalizeErr != nil {
				return response, finalizeErr
			}
			return response, digestErr
		}
		if err := recorder.Record(audittrail.Event{
			Stage:   audittrail.StageRequest,
			Action:  "request_binding_completed",
			Outcome: "bound",
			Evidence: audittrail.Evidence{
				InputSHA256: inputDigest,
			},
		}); err != nil {
			if finalizeErr := finishAudit(trustedorchestration.Result{}, err, "audit_persistence"); finalizeErr != nil {
				return response, finalizeErr
			}
			return response, err
		}
		ctx = audittrail.WithRecorder(ctx, recorder)
	}
	result, runErr := service.trustedOrchestration.RunApprovedFlow(ctx, input)
	if result.Status != "" {
		response.Status = result.Status
	}
	response.Safeguard = result.Safeguard
	response.AutomationRun = result.AutomationRun
	response.IdempotentReplay = result.IdempotentReplay
	response.ApprovedProjection = result.ApprovedProjection
	if recorder != nil {
		if result.AutomationRun != nil {
			if bindErr := recorder.BindIdentity(audittrail.Identity{
				CorrelationID: result.AutomationRun.CorrelationID,
				TraceID:       result.AutomationRun.TraceID,
				RunID:         result.AutomationRun.RunID,
			}); bindErr != nil && runErr == nil {
				runErr = bindErr
			}
		}
		if finalizeErr := finishAudit(result, runErr, "trusted_orchestration"); finalizeErr != nil {
			return response, finalizeErr
		}
	}
	return response, runErr
}

func buildTrustedOrchestrationInput(
	request TrustedAutomationRunRequest,
) (trustedorchestration.Input, error) {
	appVersionID := strings.TrimSpace(request.AppVersionID)
	candidateID := strings.TrimSpace(request.CandidateID)
	if appVersionID == "" || candidateID == "" {
		return trustedorchestration.Input{}, fmt.Errorf("app_version_id and candidate_id are required")
	}
	input := request.Input
	requestedBy := strings.TrimSpace(input.RequestedBy)
	if requestedBy == "" {
		requestedBy = "geon-web"
	}

	appID := "ai-application"
	appVersion := "1.0.0"
	artifact := agentcontrol.Artifact{
		Type:       "script",
		URI:        "file:///tmp/geon-standalone/run.sh",
		Entrypoint: []string{"bash", "run.sh"},
	}
	userRequest := strings.TrimSpace(input.Request)
	if input.AppSpec != nil {
		if value := strings.TrimSpace(input.AppSpec.AppID); value != "" {
			appID = value
		}
		if value := strings.TrimSpace(input.AppSpec.AppVersion); value != "" {
			appVersion = value
		}
		if input.AppSpec.Artifact != nil {
			artifact = *input.AppSpec.Artifact
		}
	}
	switch input.InputType {
	case agentcontrol.InputTypeNaturalLanguage:
		if userRequest == "" {
			return trustedorchestration.Input{}, fmt.Errorf("request is required for natural_language input")
		}
	case agentcontrol.InputTypeStructured:
		if input.AppSpec == nil {
			return trustedorchestration.Input{}, fmt.Errorf("app_spec is required for structured input")
		}
		userRequest = structuredSafeguardSummary(*input.AppSpec)
	default:
		return trustedorchestration.Input{}, fmt.Errorf("unsupported input_type: %s", input.InputType)
	}

	requestID, err := trustedID("request")
	if err != nil {
		return trustedorchestration.Input{}, err
	}
	correlationID, err := trustedID("flow")
	if err != nil {
		return trustedorchestration.Input{}, err
	}
	traceID, err := trustedID("trace")
	if err != nil {
		return trustedorchestration.Input{}, err
	}
	messageID, err := trustedID("msg-analysis")
	if err != nil {
		return trustedorchestration.Input{}, err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)

	llmRequest := llmop.Request{
		APIVersion:    llmop.APIVersion,
		RequestID:     requestID,
		CorrelationID: correlationID,
		TraceID:       traceID,
		CandidateID:   candidateID,
		RequestedBy:   requestedBy,
		Application: llmop.Application{
			AppVersionID: appVersionID,
			UserRequest:  userRequest,
		},
		Policy: llmop.RequestPolicy{Mode: llmop.ModePrepareOnly},
	}
	analysisRequest := agentcontrol.ApplicationAnalysisRequestEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       messageID,
			MessageType:     agentcontrol.MessageApplicationAnalysisRequest,
			OccurredAt:      now,
			CorrelationID:   correlationID,
			TraceID:         traceID,
			Source: agentcontrol.Endpoint{
				System:    requestedBy,
				Component: "trusted-request-api",
			},
			Target: agentcontrol.Endpoint{
				System:    "khu-agent-control",
				Component: "automation-agent",
			},
		},
		Data: agentcontrol.ApplicationAnalysisRequestData{
			Application: agentcontrol.AnalysisRequestApplication{
				AppID:       appID,
				AppVersion:  appVersion,
				Artifact:    artifact,
				UserRequest: userRequest,
			},
			RequestedDecisionAgent: strings.TrimSpace(input.DecisionAgent),
		},
	}
	if input.InputType == agentcontrol.InputTypeStructured {
		structured := *input.AppSpec
		analysisRequest.Data.StructuredAppSpec = &structured
	}
	return trustedorchestration.Input{
		Request:         llmRequest,
		AnalysisRequest: analysisRequest,
	}, nil
}

func structuredSafeguardSummary(spec agentcontrol.StructuredAppSpec) string {
	return fmt.Sprintf(
		"Prepare only a VM resource plan requiring CPU %d cores, memory %d MiB, NVIDIA GPU %d, and storage %d GiB. Do not execute it.",
		spec.CPUCores,
		spec.MemoryMiB,
		spec.AcceleratorCount,
		spec.StorageGiB,
	)
}

func trustedID(prefix string) (string, error) {
	bytes := make([]byte, 12)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate %s id: %w", prefix, err)
	}
	return prefix + "-" + hex.EncodeToString(bytes), nil
}
