package api

import (
	"context"
	"errors"
	"fmt"
	"time"
	"unicode/utf8"

	"kyunghee-aiops/service-control-api/internal/audittrail"
	"kyunghee-aiops/service-control-api/internal/trustedorchestration"
)

func buildTrustedAuditRequestSummary(
	request TrustedAutomationRunRequest,
) (audittrail.RequestSummary, error) {
	payloadSHA256, err := audittrail.DigestJSON(request)
	if err != nil {
		return audittrail.RequestSummary{}, err
	}
	inputType := request.Input.InputType
	if inputType != "natural_language" && inputType != "structured" {
		inputType = "invalid"
	}
	return audittrail.RequestSummary{
		InputType:             inputType,
		CandidateIDSHA256:     audittrail.DigestString(request.CandidateID),
		PayloadSHA256:         payloadSHA256,
		UserRequestRuneCount:  utf8.RuneCountInString(request.Input.Request),
		StructuredInput:       request.Input.AppSpec != nil,
		RequestedBySHA256:     audittrail.DigestString(request.Input.RequestedBy),
		AppVersionIDSHA256:    audittrail.DigestString(request.AppVersionID),
		RawPayloadStored:      false,
		RawModelContentStored: false,
	}, nil
}

func trustedAuditInputIdentity(input trustedorchestration.Input) audittrail.Identity {
	return audittrail.Identity{
		RequestID:     input.Request.RequestID,
		MessageID:     input.AnalysisRequest.MessageID,
		CorrelationID: input.Request.CorrelationID,
		TraceID:       input.Request.TraceID,
	}
}

func trustedAuditResultSummary(
	result trustedorchestration.Result,
) audittrail.ResultSummary {
	summary := audittrail.ResultSummary{
		FinalStatus:                result.Status,
		SafeguardStatus:            result.Safeguard.Status,
		SafeguardApproved:          result.Safeguard.Approved,
		SafeguardDecision:          result.Safeguard.Decision.Action,
		SafeguardReasonCode:        result.Safeguard.Decision.ReasonCode,
		IdempotentReplay:           result.IdempotentReplay,
		ApprovedProjectionPrepared: result.ApprovedProjection != nil,
	}
	if summary.FinalStatus == "" {
		summary.FinalStatus = "ERROR"
	}
	run := result.AutomationRun
	if run == nil {
		return summary
	}
	summary.AutomationStatus = run.Status
	if run.RequirementAnalysis != nil {
		summary.AnalysisMode = run.RequirementAnalysis.Mode
	}
	if run.Flow != nil {
		summary.FlowState = run.Flow.State
		if run.Flow.Guard != nil {
			summary.GuardStatus = run.Flow.Guard.Status
		}
		summary.RevisionCount = len(run.Flow.ManifestRevisions)
		if len(run.Flow.ManifestRevisions) > 0 {
			summary.LatestRevisionPhase = run.Flow.ManifestRevisions[len(run.Flow.ManifestRevisions)-1].Phase
		}
	}
	if run.DesiredDeploymentSpec != nil {
		digest, err := audittrail.DigestJSON(run.DesiredDeploymentSpec)
		if err == nil {
			summary.DesiredDeploymentSpecSHA256 = digest
		}
	}
	if run.DeploymentSubmission != nil {
		summary.DeploymentAdapter = run.DeploymentSubmission.Adapter
		summary.DeploymentSubmissionStatus = run.DeploymentSubmission.Status
		summary.DeploymentSimulated = run.DeploymentSubmission.Simulated
	}
	return summary
}

func trustedAutomationErrorCode(err error) string {
	switch {
	case err == nil:
		return ""
	case errors.Is(err, audittrail.ErrPersistence):
		return "AUDIT_PERSISTENCE_FAILED"
	case errors.Is(err, audittrail.ErrIdentityMismatch):
		return "AUDIT_IDENTITY_MISMATCH"
	case errors.Is(err, context.Canceled):
		return "REQUEST_CANCELED"
	case errors.Is(err, context.DeadlineExceeded):
		return "REQUEST_DEADLINE_EXCEEDED"
	default:
		return "TRUSTED_AUTOMATION_FAILED"
	}
}

func trustedAuditFailure(stage string, err error) *audittrail.Failure {
	if err == nil {
		return nil
	}
	return &audittrail.Failure{
		Stage: stage,
		Code:  trustedAutomationErrorCode(err),
	}
}

func degradedAuditReference() audittrail.Reference {
	auditID, _ := audittrail.NewAuditID()
	return audittrail.Reference{
		SchemaVersion:     audittrail.SchemaVersion,
		AuditID:           auditID,
		PersistenceStatus: audittrail.PersistenceDegraded,
	}
}

func finalizeTrustedAudit(
	session *audittrail.Session,
	result trustedorchestration.Result,
	runErr error,
	failureStage string,
	completedAt time.Time,
) (audittrail.Reference, error) {
	if session == nil {
		return degradedAuditReference(), fmt.Errorf("%w: audit session is unavailable", audittrail.ErrPersistence)
	}
	outcome := "completed"
	if runErr != nil {
		outcome = "failed"
	} else {
		switch result.Status {
		case trustedorchestration.StatusSafeguardStopped:
			outcome = "stopped"
		case trustedorchestration.StatusGeonRejected:
			outcome = "rejected"
		case trustedorchestration.StatusApprovedFlowReady:
			outcome = "approved"
		}
	}
	return session.Finalize(audittrail.FinalizeMetadata{
		CompletedAt:     completedAt,
		TerminalOutcome: outcome,
		ErrorCode:       trustedAutomationErrorCode(runErr),
		Result:          trustedAuditResultSummary(result),
		Failure:         trustedAuditFailure(failureStage, runErr),
	})
}
