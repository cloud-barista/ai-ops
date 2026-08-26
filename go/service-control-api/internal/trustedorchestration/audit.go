package trustedorchestration

import (
	"context"
	"fmt"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/audittrail"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/llmopbridge"
)

func recordStageStarted(
	ctx context.Context,
	stage string,
	action string,
	input any,
) error {
	digest, err := audittrail.DigestJSON(input)
	if err != nil {
		return err
	}
	return audittrail.Record(ctx, audittrail.Event{
		Stage:   stage,
		Action:  action,
		Outcome: "started",
		Evidence: audittrail.Evidence{
			InputSHA256: digest,
		},
	})
}

func recordSafeguardReview(
	ctx context.Context,
	startedAt time.Time,
	request llmop.Request,
	stage llmop.SafeguardStageResult,
	outcome string,
	errorCode string,
) error {
	inputDigest, err := audittrail.DigestJSON(request)
	if err != nil {
		return err
	}
	outputDigest, err := audittrail.DigestJSON(stage)
	if err != nil {
		return err
	}
	evidence := audittrail.Evidence{
		InputSHA256:         inputDigest,
		OutputSHA256:        outputDigest,
		PolicyVersion:       audittrail.SafeLabel(stage.RequestGuard.PolicyVersion),
		Decision:            stage.Decision.Action,
		ReasonCode:          stage.Decision.ReasonCode,
		Confidence:          stage.Decision.Confidence,
		Approved:            audittrail.Bool(stage.Approved),
		RequestGuardStatus:  stage.RequestGuard.Status,
		RequestGuardValid:   audittrail.Bool(stage.RequestGuard.Valid),
	}
	if stage.Review != nil {
		evidence.CandidateIDSHA256 = audittrail.DigestString(stage.Review.CandidateID)
		evidence.Provider = audittrail.SafeLabel(stage.Review.Provider)
		evidence.ActualModel = audittrail.SafeLabel(stage.Review.ActualModel)
		evidence.ModelLatencyMS = stage.Review.LatencyMS
	}
	if stage.Continuation != nil {
		evidence.RequestBinding = stage.Continuation.RequestBinding
		evidence.BindingAlgorithm = stage.Continuation.BindingAlgorithm
		evidence.SubmissionMode = stage.Continuation.SubmissionMode
	}
	return audittrail.Record(ctx, audittrail.Event{
		Stage:      audittrail.StageSafeguard,
		Action:     "review_completed",
		Outcome:    outcome,
		DurationMS: elapsedMilliseconds(startedAt),
		ErrorCode:  errorCode,
		Evidence:   evidence,
	})
}

func recordSafeguardBinding(
	ctx context.Context,
	stage llmop.SafeguardStageResult,
	outcome string,
	errorCode string,
) error {
	evidence := audittrail.Evidence{
		PolicyVersion:      audittrail.SafeLabel(stage.RequestGuard.PolicyVersion),
		Decision:           stage.Decision.Action,
		ReasonCode:         stage.Decision.ReasonCode,
		Approved:           audittrail.Bool(stage.Approved),
		RequestGuardStatus: stage.RequestGuard.Status,
		RequestGuardValid:  audittrail.Bool(stage.RequestGuard.Valid),
	}
	if stage.Continuation != nil {
		evidence.CandidateIDSHA256 = audittrail.DigestString(stage.Continuation.CandidateID)
		evidence.RequestBinding = stage.Continuation.RequestBinding
		evidence.BindingAlgorithm = stage.Continuation.BindingAlgorithm
		evidence.SubmissionMode = stage.Continuation.SubmissionMode
	}
	return audittrail.Record(ctx, audittrail.Event{
		Stage:     audittrail.StageSafeguard,
		Action:    "approval_binding_validated",
		Outcome:   outcome,
		ErrorCode: errorCode,
		Evidence:  evidence,
	})
}

func bindAutomationRunIdentity(ctx context.Context, run agentcontrol.AutomationRun) error {
	return audittrail.BindIdentity(ctx, audittrail.Identity{
		CorrelationID: run.CorrelationID,
		TraceID:       run.TraceID,
		RunID:         run.RunID,
	})
}

func recordGeonRun(
	ctx context.Context,
	startedAt time.Time,
	run agentcontrol.AutomationRun,
	replayed bool,
	outcome string,
	errorCode string,
) error {
	outputDigest, err := audittrail.DigestJSON(run)
	if err != nil {
		return err
	}
	evidence := audittrail.Evidence{
		OutputSHA256:      outputDigest,
		IdempotentReplay: replayed,
	}
	if analysis := run.RequirementAnalysis; analysis != nil {
		evidence.AnalysisMode = analysis.Mode
		evidence.CandidateIDSHA256 = audittrail.DigestString(analysis.Evidence.CandidateID)
		evidence.Provider = audittrail.SafeLabel(analysis.Evidence.Provider)
		evidence.ActualModel = audittrail.SafeLabel(analysis.Evidence.ActualModel)
		evidence.ModelLatencyMS = analysis.Evidence.LatencyMS
		requirements := analysis.ApplicationProfile.Requirements
		evidence.Resources = &audittrail.ResourceSummary{
			CPUCoresMin:                   requirements.Compute.CPUCoresMin,
			MemoryMiBMin:                  requirements.Compute.MemoryMiBMin,
			StorageGiBMin:                 requirements.Compute.StorageGiBMin,
			AcceleratorRequired:           requirements.Accelerator.Required,
			AcceleratorType:               normalizedAuditAcceleratorType(requirements.Accelerator.Required, requirements.Accelerator.Type),
			AcceleratorCountMin:           requirements.Accelerator.CountMin,
			AcceleratorMemoryMiBPerDevice: requirements.Accelerator.MemoryMiBMinPerDevice,
			ReplicasMin:                   requirements.Deployment.ReplicasMin,
			ReplicasMax:                   requirements.Deployment.ReplicasMax,
		}
	}
	if recommendation := run.ResourceRecommendation; recommendation != nil {
		evidence.CatalogVersion = audittrail.SafeLabel(recommendation.Evidence.CatalogVersion)
		evidence.RecommendationStatus = recommendation.ResourceRecommendation.Status
		evidence.SelectedResourceSHA256 = audittrail.DigestString(
			recommendation.ResourceRecommendation.SelectedCandidateID,
		)
	}
	if flow := run.Flow; flow != nil {
		evidence.FlowState = flow.State
		if flow.AgentAuthorization != nil {
			evidence.AgentName = audittrail.SafeLabel(flow.AgentAuthorization.AgentName)
			evidence.AgentAuthorized = audittrail.Bool(flow.AgentAuthorization.Authorized)
		}
		if flow.Guard != nil {
			evidence.GuardStatus = flow.Guard.Status
		}
		evidence.RevisionCount = len(flow.ManifestRevisions)
		if len(flow.ManifestRevisions) > 0 {
			evidence.LatestRevisionPhase = flow.ManifestRevisions[len(flow.ManifestRevisions)-1].Phase
		}
	}
	if run.DesiredDeploymentSpec != nil {
		evidence.DesiredDeploymentSpecSHA256, err = audittrail.DigestJSON(run.DesiredDeploymentSpec)
		if err != nil {
			return err
		}
	}
	if submission := run.DeploymentSubmission; submission != nil {
		evidence.DeploymentAdapter = submission.Adapter
		evidence.DeploymentSubmissionStatus = submission.Status
		evidence.DeploymentSubmissionSimulated = audittrail.Bool(submission.Simulated)
		if submission.Simulated {
			evidence.SubmissionMode = "simulated"
		} else {
			evidence.SubmissionMode = "handoff_ready"
		}
	} else {
		evidence.SubmissionMode = "not_submitted"
	}
	return audittrail.Record(ctx, audittrail.Event{
		Stage:      audittrail.StageGeon,
		Action:     "revision_flow_completed",
		Outcome:    outcome,
		DurationMS: elapsedMilliseconds(startedAt),
		ErrorCode:  errorCode,
		Evidence:   evidence,
	})
}

func recordApprovedProjection(
	ctx context.Context,
	startedAt time.Time,
	projection llmopbridge.ApprovedInitialFlowProjection,
	outcome string,
	errorCode string,
) error {
	outputDigest, err := audittrail.DigestJSON(projection)
	if err != nil {
		return err
	}
	return audittrail.Record(ctx, audittrail.Event{
		Stage:      audittrail.StageApprovedProjection,
		Action:     "prepare_only_projection_completed",
		Outcome:    outcome,
		DurationMS: elapsedMilliseconds(startedAt),
		ErrorCode:  errorCode,
		Evidence: audittrail.Evidence{
			OutputSHA256:  outputDigest,
			SubmissionMode: "not_submitted",
		},
	})
}

func elapsedMilliseconds(startedAt time.Time) int64 {
	duration := time.Since(startedAt)
	if duration <= 0 {
		return 0
	}
	return duration.Milliseconds()
}

func wrapAuditError(stage string, err error) error {
	return fmt.Errorf("record %s execution evidence: %w", stage, err)
}

func normalizedAuditAcceleratorType(required bool, value string) string {
	switch strings.ToUpper(strings.TrimSpace(value)) {
	case "GPU":
		return "GPU"
	case "NVIDIA_GPU":
		return "NVIDIA_GPU"
	case "", "NONE":
		if required {
			return "OTHER"
		}
		return "NONE"
	default:
		return "OTHER"
	}
}
