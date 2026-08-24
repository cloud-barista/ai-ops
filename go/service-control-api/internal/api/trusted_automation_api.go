package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/trustedorchestration"
)

type TrustedAutomationRunRequest struct {
	AppVersionID string                          `json:"app_version_id" validate:"required"`
	CandidateID  string                          `json:"candidate_id" validate:"required"`
	Input        agentcontrol.AutomationRunInput `json:"input" validate:"required"`
}

type TrustedAutomationRunErrorResponse struct {
	Message string                      `json:"message"`
	Error   string                      `json:"error"`
	Result  trustedorchestration.Result `json:"result"`
}

// RestPostTrustedAutomationRun godoc
// @ID PostTrustedAutomationRun
// @Summary Run LLM_Op Safeguard before the geon Revision 1 workflow
// @Description Review the exact request with LLM_Op, stop non-approved requests, and run geon requirement analysis, resource recommendation, Agent decision, and Guards only after approval. This endpoint does not submit to AppDeploy.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body TrustedAutomationRunRequest true "Guard-first automation request"
// @Success 201 {object} trustedorchestration.Result
// @Success 200 {object} trustedorchestration.Result "Safeguard or geon terminal decision"
// @Failure 400 {object} TrustedAutomationRunErrorResponse
// @Router /api/v1/agent-control/trusted-automation-runs [post]
func (handler restHandler) RestPostTrustedAutomationRun(context echo.Context) error {
	var request TrustedAutomationRunRequest
	if message, err := bindAndValidate(context, &request); err != nil {
		return jsonError(context, http.StatusBadRequest, message, err)
	}
	result, err := handler.service.RunTrustedAutomation(
		context.Request().Context(),
		request,
	)
	if err != nil {
		return context.JSON(http.StatusBadRequest, TrustedAutomationRunErrorResponse{
			Message: "Guard-first automation request could not be processed",
			Error:   err.Error(),
			Result:  result,
		})
	}
	if result.Status == trustedorchestration.StatusApprovedFlowReady {
		return context.JSON(http.StatusCreated, result)
	}
	return context.JSON(http.StatusOK, result)
}

func (service Service) RunTrustedAutomation(
	ctx context.Context,
	request TrustedAutomationRunRequest,
) (trustedorchestration.Result, error) {
	if service.trustedOrchestration == nil {
		return trustedorchestration.Result{}, fmt.Errorf("trusted orchestration is not configured")
	}
	input, err := buildTrustedOrchestrationInput(request)
	if err != nil {
		return trustedorchestration.Result{}, err
	}
	return service.trustedOrchestration.RunApprovedFlow(ctx, input)
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
