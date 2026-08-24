package llmopbridge

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

// TrustedAppVersionBinding is the result of resolving one Common JSON
// application identity against the trusted AppDeploy application registry.
// AppVersionID is deliberately not accepted from the untrusted Flow.
type TrustedAppVersionBinding struct {
	AppID        string `json:"app_id"`
	AppVersion   string `json:"app_version"`
	AppVersionID string `json:"app_version_id"`
}

// TrustedAppVersionResolver binds the Common JSON app_id/app_version pair to
// the AppDeploy app_version_id namespace. Production callers must implement
// this with an authoritative registry lookup, not a value copied from an LLM.
type TrustedAppVersionResolver func(
	appID string,
	appVersion string,
) (TrustedAppVersionBinding, error)

// ApprovedInitialFlowInput combines the original natural-language analysis
// message and its pre-geon LLM_Op safeguard result with the post-decision Flow.
// Request is the exact request reviewed before geon, except that this bridge
// subsequently enriches it with trusted PlanningConstraints. The downstream
// PrepareApproved stage verifies this continuation and permits only that
// enrichment without making another safeguard-model call. This pre-geon
// evidence remains an audit binding, never authorization.
type ApprovedInitialFlowInput struct {
	Request         llmop.Request
	Safeguard       llmop.SafeguardStageResult
	AnalysisRequest agentcontrol.ApplicationAnalysisRequestEnvelope
	Flow            agentcontrol.Flow
}

// ApprovedInitialFlowEvidence is the minimum immutable handoff provenance
// needed to prove which approved geon decision and Manifest revision produced
// the LLM_Op request. The LLM completion candidate and infrastructure resource
// candidate remain explicitly separate namespaces. This bridge validates the
// public continuation's shape and cross-stage identities, then preserves it as
// audit evidence. It cannot recompute the private normalized/policy/candidate
// binding inputs; PrepareApproved is responsible for cryptographic binding
// recomputation before downstream generation.
type ApprovedInitialFlowEvidence struct {
	DecisionID                  string                             `json:"decision_id"`
	Revision                    int                                `json:"revision"`
	Phase                       string                             `json:"phase"`
	TriggerAction               string                             `json:"trigger_action"`
	DeploymentRequestMessageID  string                             `json:"deployment_request_message_id"`
	DeploymentRequestID         string                             `json:"deployment_request_id"`
	ManifestID                  string                             `json:"manifest_id"`
	ManifestFingerprint         string                             `json:"manifest_fingerprint"`
	AppID                       string                             `json:"app_id"`
	AppVersion                  string                             `json:"app_version"`
	AppVersionID                string                             `json:"app_version_id"`
	LLMCandidateID              string                             `json:"llm_candidate_id"`
	SelectedResourceCandidateID string                             `json:"selected_resource_candidate_id"`
	SafeguardContinuation       llmop.ApprovedContinuationEvidence `json:"safeguard_continuation"`
	Projection                  Evidence                           `json:"projection"`
}

type ApprovedInitialFlowProjection struct {
	Request   llmop.Request                `json:"request"`
	Safeguard llmop.SafeguardStageResult  `json:"safeguard"`
	Evidence  ApprovedInitialFlowEvidence `json:"evidence"`
}

// ProjectApprovedInitialFlow is the production integration boundary between
// geon's approved initial planning Flow and the bounded LLM_Op prepare-only
// contract. It fails closed on incomplete, rejected, stale, replanned, or
// post-deployment/optimized Flows. Project remains available for pre-decision
// compatibility and the manual demo, but must not be used as the authoritative
// post-decision handoff.
func ProjectApprovedInitialFlow(
	input ApprovedInitialFlowInput,
	resolve TrustedAppVersionResolver,
) (ApprovedInitialFlowProjection, error) {
	if resolve == nil {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("trusted AppDeploy app version resolver is required")
	}
	if err := validateApprovedSafeguard(input.Request, input.Safeguard); err != nil {
		return ApprovedInitialFlowProjection{}, err
	}
	if input.Request.Application.UserRequest != input.AnalysisRequest.Data.Application.UserRequest {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("pre-geon safeguarded user request must exactly match the analysis request")
	}
	if input.Request.CorrelationID != input.AnalysisRequest.CorrelationID ||
		input.Request.TraceID != input.AnalysisRequest.TraceID {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("pre-geon safeguarded correlation and trace identifiers must exactly match the analysis request")
	}

	revision, err := validateApprovedInitialFlow(input.AnalysisRequest, input.Flow)
	if err != nil {
		return ApprovedInitialFlowProjection{}, err
	}
	profile := input.Flow.ApplicationContext.Data.ApplicationProfile
	binding, err := resolve(profile.AppID, profile.AppVersion)
	if err != nil {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("resolve trusted AppDeploy app version: %w", err)
	}
	if err := validateTrustedAppVersionBinding(profile, binding); err != nil {
		return ApprovedInitialFlowProjection{}, err
	}
	if binding.AppVersionID != input.Request.Application.AppVersionID {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("trusted AppDeploy app_version_id does not match the pre-geon safeguarded request")
	}

	request := input.Request
	projection, err := Project(Input{
		Request:                request,
		AnalysisRequest:        input.AnalysisRequest,
		ApplicationContext:     *input.Flow.ApplicationContext,
		ResourceRecommendation: *input.Flow.ResourceRecommendation,
	})
	if err != nil {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("project approved Flow into llmop request: %w", err)
	}
	resourceCandidateID := projection.Evidence.SelectedResourceCandidateID
	if projection.Request.CandidateID == resourceCandidateID {
		return ApprovedInitialFlowProjection{}, fmt.Errorf("llm_candidate_id and selected_resource_candidate_id must be separate bindings")
	}
	fingerprint, err := fingerprintApprovedRevision(input.Flow, revision)
	if err != nil {
		return ApprovedInitialFlowProjection{}, err
	}
	deploymentRequest := revision.DeploymentRequest.Data.DeploymentRequest
	continuation := copyApprovedContinuation(*input.Safeguard.Continuation)

	return ApprovedInitialFlowProjection{
		Request:   projection.Request,
		Safeguard: copyApprovedSafeguard(input.Safeguard),
		Evidence: ApprovedInitialFlowEvidence{
			DecisionID:                  input.Flow.Decision.DecisionID,
			Revision:                    revision.Revision,
			Phase:                       revision.Phase,
			TriggerAction:               revision.TriggerAction,
			DeploymentRequestMessageID:  revision.DeploymentRequest.MessageID,
			DeploymentRequestID:         deploymentRequest.RequestID,
			ManifestID:                  deploymentRequest.DeploymentManifest.ManifestID,
			ManifestFingerprint:         fingerprint,
			AppID:                       binding.AppID,
			AppVersion:                  binding.AppVersion,
			AppVersionID:                binding.AppVersionID,
			LLMCandidateID:               projection.Request.CandidateID,
			SelectedResourceCandidateID: resourceCandidateID,
			SafeguardContinuation:        continuation,
			Projection:                   projection.Evidence,
		},
	}, nil
}

func validateApprovedInitialFlow(
	analysis agentcontrol.ApplicationAnalysisRequestEnvelope,
	flow agentcontrol.Flow,
) (agentcontrol.ManifestRevision, error) {
	if flow.State != agentcontrol.StateDecisionApproved {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Flow state must be DEPLOY_APPROVED")
	}
	if err := requireLLMOpStrongIdentifier("Flow correlation_id", flow.CorrelationID); err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	if err := requireLLMOpStrongIdentifier("Flow trace_id", flow.TraceID); err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	if flow.Decision == nil || flow.Decision.Action != agentcontrol.ActionDeploy {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Flow decision must be DEPLOY")
	}
	if strings.TrimSpace(flow.Decision.Reason) == "" ||
		strings.TrimSpace(flow.Decision.ReasoningMode) == "" ||
		flow.Decision.Confidence < 0 || flow.Decision.Confidence > 1 ||
		flow.Decision.Confidence != flow.Decision.Confidence ||
		flow.Decision.CorrectionRequest != nil {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("approved DEPLOY decision semantics are invalid")
	}
	if err := requireLLMOpStrongIdentifier("Flow decision_id", flow.Decision.DecisionID); err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	if flow.Guard == nil || flow.Guard.Status != agentcontrol.GuardApproved {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Flow Guard must be APPROVED")
	}
	if len(flow.Guard.Checks) == 0 || len(flow.Guard.Issues) != 0 {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("approved Flow Guard must contain passed checks and no issues")
	}
	for _, check := range flow.Guard.Checks {
		if strings.TrimSpace(check.Name) == "" || !check.Passed {
			return agentcontrol.ManifestRevision{}, fmt.Errorf("every approved Flow Guard check must be named and passed")
		}
	}
	if flow.AgentAuthorization == nil || !flow.AgentAuthorization.Authorized {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Flow automation Agent authorization must be present and authorized")
	}
	if err := requireBridgeIdentifier(
		"Flow authorized decision agent name",
		flow.AgentAuthorization.AgentName,
	); err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	if flow.AgentAuthorization.Capability != agentcontrol.AutomationCapability ||
		flow.AgentAuthorization.Action != agentcontrol.AutomationDecisionAction {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Flow automation Agent authorization binding is invalid")
	}
	if flow.RequestedDecisionAgent != "" {
		if err := requireBridgeIdentifier("Flow requested decision agent", flow.RequestedDecisionAgent); err != nil {
			return agentcontrol.ManifestRevision{}, err
		}
		if flow.RequestedDecisionAgent != flow.AgentAuthorization.AgentName || flow.AgentExecution == nil {
			return agentcontrol.ManifestRevision{}, fmt.Errorf("requested decision Agent must match authorization and completed execution evidence")
		}
	}
	if flow.AgentExecution == nil &&
		(flow.RequestedDecisionAgent != "" ||
			flow.AgentAuthorization.AgentName != agentcontrol.AutomationAgentName) {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("legacy Flow without execution evidence must use the built-in automation Agent")
	}
	if flow.AgentExecution != nil {
		if err := requireBridgeIdentifier("Flow decision Agent execution source", flow.AgentExecution.Source); err != nil {
			return agentcontrol.ManifestRevision{}, err
		}
		executionDecision := flow.AgentExecution.Decision
		if flow.AgentExecution.AgentName != flow.AgentAuthorization.AgentName ||
			(flow.RequestedDecisionAgent != "" && flow.AgentExecution.AgentName != flow.RequestedDecisionAgent) ||
			flow.AgentExecution.Status != "completed" ||
			executionDecision.Action != flow.Decision.Action ||
			(executionDecision.DecisionID != "" &&
				executionDecision.DecisionID != flow.Decision.DecisionID) ||
			executionDecision.Reason != flow.Decision.Reason ||
			executionDecision.Confidence != flow.Decision.Confidence ||
			executionDecision.SelectedCandidateID != flow.Decision.SelectedCandidateID ||
			!reflect.DeepEqual(executionDecision.CorrectionRequest, flow.Decision.CorrectionRequest) ||
			(strings.TrimSpace(executionDecision.ReasoningMode) != "" &&
				executionDecision.ReasoningMode != flow.Decision.ReasoningMode) {
			return agentcontrol.ManifestRevision{}, fmt.Errorf("Decision Agent execution evidence does not match the approved Flow")
		}
		if err := validateApprovedAgentGuard("request", flow.AgentExecution.RequestGuard); err != nil {
			return agentcontrol.ManifestRevision{}, err
		}
		if err := validateApprovedAgentGuard("result", flow.AgentExecution.ResultGuard); err != nil {
			return agentcontrol.ManifestRevision{}, err
		}
	}
	if flow.ApplicationContext == nil || flow.ResourceRecommendation == nil {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("approved Flow must retain application context and resource recommendation")
	}
	profile := flow.ApplicationContext.Data.ApplicationProfile
	recommendation := flow.ResourceRecommendation.Data.ResourceRecommendation
	if profile.Artifact == nil ||
		!reflect.DeepEqual(*profile.Artifact, analysis.Data.Application.Artifact) {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("ApplicationProfile artifact must match the original analysis request")
	}
	if err := validateExplicitResourceProvenance(profile.Analysis); err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	if flow.ProfileID != profile.ProfileID || recommendation.ProfileID != profile.ProfileID {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Flow, ApplicationProfile, and ResourceRecommendation profile_id values must match")
	}
	if flow.CorrelationID != analysis.CorrelationID ||
		flow.CorrelationID != flow.ApplicationContext.CorrelationID ||
		flow.CorrelationID != flow.ResourceRecommendation.CorrelationID {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("approved Flow correlation_id chain does not match")
	}
	if flow.TraceID != analysis.TraceID ||
		flow.TraceID != flow.ApplicationContext.TraceID ||
		flow.TraceID != flow.ResourceRecommendation.TraceID {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("approved Flow trace_id chain does not match")
	}
	if flow.Decision.SelectedCandidateID == "" ||
		flow.Decision.SelectedCandidateID != recommendation.SelectedCandidateID {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("DEPLOY decision selected candidate must match ResourceRecommendation")
	}
	if flow.DeploymentPlan == nil ||
		flow.DeploymentPlan.ProfileID != profile.ProfileID ||
		flow.DeploymentPlan.AppID != profile.AppID ||
		flow.DeploymentPlan.AppVersion != profile.AppVersion ||
		flow.DeploymentPlan.SelectedCandidateID != recommendation.SelectedCandidateID {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("DeploymentPlan identity must match the approved planning inputs")
	}
	selectedCandidate, err := approvedSelectedCandidate(recommendation)
	if err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	if !reflect.DeepEqual(flow.DeploymentPlan.DesiredInfrastructure, selectedCandidate.DesiredInfrastructure) ||
		!reflect.DeepEqual(flow.DeploymentPlan.ResourceHints, selectedCandidate.ResourceHints) ||
		!reflect.DeepEqual(
			flow.DeploymentPlan.InferenceConfiguration,
			flow.ApplicationContext.Data.ModelRecommendation.InferenceConfiguration,
		) {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("DeploymentPlan must be a lossless projection of the selected resource and model recommendation")
	}

	if len(flow.ManifestRevisions) != 1 {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("only one initial Manifest revision is accepted")
	}
	revision := flow.ManifestRevisions[0]
	if revision.Revision != 1 || revision.Phase != agentcontrol.ManifestPhaseInitial ||
		revision.TriggerAction != agentcontrol.ActionDeploy {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Manifest revision must be revision 1, INITIAL, and triggered by DEPLOY")
	}
	if _, err := time.Parse(time.RFC3339Nano, revision.CreatedAt); err != nil {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("Manifest revision created_at must be RFC3339: %w", err)
	}
	if flow.DesiredDeploymentSpec == nil || flow.DeploymentRequest == nil {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("approved Flow must expose an active DesiredDeploymentSpec and deployment request")
	}
	if !reflect.DeepEqual(revision.DesiredDeploymentSpec, *flow.DesiredDeploymentSpec) ||
		!reflect.DeepEqual(revision.DeploymentRequest, *flow.DeploymentRequest) {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("latest Manifest revision must exactly match the active Flow request")
	}
	if flow.DeploymentStatus != nil || flow.OptimizationFeedback != nil ||
		flow.FeedbackSummary != nil || flow.OperationAgentExecution != nil ||
		flow.ScalingDecision != nil {
		return agentcontrol.ManifestRevision{}, fmt.Errorf("post-deployment or optimization state is outside the initial LLM_Op handoff boundary")
	}
	if err := validateApprovedRevisionIdentity(flow, revision); err != nil {
		return agentcontrol.ManifestRevision{}, err
	}
	return revision, nil
}

func approvedSelectedCandidate(
	recommendation agentcontrol.ResourceRecommendation,
) (agentcontrol.ResourceCandidate, error) {
	var selected agentcontrol.ResourceCandidate
	matches := 0
	for _, candidate := range recommendation.Candidates {
		if candidate.CandidateID == recommendation.SelectedCandidateID {
			selected = candidate
			matches++
		}
	}
	if matches != 1 || !selected.Feasible {
		return agentcontrol.ResourceCandidate{}, fmt.Errorf("approved ResourceRecommendation must contain exactly one feasible selected candidate")
	}
	return selected, nil
}

func validateApprovedSafeguard(
	request llmop.Request,
	stage llmop.SafeguardStageResult,
) error {
	if stage.APIVersion != llmop.APIVersion || stage.Stage != llmop.SafeguardStageName {
		return fmt.Errorf("pre-geon safeguard API and stage binding is invalid")
	}
	if !stage.Approved || stage.Status != llmop.StatusSafeguardApproved ||
		!stage.RequestGuard.Valid || stage.RequestGuard.Status != "approved" {
		return fmt.Errorf("pre-geon deterministic and LLM safeguard must be approved")
	}
	if stage.Decision.Action != llmop.SafeguardDecisionAllow ||
		stage.Review == nil || stage.Review.Decision != llmop.SafeguardDecisionAllow {
		return fmt.Errorf("pre-geon safeguard decision and review must both allow_request")
	}
	if stage.Continuation == nil {
		return fmt.Errorf("pre-geon safeguard continuation evidence is required")
	}
	continuation := stage.Continuation
	if request.APIVersion != llmop.APIVersion || request.Policy.Mode != llmop.ModePrepareOnly ||
		strings.TrimSpace(request.Policy.ApprovalReference) != "" ||
		request.Application.PlanningConstraints != nil {
		return fmt.Errorf("pre-geon request must use the bounded prepare_only contract")
	}
	if stage.RequestID != request.RequestID || stage.CorrelationID != request.CorrelationID ||
		stage.TraceID != request.TraceID || continuation.RequestID != request.RequestID ||
		continuation.CorrelationID != request.CorrelationID || continuation.TraceID != request.TraceID {
		return fmt.Errorf("pre-geon safeguard request, correlation, and trace identifiers do not match")
	}
	if continuation.Stage != llmop.SafeguardContinuationStageName ||
		continuation.CandidateID != request.CandidateID ||
		stage.Review.CandidateID != request.CandidateID ||
		continuation.ReviewDecision != llmop.SafeguardDecisionAllow ||
		continuation.ReviewDecision != stage.Review.Decision ||
		continuation.ReviewReasonCode != stage.Review.ReasonCode ||
		stage.Decision.ReasonCode != stage.Review.ReasonCode ||
		!reflect.DeepEqual(stage.Decision.Confidence, stage.Review.Confidence) ||
		!reflect.DeepEqual(continuation.Confidence, stage.Review.Confidence) ||
		continuation.PolicyVersion == "" ||
		continuation.PolicyVersion != stage.RequestGuard.PolicyVersion ||
		continuation.ObservationStatus != stage.Input.ObservationStatus ||
		stage.Decision.ObservationStatus != stage.Input.ObservationStatus ||
		continuation.SubmissionMode != "not_submitted" {
		return fmt.Errorf("pre-geon safeguard continuation binding surface does not match the request and review")
	}
	if len(stage.RequestGuard.Checks) == 0 {
		return fmt.Errorf("pre-geon safeguard request guard must include passed checks")
	}
	for _, check := range stage.RequestGuard.Checks {
		if strings.TrimSpace(check.Name) == "" || !check.Passed {
			return fmt.Errorf("every pre-geon safeguard request guard check must be named and passed")
		}
	}
	// The bridge can enforce encoding and identity joins only. PrepareApproved
	// recomputes this digest from the exact reviewed request, normalization,
	// policy, candidate configuration, and review before treating it as valid.
	if continuation.BindingAlgorithm != llmop.SafeguardBindingAlgorithm ||
		len(continuation.RequestBinding) != sha256.Size*2 ||
		continuation.RequestBinding != strings.ToLower(continuation.RequestBinding) {
		return fmt.Errorf("pre-geon safeguard request binding must be lowercase sha256 hex")
	}
	decoded, err := hex.DecodeString(continuation.RequestBinding)
	if err != nil || len(decoded) != sha256.Size {
		return fmt.Errorf("pre-geon safeguard request binding must be lowercase sha256 hex")
	}
	if err := requireLLMOpStrongIdentifier("pre-geon app_version_id", request.Application.AppVersionID); err != nil {
		return err
	}
	return nil
}

func validateApprovedAgentGuard(label string, guard agentcontrol.GuardResult) error {
	if guard.Status != agentcontrol.GuardApproved || len(guard.Checks) == 0 || len(guard.Issues) != 0 {
		return fmt.Errorf("Decision Agent %s guard must contain only approved checks", label)
	}
	for _, check := range guard.Checks {
		if strings.TrimSpace(check.Name) == "" || !check.Passed {
			return fmt.Errorf("Decision Agent %s guard must contain only approved checks", label)
		}
	}
	return nil
}

func validateExplicitResourceProvenance(analysis agentcontrol.AnalysisSummary) error {
	if len(analysis.MissingFields) != 0 {
		return fmt.Errorf("ApplicationProfile contains missing fields; clarification is required before LLM_Op handoff")
	}
	if len(analysis.Assumptions) != 0 || len(analysis.Warnings) != 0 {
		return fmt.Errorf("ApplicationProfile contains assumptions or warnings; clarification is required before LLM_Op handoff")
	}
	return nil
}

func copyApprovedContinuation(
	evidence llmop.ApprovedContinuationEvidence,
) llmop.ApprovedContinuationEvidence {
	copy := evidence
	if evidence.Confidence != nil {
		confidence := *evidence.Confidence
		copy.Confidence = &confidence
	}
	return copy
}

func copyApprovedSafeguard(
	stage llmop.SafeguardStageResult,
) llmop.SafeguardStageResult {
	copy := stage
	copy.Decision.Confidence = copyFloat64(stage.Decision.Confidence)
	copy.RequestGuard.Checks = append(
		[]plannerguard.Check(nil),
		stage.RequestGuard.Checks...,
	)
	copy.Input.StaleSources = append([]string(nil), stage.Input.StaleSources...)
	if stage.Review != nil {
		review := *stage.Review
		review.Confidence = copyFloat64(stage.Review.Confidence)
		copy.Review = &review
	}
	if stage.Continuation != nil {
		continuation := copyApprovedContinuation(*stage.Continuation)
		copy.Continuation = &continuation
	}
	return copy
}

func copyFloat64(value *float64) *float64 {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}

func validateApprovedRevisionIdentity(
	flow agentcontrol.Flow,
	revision agentcontrol.ManifestRevision,
) error {
	profile := flow.ApplicationContext.Data.ApplicationProfile
	plan := flow.DeploymentPlan
	decisionID := flow.Decision.DecisionID
	spec := revision.DesiredDeploymentSpec
	requestEnvelope := revision.DeploymentRequest
	request := requestEnvelope.Data.DeploymentRequest
	manifest := request.DeploymentManifest

	if err := validateEnvelope(requestEnvelope.Envelope, agentcontrol.MessageDeploymentCreateRequest); err != nil {
		return fmt.Errorf("active deployment request envelope: %w", err)
	}
	if requestEnvelope.CorrelationID != flow.CorrelationID || requestEnvelope.TraceID != flow.TraceID ||
		requestEnvelope.CausationID != flow.ResourceRecommendation.MessageID {
		return fmt.Errorf("active deployment request identity chain does not match the approved Flow")
	}
	if err := requireLLMOpStrongIdentifier("deployment request_id", request.RequestID); err != nil {
		return err
	}
	if err := requireLLMOpStrongIdentifier("deployment manifest_id", manifest.ManifestID); err != nil {
		return err
	}
	if spec.SpecVersion != agentcontrol.ContractVersionV1 || manifest.ManifestVersion != spec.SpecVersion {
		return fmt.Errorf("DesiredDeploymentSpec and Manifest schema versions must match Common JSON v1")
	}
	if spec.DecisionID != decisionID || request.DecisionID != decisionID || manifest.DecisionID != decisionID {
		return fmt.Errorf("decision_id must match across Flow, spec, request, and Manifest")
	}
	if spec.Metadata.ProfileID != profile.ProfileID || manifest.Metadata.ProfileID != profile.ProfileID {
		return fmt.Errorf("profile_id must match across Flow, spec, and Manifest")
	}
	if spec.Application.AppID != profile.AppID || spec.Application.AppVersion != profile.AppVersion ||
		request.Application.AppID != profile.AppID || request.Application.AppVersion != profile.AppVersion ||
		manifest.Application.AppID != profile.AppID || manifest.Application.AppVersion != profile.AppVersion {
		return fmt.Errorf("application identity must match across profile, spec, request, and Manifest")
	}
	if !reflect.DeepEqual(request.Application.Artifact, profile.Artifact) {
		return fmt.Errorf("deployment request artifact must match the approved ApplicationProfile")
	}
	if plan == nil || plan.TargetRuntime != spec.TargetRuntime ||
		!reflect.DeepEqual(plan.DesiredInfrastructure, spec.DesiredInfrastructure) ||
		!reflect.DeepEqual(plan.InferenceConfiguration, spec.InferenceConfiguration) ||
		!reflect.DeepEqual(plan.ResourceHints, spec.PolicyHints) {
		return fmt.Errorf("DesiredDeploymentSpec must be a lossless projection of the approved DeploymentPlan")
	}
	if !reflect.DeepEqual(spec.Application, manifest.Application) ||
		spec.TargetRuntime != manifest.TargetRuntime ||
		!reflect.DeepEqual(spec.DesiredInfrastructure, manifest.DesiredInfrastructure) ||
		!reflect.DeepEqual(spec.InferenceConfiguration, manifest.InferenceConfiguration) ||
		!reflect.DeepEqual(spec.Runtime, manifest.Runtime) ||
		!reflect.DeepEqual(spec.PolicyHints, manifest.ResourceHints) {
		return fmt.Errorf("active deployment Manifest must be a lossless projection of DesiredDeploymentSpec")
	}
	return nil
}

func validateTrustedAppVersionBinding(
	profile agentcontrol.ApplicationProfile,
	binding TrustedAppVersionBinding,
) error {
	if binding.AppID != profile.AppID || binding.AppVersion != profile.AppVersion {
		return fmt.Errorf("trusted AppDeploy app version binding does not match ApplicationProfile")
	}
	if err := requireLLMOpStrongIdentifier("trusted AppDeploy app_version_id", binding.AppVersionID); err != nil {
		return err
	}
	return nil
}

func fingerprintApprovedRevision(
	flow agentcontrol.Flow,
	revision agentcontrol.ManifestRevision,
) (string, error) {
	evidence := struct {
		CorrelationID string                          `json:"correlation_id"`
		TraceID       string                          `json:"trace_id"`
		ProfileID     string                          `json:"profile_id"`
		Decision      agentcontrol.AutomationDecision `json:"decision"`
		Revision      agentcontrol.ManifestRevision    `json:"revision"`
	}{
		CorrelationID: flow.CorrelationID,
		TraceID:       flow.TraceID,
		ProfileID:     flow.ProfileID,
		Decision:      *flow.Decision,
		Revision:      revision,
	}
	encoded, err := json.Marshal(evidence)
	if err != nil {
		return "", fmt.Errorf("fingerprint approved Manifest revision: %w", err)
	}
	digest := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}
