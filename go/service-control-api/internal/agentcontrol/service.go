package agentcontrol

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

type Reasoner interface {
	Propose(context.Context, string, ReasoningInput) (ModelReasoningResult, error)
}

type Authorizer interface {
	Authorize(context.Context, AgentAuthorizationRequest) (AgentAuthorization, error)
}

type Service struct {
	mu              sync.RWMutex
	flows           map[string]Flow
	now             func() time.Time
	reasoner        Reasoner
	authorizer      Authorizer
	decisionRuntime DecisionAgentRuntime
}

func NewService() *Service {
	return NewServiceWithReasoner(nil)
}

func NewServiceWithReasoner(reasoner Reasoner) *Service {
	return NewServiceWithDependencies(reasoner, nil)
}

func NewServiceWithDependencies(reasoner Reasoner, authorizer Authorizer) *Service {
	return NewServiceWithDecisionRuntime(reasoner, authorizer, nil)
}

func NewServiceWithDecisionRuntime(
	reasoner Reasoner,
	authorizer Authorizer,
	decisionRuntime DecisionAgentRuntime,
) *Service {
	return &Service{
		flows:           map[string]Flow{},
		now:             func() time.Time { return time.Now().UTC() },
		reasoner:        reasoner,
		authorizer:      authorizer,
		decisionRuntime: decisionRuntime,
	}
}

func (service *Service) ReceiveApplicationContext(
	ctx context.Context,
	message ApplicationContextEnvelope,
) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	if err := validateEnvelope(message.Envelope, MessageApplicationContextCreated); err != nil {
		return Flow{}, err
	}
	if err := validateApplicationContext(message.Data); err != nil {
		return Flow{}, err
	}

	service.mu.Lock()
	defer service.mu.Unlock()

	flow := service.flows[message.CorrelationID]
	if err := ensureFlowIdentity(flow, message.Envelope, message.Data.ApplicationProfile.ProfileID); err != nil {
		return Flow{}, err
	}
	messageCopy := cloneApplicationContextEnvelope(message)
	flow.CorrelationID = message.CorrelationID
	flow.TraceID = message.TraceID
	flow.ProfileID = message.Data.ApplicationProfile.ProfileID
	flow.ApplicationContext = &messageCopy
	flow.State = inputJoinState(flow)
	flow.UpdatedAt = service.now().Format(time.RFC3339Nano)
	flow = service.evaluateFlow(ctx, flow)
	service.flows[message.CorrelationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
}

func (service *Service) ReceiveResourceRecommendation(
	ctx context.Context,
	message ResourceRecommendationEnvelope,
) (Flow, error) {
	return service.ReceiveResourceRecommendationForAgent(ctx, message, "", "")
}

func (service *Service) ReceiveResourceRecommendationForAgent(
	ctx context.Context,
	message ResourceRecommendationEnvelope,
	requestedAgent string,
	runID string,
) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	if err := validateEnvelope(message.Envelope, MessageResourceRecommendationCreated); err != nil {
		return Flow{}, err
	}
	if err := validateResourceRecommendation(message.Data.ResourceRecommendation); err != nil {
		return Flow{}, err
	}

	service.mu.Lock()
	defer service.mu.Unlock()

	flow := service.flows[message.CorrelationID]
	if err := ensureFlowIdentity(flow, message.Envelope, message.Data.ResourceRecommendation.ProfileID); err != nil {
		return Flow{}, err
	}
	messageCopy := cloneResourceRecommendationEnvelope(message)
	flow.CorrelationID = message.CorrelationID
	flow.TraceID = message.TraceID
	flow.ProfileID = message.Data.ResourceRecommendation.ProfileID
	flow.ResourceRecommendation = &messageCopy
	flow.RequestedDecisionAgent = strings.TrimSpace(requestedAgent)
	flow.AutomationRunID = strings.TrimSpace(runID)
	flow.State = inputJoinState(flow)
	flow.UpdatedAt = service.now().Format(time.RFC3339Nano)
	flow = service.evaluateFlow(ctx, flow)
	service.flows[message.CorrelationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
}

func (service *Service) ReceiveDeploymentStatus(
	ctx context.Context,
	message DeploymentStatusEnvelope,
) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	if err := validateEnvelope(message.Envelope, MessageDeploymentStatusChanged); err != nil {
		return Flow{}, err
	}
	if err := validateDeploymentStatus(message.Data.DeploymentStatus); err != nil {
		return Flow{}, err
	}

	service.mu.Lock()
	defer service.mu.Unlock()

	flow, ok := service.flows[message.CorrelationID]
	if !ok {
		return Flow{}, fmt.Errorf("correlation_id does not identify an Agent Control flow")
	}
	if err := ensureFeedbackIdentity(
		flow,
		message.Envelope,
		message.Data.DeploymentStatus.DecisionID,
		message.Data.DeploymentStatus.DeploymentID,
	); err != nil {
		return Flow{}, err
	}
	messageCopy := cloneDeploymentStatusEnvelope(message)
	flow.DeploymentStatus = &messageCopy
	flow.FeedbackSummary = summarizeFeedback(flow)
	flow.ScalingDecision = evaluateScalingDecision(flow, service.now())
	flow.UpdatedAt = service.now().Format(time.RFC3339Nano)
	service.flows[message.CorrelationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
}

func (service *Service) ReceiveOptimizationFeedback(
	ctx context.Context,
	message OptimizationFeedbackEnvelope,
) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	if err := validateEnvelope(message.Envelope, MessageOptimizationFeedbackCreated); err != nil {
		return Flow{}, err
	}
	if err := validateOptimizationFeedback(message.Data.OptimizationFeedback); err != nil {
		return Flow{}, err
	}

	service.mu.Lock()
	defer service.mu.Unlock()

	flow, ok := service.flows[message.CorrelationID]
	if !ok {
		return Flow{}, fmt.Errorf("correlation_id does not identify an Agent Control flow")
	}
	feedback := message.Data.OptimizationFeedback
	if err := ensureFeedbackIdentity(
		flow,
		message.Envelope,
		feedback.DecisionID,
		feedback.DeploymentID,
	); err != nil {
		return Flow{}, err
	}
	if flow.DeploymentStatus == nil {
		return Flow{}, fmt.Errorf("deployment.status.changed must be received before optimization feedback")
	}
	messageCopy := cloneOptimizationFeedbackEnvelope(message)
	flow.OptimizationFeedback = &messageCopy
	flow.FeedbackSummary = summarizeFeedback(flow)
	flow.ScalingDecision = evaluateScalingDecision(flow, service.now())
	flow.UpdatedAt = service.now().Format(time.RFC3339Nano)
	service.flows[message.CorrelationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
}

func (service *Service) CompareReasoning(
	ctx context.Context,
	correlationID string,
	candidateID string,
) (ReasoningComparison, error) {
	if err := ctx.Err(); err != nil {
		return ReasoningComparison{}, err
	}
	correlationID = strings.TrimSpace(correlationID)
	candidateID = strings.TrimSpace(candidateID)
	if correlationID == "" {
		return ReasoningComparison{}, fmt.Errorf("correlation_id is required")
	}
	if candidateID == "" {
		return ReasoningComparison{}, fmt.Errorf("candidate_id is required")
	}

	service.mu.RLock()
	flow, ok := service.flows[correlationID]
	flow = cloneFlow(flow)
	service.mu.RUnlock()
	if !ok {
		return ReasoningComparison{}, fmt.Errorf("correlation_id does not identify an Agent Control flow")
	}
	if flow.ApplicationContext == nil || flow.ResourceRecommendation == nil || flow.Decision == nil {
		return ReasoningComparison{}, fmt.Errorf("joined inputs and an automation decision are required")
	}

	createdAt := service.now().Format(time.RFC3339Nano)
	comparison := ReasoningComparison{
		CorrelationID: correlationID,
		CandidateID:   candidateID,
		RuleBased: ReasoningTrial{
			Mode:                ReasoningModeRuleBased,
			ExecutionStatus:     ReasoningExecutionExecuted,
			Action:              flow.Decision.Action,
			SelectedCandidateID: flow.Decision.SelectedCandidateID,
			Reason:              flow.Decision.Reason,
			Confidence:          flow.Decision.Confidence,
			GuardStatus:         flow.Guard.Status,
			GuardReason:         "Deterministic requirements and resource compatibility checks.",
		},
		SimpleInference: ReasoningTrial{
			Mode:            ReasoningModeSimpleInference,
			ExecutionStatus: ReasoningExecutionSkipped,
			CandidateID:     candidateID,
			GuardStatus:     GuardNotApplied,
		},
		ValidatedInference: ReasoningTrial{
			Mode:            ReasoningModeValidatedInference,
			ExecutionStatus: ReasoningExecutionSkipped,
			CandidateID:     candidateID,
			GuardStatus:     GuardNotApplied,
		},
		CreatedAt: createdAt,
	}
	if service.reasoner == nil {
		comparison.SimpleInference.ExecutionStatus = ReasoningProviderUnavailable
		comparison.SimpleInference.Error = "reasoning provider is not configured"
		comparison.ValidatedInference.Error = "validation was skipped because no model proposal was produced"
		if err := service.storeReasoningComparison(flow, comparison); err != nil {
			return ReasoningComparison{}, err
		}
		return comparison, nil
	}

	modelResult, err := service.reasoner.Propose(ctx, candidateID, ReasoningInput{
		ApplicationProfile:     flow.ApplicationContext.Data.ApplicationProfile,
		ResourceRecommendation: flow.ResourceRecommendation.Data.ResourceRecommendation,
	})
	if strings.TrimSpace(modelResult.CandidateID) == "" {
		modelResult.CandidateID = candidateID
	}
	if err != nil {
		executionStatus := strings.TrimSpace(modelResult.ExecutionStatus)
		if executionStatus == "" || executionStatus == "not_executed" {
			executionStatus = ReasoningProviderUnavailable
		}
		comparison.SimpleInference = ReasoningTrial{
			Mode:            ReasoningModeSimpleInference,
			ExecutionStatus: executionStatus,
			CandidateID:     modelResult.CandidateID,
			Provider:        modelResult.Provider,
			ActualModel:     modelResult.ActualModel,
			LatencyMS:       modelResult.LatencyMS,
			GuardStatus:     GuardNotApplied,
			Error:           err.Error(),
		}
		comparison.ValidatedInference.Error = "validation was skipped because the provider did not return a proposal"
		if storeErr := service.storeReasoningComparison(flow, comparison); storeErr != nil {
			return ReasoningComparison{}, storeErr
		}
		return comparison, nil
	}

	proposal := modelResult.Proposal
	comparison.SimpleInference = trialFromProposal(
		ReasoningModeSimpleInference,
		modelResult,
		proposal,
		GuardNotApplied,
		"Raw Qwen proposal; no safety validation was applied.",
	)
	guard := validateReasoningProposal(flow, proposal)
	finalProposal := proposal
	if guard.Status != GuardApproved {
		finalProposal = ReasoningProposal{
			Action:              flow.Decision.Action,
			SelectedCandidateID: flow.Decision.SelectedCandidateID,
			Reason:              "Go Guard rejected the model proposal; use the deterministic safe decision.",
			Confidence:          flow.Decision.Confidence,
		}
	}
	comparison.ValidatedInference = trialFromProposal(
		ReasoningModeValidatedInference,
		modelResult,
		finalProposal,
		guard.Status,
		guardSummary(guard),
	)
	comparison.ValidatedInference.ProposedAction = proposal.Action
	comparison.Agreement = ReasoningAgreement{
		ActionMatch:    proposal.Action == flow.Decision.Action,
		CandidateMatch: proposal.SelectedCandidateID == flow.Decision.SelectedCandidateID,
	}
	if err := service.storeReasoningComparison(flow, comparison); err != nil {
		return ReasoningComparison{}, err
	}
	return comparison, nil
}

func trialFromProposal(
	mode string,
	modelResult ModelReasoningResult,
	proposal ReasoningProposal,
	guardStatus string,
	guardReason string,
) ReasoningTrial {
	return ReasoningTrial{
		Mode:                mode,
		ExecutionStatus:     ReasoningExecutionExecuted,
		CandidateID:         modelResult.CandidateID,
		Provider:            modelResult.Provider,
		ActualModel:         modelResult.ActualModel,
		LatencyMS:           modelResult.LatencyMS,
		Action:              proposal.Action,
		SelectedCandidateID: proposal.SelectedCandidateID,
		Reason:              proposal.Reason,
		Confidence:          proposal.Confidence,
		GuardStatus:         guardStatus,
		GuardReason:         guardReason,
	}
}

func validateReasoningProposal(flow Flow, proposal ReasoningProposal) GuardResult {
	checks := []GuardCheck{
		{
			Name:   "allowed_action",
			Passed: proposal.Action == ActionDeploy || proposal.Action == ActionReject || proposal.Action == ActionRetry,
			Reason: "Action must be DEPLOY, REJECT, or RETRY.",
		},
		{
			Name:   "reason",
			Passed: strings.TrimSpace(proposal.Reason) != "",
			Reason: "A model proposal must include its reason.",
		},
		{
			Name:   "confidence_range",
			Passed: proposal.Confidence >= 0 && proposal.Confidence <= 1,
			Reason: "Confidence must be between 0 and 1.",
		},
		{
			Name:   "safe_action_agreement",
			Passed: flow.Decision != nil && proposal.Action == flow.Decision.Action,
			Reason: "The model Action must agree with the deterministic safety decision.",
		},
	}
	if proposal.Action == ActionDeploy {
		checks = append(checks, GuardCheck{
			Name: "registered_candidate",
			Passed: flow.Decision != nil &&
				strings.TrimSpace(proposal.SelectedCandidateID) != "" &&
				proposal.SelectedCandidateID == flow.Decision.SelectedCandidateID,
			Reason: "DEPLOY must use the feasible candidate selected from ResourceRecommendation.",
		})
	}
	status := GuardApproved
	for _, check := range checks {
		if !check.Passed {
			status = GuardRejected
			break
		}
	}
	return GuardResult{Status: status, Checks: checks}
}

func guardSummary(guard GuardResult) string {
	reasons := make([]string, 0)
	for _, check := range guard.Checks {
		if !check.Passed {
			reasons = append(reasons, check.Name+": "+check.Reason)
		}
	}
	if len(reasons) == 0 {
		return "Go Guard approved the model proposal."
	}
	return strings.Join(reasons, "; ")
}

func (service *Service) storeReasoningComparison(
	source Flow,
	comparison ReasoningComparison,
) error {
	service.mu.Lock()
	defer service.mu.Unlock()
	current, ok := service.flows[source.CorrelationID]
	if !ok || current.Decision == nil || source.Decision == nil ||
		current.Decision.DecisionID != source.Decision.DecisionID {
		return fmt.Errorf("Agent Control flow changed while reasoning comparison was running")
	}
	value := comparison
	current.ReasoningComparison = &value
	current.UpdatedAt = service.now().Format(time.RFC3339Nano)
	service.flows[source.CorrelationID] = cloneFlow(current)
	return nil
}

func (service *Service) GetFlow(correlationID string) (Flow, bool) {
	service.mu.RLock()
	defer service.mu.RUnlock()
	flow, ok := service.flows[strings.TrimSpace(correlationID)]
	return cloneFlow(flow), ok
}

func (service *Service) ListFlows() []Flow {
	service.mu.RLock()
	defer service.mu.RUnlock()

	flows := make([]Flow, 0, len(service.flows))
	for _, flow := range service.flows {
		flows = append(flows, cloneFlow(flow))
	}
	sort.Slice(flows, func(i, j int) bool {
		return flows[i].CorrelationID < flows[j].CorrelationID
	})
	return flows
}

func (service *Service) DeleteFlow(correlationID string) (Flow, bool) {
	service.mu.Lock()
	defer service.mu.Unlock()

	key := strings.TrimSpace(correlationID)
	flow, ok := service.flows[key]
	if !ok {
		return Flow{}, false
	}
	delete(service.flows, key)
	return cloneFlow(flow), true
}

func (service *Service) ClearFlows() int {
	service.mu.Lock()
	defer service.mu.Unlock()

	count := len(service.flows)
	service.flows = map[string]Flow{}
	return count
}

func inputJoinState(flow Flow) string {
	switch {
	case flow.ApplicationContext == nil:
		return StateWaitingForContext
	case flow.ResourceRecommendation == nil:
		return StateWaitingForRecommendation
	default:
		return StateReady
	}
}

func (service *Service) evaluateFlow(ctx context.Context, flow Flow) Flow {
	if flow.State != StateReady || service.decisionRuntime == nil {
		return service.evaluateFlowLegacy(ctx, flow)
	}

	profile := flow.ApplicationContext.Data.ApplicationProfile
	recommendation := flow.ResourceRecommendation.Data.ResourceRecommendation
	createdAt := service.now().Format(time.RFC3339Nano)
	decisionID := "decision-" + flow.CorrelationID
	result, err := service.decisionRuntime.Decide(ctx, DecisionAgentRequest{
		RunID:                  flow.AutomationRunID,
		RequestedAgent:         flow.RequestedDecisionAgent,
		CorrelationID:          flow.CorrelationID,
		TraceID:                flow.TraceID,
		ApplicationProfile:     profile,
		ResourceRecommendation: recommendation,
	})
	resultCopy := cloneDecisionAgentResult(result)
	flow.AgentExecution = &resultCopy
	flow.AgentAuthorization = authorizationFromDecisionResult(result)
	if err != nil {
		return failedDecisionAgentFlow(flow, result)
	}
	return service.applyDecisionAgentResult(flow, result, createdAt, decisionID)
}

func (service *Service) evaluateFlowLegacy(ctx context.Context, flow Flow) Flow {
	if flow.State != StateReady {
		return flow
	}

	authorization := AgentAuthorization{
		AgentName:  AutomationAgentName,
		Capability: AutomationCapability,
		Action:     AutomationDecisionAction,
		Authorized: true,
		Reason:     "Agent Registry authorization is not configured for this in-process service.",
	}
	if service.authorizer != nil {
		result, err := service.authorizer.Authorize(ctx, AgentAuthorizationRequest{
			AgentName:  AutomationAgentName,
			Capability: AutomationCapability,
			Action:     AutomationDecisionAction,
		})
		if err != nil {
			result.Authorized = false
			result.Reason = "Agent Registry authorization failed: " + err.Error()
		}
		authorization = result
	}
	flow.AgentAuthorization = &authorization
	if !authorization.Authorized {
		flow.State = StateAgentAuthorizationRejected
		flow.Decision = nil
		flow.DeploymentPlan = nil
		flow.DeploymentRequest = nil
		flow.Guard = &GuardResult{
			Status: GuardRejected,
			Checks: []GuardCheck{
				{
					Name:   "agent_registry_authorization",
					Passed: false,
					Reason: authorization.Reason,
				},
			},
		}
		return flow
	}

	profile := flow.ApplicationContext.Data.ApplicationProfile
	recommendation := flow.ResourceRecommendation.Data.ResourceRecommendation
	createdAt := service.now().Format(time.RFC3339Nano)
	decisionID := "decision-" + flow.CorrelationID

	if issues := validateApplicationRequirements(profile.Requirements); len(issues) > 0 {
		flow.State = StateDecisionRejected
		flow.Decision = &AutomationDecision{
			DecisionID:    decisionID,
			Action:        ActionReject,
			Reason:        "Application requirements are invalid and must be corrected.",
			ReasoningMode: ReasoningModeRuleBased,
			Confidence:    1,
			CorrectionRequest: &CorrectionRequest{
				Target: CorrectionTargetApplicationProfile,
				Reason: "Correct the invalid Application Profile fields and submit it again.",
				Issues: issues,
			},
			CreatedAt: createdAt,
		}
		flow.DeploymentPlan = nil
		flow.Guard = &GuardResult{
			Status: GuardRejected,
			Checks: []GuardCheck{
				{
					Name:   "application_requirements",
					Passed: false,
					Reason: "Application requirements contain invalid values.",
				},
			},
			Issues: append([]ValidationIssue(nil), issues...),
		}
		flow.DeploymentRequest = nil
		return flow
	}

	candidate, issues := selectRecommendedCandidate(profile.Requirements, recommendation)
	if len(issues) > 0 {
		flow.State = StateRetryRequired
		flow.Decision = &AutomationDecision{
			DecisionID:    decisionID,
			Action:        ActionRetry,
			Reason:        "The Resource Recommendation cannot satisfy the Application Profile.",
			ReasoningMode: ReasoningModeRuleBased,
			Confidence:    1,
			CorrectionRequest: &CorrectionRequest{
				Target: CorrectionTargetResourceRecommendation,
				Reason: "Recommend another resource candidate that satisfies all minimum requirements.",
				Issues: issues,
			},
			CreatedAt: createdAt,
		}
		flow.DeploymentPlan = nil
		flow.Guard = &GuardResult{
			Status: GuardRetryRequired,
			Checks: []GuardCheck{
				{
					Name:   "resource_compatibility",
					Passed: false,
					Reason: "The selected resource does not satisfy the Application Profile.",
				},
			},
			Issues: append([]ValidationIssue(nil), issues...),
		}
		flow.DeploymentRequest = nil
		return flow
	}

	flow.State = StateDecisionApproved
	flow.Decision = &AutomationDecision{
		DecisionID:          decisionID,
		Action:              ActionDeploy,
		Reason:              "The selected resource candidate satisfies the minimum deployment requirements.",
		ReasoningMode:       ReasoningModeRuleBased,
		Confidence:          candidate.Scores.Total,
		SelectedCandidateID: candidate.CandidateID,
		CreatedAt:           createdAt,
	}
	flow.DeploymentPlan = &DeploymentPlan{
		PlanID:                 "plan-" + flow.CorrelationID,
		ProfileID:              profile.ProfileID,
		AppID:                  profile.AppID,
		AppVersion:             profile.AppVersion,
		SelectedCandidateID:    candidate.CandidateID,
		TargetRuntime:          "VM",
		DesiredInfrastructure:  candidate.DesiredInfrastructure,
		InferenceConfiguration: flow.ApplicationContext.Data.ModelRecommendation.InferenceConfiguration,
		ResourceHints:          append([]string(nil), candidate.ResourceHints...),
		ReasoningMode:          ReasoningModeRuleBased,
		CreatedAt:              createdAt,
	}
	flow.Guard = &GuardResult{
		Status: GuardApproved,
		Checks: []GuardCheck{
			{
				Name:   "application_requirements",
				Passed: true,
				Reason: "Application requirements are structurally and semantically valid.",
			},
			{
				Name:   "resource_compatibility",
				Passed: true,
				Reason: "The selected resource satisfies all minimum requirements.",
			},
			{
				Name:   "identity_consistency",
				Passed: true,
				Reason: "correlation_id, trace_id, and profile_id are consistent.",
			},
		},
	}
	spec := buildDesiredDeploymentSpec(flow)
	flow.DesiredDeploymentSpec = &spec
	request := buildDeploymentCreateRequest(flow, createdAt)
	flow.DeploymentRequest = &request
	return flow
}

func (service *Service) applyDecisionAgentResult(
	flow Flow,
	result DecisionAgentResult,
	createdAt string,
	decisionID string,
) Flow {
	profile := flow.ApplicationContext.Data.ApplicationProfile
	recommendation := flow.ResourceRecommendation.Data.ResourceRecommendation
	decision := result.Decision
	decision.DecisionID = decisionID
	decision.CreatedAt = createdAt
	if strings.TrimSpace(decision.ReasoningMode) == "" {
		decision.ReasoningMode = "selected_agent"
	}
	guard := validateDecisionAgentResult(profile, recommendation, result)
	flow.Guard = &guard
	flow.Decision = &decision
	flow.DeploymentPlan = nil
	flow.DesiredDeploymentSpec = nil
	flow.DeploymentRequest = nil

	switch guard.Status {
	case GuardApproved:
		candidate, _ := findDecisionCandidate(recommendation, decision.SelectedCandidateID)
		flow.State = StateDecisionApproved
		flow.DeploymentPlan = &DeploymentPlan{
			PlanID:                 "plan-" + flow.CorrelationID,
			ProfileID:              profile.ProfileID,
			AppID:                  profile.AppID,
			AppVersion:             profile.AppVersion,
			SelectedCandidateID:    candidate.CandidateID,
			TargetRuntime:          "VM",
			DesiredInfrastructure:  candidate.DesiredInfrastructure,
			InferenceConfiguration: flow.ApplicationContext.Data.ModelRecommendation.InferenceConfiguration,
			ResourceHints:          append([]string(nil), candidate.ResourceHints...),
			ReasoningMode:          decision.ReasoningMode,
			CreatedAt:              createdAt,
		}
		spec := buildDesiredDeploymentSpec(flow)
		flow.DesiredDeploymentSpec = &spec
		request := buildDeploymentCreateRequest(flow, createdAt)
		flow.DeploymentRequest = &request
	case GuardRetryRequired:
		flow.State = StateRetryRequired
		flow.Decision.Action = ActionRetry
		if flow.Decision.CorrectionRequest == nil {
			flow.Decision.CorrectionRequest = &CorrectionRequest{
				Target: CorrectionTargetResourceRecommendation,
				Reason: "Recommend another resource candidate that satisfies the Application Profile.",
				Issues: append([]ValidationIssue(nil), guard.Issues...),
			}
		}
	case GuardRejected:
		if decision.Action == ActionReject && decisionResultGuardsApproved(result) {
			flow.State = StateDecisionRejected
		} else {
			flow.State = StateAgentResultRejected
		}
	}
	return flow
}

func authorizationFromDecisionResult(result DecisionAgentResult) *AgentAuthorization {
	return &AgentAuthorization{
		AgentName:  result.AgentName,
		Source:     result.Source,
		Capability: AutomationCapability,
		Action:     AutomationDecisionAction,
		Authorized: result.RequestGuard.Status == GuardApproved,
		Reason:     decisionGuardReason(result.RequestGuard),
	}
}

func failedDecisionAgentFlow(flow Flow, result DecisionAgentResult) Flow {
	flow.Decision = nil
	flow.DeploymentPlan = nil
	flow.DesiredDeploymentSpec = nil
	flow.DeploymentRequest = nil
	switch {
	case result.RequestGuard.Status == GuardRejected:
		flow.State = StateAgentAuthorizationRejected
		guard := result.RequestGuard
		flow.Guard = &guard
	case result.ResultGuard.Status == GuardRejected:
		flow.State = StateAgentResultRejected
		guard := result.ResultGuard
		flow.Guard = &guard
	default:
		flow.State = StateAgentExecutionFailed
		guard := rejectedDecisionGuard("agent_execution", "Decision Agent execution failed.")
		flow.Guard = &guard
	}
	return flow
}

func decisionResultGuardsApproved(result DecisionAgentResult) bool {
	return result.RequestGuard.Status == GuardApproved && result.ResultGuard.Status == GuardApproved
}

func decisionGuardReason(guard GuardResult) string {
	if len(guard.Checks) > 0 {
		return guard.Checks[0].Reason
	}
	return guard.Status
}

func validateApplicationRequirements(requirements ApplicationRequirements) []ValidationIssue {
	issues := make([]ValidationIssue, 0)
	addPositiveIssue := func(field string, actual int) {
		if actual <= 0 {
			issues = append(issues, ValidationIssue{
				Field:    field,
				Code:     "VALUE_MUST_BE_POSITIVE",
				Reason:   "The value must be greater than zero.",
				Expected: "> 0",
				Actual:   actual,
			})
		}
	}
	addPositiveIssue("requirements.compute.cpu_cores_min", requirements.Compute.CPUCoresMin)
	addPositiveIssue("requirements.compute.memory_mib_min", requirements.Compute.MemoryMiBMin)
	if requirements.Compute.StorageGiBMin < 0 {
		issues = append(issues, ValidationIssue{
			Field:    "requirements.compute.storage_gib_min",
			Code:     "VALUE_MUST_NOT_BE_NEGATIVE",
			Reason:   "Storage must be zero or greater.",
			Expected: ">= 0",
			Actual:   requirements.Compute.StorageGiBMin,
		})
	}
	addPositiveIssue("requirements.deployment.replicas_min", requirements.Deployment.ReplicasMin)
	if requirements.Deployment.ReplicasMax < requirements.Deployment.ReplicasMin {
		issues = append(issues, ValidationIssue{
			Field:    "requirements.deployment.replicas_max",
			Code:     "INVALID_REPLICA_RANGE",
			Reason:   "replicas_max must be greater than or equal to replicas_min.",
			Expected: requirements.Deployment.ReplicasMin,
			Actual:   requirements.Deployment.ReplicasMax,
		})
	}
	if requirements.Accelerator.Required {
		if strings.TrimSpace(requirements.Accelerator.Type) == "" {
			issues = append(issues, ValidationIssue{
				Field:  "requirements.accelerator.type",
				Code:   "REQUIRED_FIELD_MISSING",
				Reason: "An accelerator type is required.",
			})
		}
		addPositiveIssue("requirements.accelerator.count_min", requirements.Accelerator.CountMin)
	}
	return issues
}

func selectRecommendedCandidate(
	requirements ApplicationRequirements,
	recommendation ResourceRecommendation,
) (ResourceCandidate, []ValidationIssue) {
	var selected ResourceCandidate
	found := false
	for _, candidate := range recommendation.Candidates {
		if candidate.CandidateID == recommendation.SelectedCandidateID {
			selected = candidate
			found = true
			break
		}
	}
	if !found {
		return ResourceCandidate{}, []ValidationIssue{{
			Field:    "resource_recommendation.selected_candidate_id",
			Code:     "SELECTED_CANDIDATE_NOT_FOUND",
			Reason:   "The selected candidate does not exist in candidates.",
			Expected: recommendation.SelectedCandidateID,
		}}
	}

	issues := make([]ValidationIssue, 0)
	addMinimumIssue := func(field string, actual int, expected int) {
		if actual < expected {
			issues = append(issues, ValidationIssue{
				Field:    field,
				Code:     "RESOURCE_BELOW_MINIMUM",
				Reason:   "The selected resource is below the application minimum.",
				Expected: expected,
				Actual:   actual,
			})
		}
	}
	if !selected.Feasible {
		issues = append(issues, ValidationIssue{
			Field:    "resource_recommendation.candidates.feasible",
			Code:     "CANDIDATE_NOT_FEASIBLE",
			Reason:   "The selected resource candidate is not feasible.",
			Expected: true,
			Actual:   false,
		})
	}
	actual := selected.DesiredInfrastructure
	addMinimumIssue("desired_infrastructure.node_count", actual.NodeCount, requirements.Deployment.ReplicasMin)
	addMinimumIssue("desired_infrastructure.cpu_cores_per_node", actual.CPUCoresPerNode, requirements.Compute.CPUCoresMin)
	addMinimumIssue("desired_infrastructure.memory_mib_per_node", actual.MemoryMiBPerNode, requirements.Compute.MemoryMiBMin)
	addMinimumIssue("desired_infrastructure.storage_gib_per_node", actual.StorageGiBPerNode, requirements.Compute.StorageGiBMin)
	if requirements.Accelerator.Required {
		if !strings.EqualFold(actual.Accelerator.Type, requirements.Accelerator.Type) {
			issues = append(issues, ValidationIssue{
				Field:    "desired_infrastructure.accelerator.type",
				Code:     "ACCELERATOR_TYPE_MISMATCH",
				Reason:   "The selected accelerator type does not match the application requirement.",
				Expected: requirements.Accelerator.Type,
				Actual:   actual.Accelerator.Type,
			})
		}
		addMinimumIssue(
			"desired_infrastructure.accelerator.count",
			actual.Accelerator.Count,
			requirements.Accelerator.CountMin,
		)
		addMinimumIssue(
			"desired_infrastructure.accelerator.memory_mib_min_per_device",
			actual.Accelerator.MemoryMiBMinPerDevice,
			requirements.Accelerator.MemoryMiBMinPerDevice,
		)
	}
	return selected, issues
}

func buildDesiredDeploymentSpec(flow Flow) DesiredDeploymentSpec {
	profile := flow.ApplicationContext.Data.ApplicationProfile
	plan := flow.DeploymentPlan
	decision := flow.Decision

	runtime := RuntimeConfiguration{RestartPolicy: "ON_FAILURE"}
	if profile.Artifact != nil {
		runtime.Command = append([]string(nil), profile.Artifact.Entrypoint...)
	}

	return DesiredDeploymentSpec{
		SpecVersion:            ContractVersionV1,
		DecisionID:             decision.DecisionID,
		Application:            ManifestApplication{AppID: profile.AppID, AppVersion: profile.AppVersion},
		TargetRuntime:          plan.TargetRuntime,
		DesiredInfrastructure:  plan.DesiredInfrastructure,
		InferenceConfiguration: plan.InferenceConfiguration,
		Runtime:                runtime,
		PolicyHints:            append([]string(nil), plan.ResourceHints...),
		Metadata:               ManifestMetadata{ProfileID: profile.ProfileID},
	}
}

func buildDeploymentCreateRequest(flow Flow, createdAt string) DeploymentCreateRequestEnvelope {
	profile := flow.ApplicationContext.Data.ApplicationProfile
	decision := flow.Decision
	spec := flow.DesiredDeploymentSpec
	requestID := "deploy-request-" + flow.CorrelationID

	var artifact *Artifact
	if profile.Artifact != nil {
		artifactCopy := *profile.Artifact
		artifactCopy.Entrypoint = append([]string(nil), profile.Artifact.Entrypoint...)
		artifact = &artifactCopy
	}

	return DeploymentCreateRequestEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-" + requestID,
			MessageType:     MessageDeploymentCreateRequest,
			OccurredAt:      createdAt,
			CorrelationID:   flow.CorrelationID,
			TraceID:         flow.TraceID,
			CausationID:     flow.ResourceRecommendation.MessageID,
			Source: Endpoint{
				System:    "khu-agent-control",
				Component: "automation-agent",
			},
			Target: Endpoint{
				System:    "common-interface",
				Component: "deployment-orchestrator",
			},
		},
		Data: DeploymentCreateRequestData{
			DeploymentRequest: DeploymentRequest{
				RequestID:  requestID,
				DecisionID: decision.DecisionID,
				Application: DeploymentApplication{
					AppID:      profile.AppID,
					AppVersion: profile.AppVersion,
					Artifact:   artifact,
				},
				DeploymentManifest: DeploymentManifest{
					ManifestID:             "manifest-" + flow.CorrelationID,
					ManifestVersion:        spec.SpecVersion,
					DecisionID:             spec.DecisionID,
					Application:            spec.Application,
					TargetRuntime:          spec.TargetRuntime,
					DesiredInfrastructure:  spec.DesiredInfrastructure,
					InferenceConfiguration: spec.InferenceConfiguration,
					Runtime:                spec.Runtime,
					ResourceHints:          append([]string(nil), spec.PolicyHints...),
					Metadata:               spec.Metadata,
				},
			},
		},
	}
}

func validateEnvelope(envelope Envelope, expectedType string) error {
	switch {
	case envelope.ContractVersion != ContractVersionV1:
		return fmt.Errorf("unsupported contract_version: %s", envelope.ContractVersion)
	case strings.TrimSpace(envelope.MessageID) == "":
		return fmt.Errorf("message_id is required")
	case envelope.MessageType != expectedType:
		return fmt.Errorf("message_type must be %s", expectedType)
	case strings.TrimSpace(envelope.CorrelationID) == "":
		return fmt.Errorf("correlation_id is required")
	case strings.TrimSpace(envelope.TraceID) == "":
		return fmt.Errorf("trace_id is required")
	case strings.TrimSpace(envelope.Source.System) == "" || strings.TrimSpace(envelope.Source.Component) == "":
		return fmt.Errorf("source system and component are required")
	case strings.TrimSpace(envelope.Target.System) == "" || strings.TrimSpace(envelope.Target.Component) == "":
		return fmt.Errorf("target system and component are required")
	}
	if _, err := time.Parse(time.RFC3339, envelope.OccurredAt); err != nil {
		return fmt.Errorf("occurred_at must be RFC3339: %w", err)
	}
	return nil
}

func validateApplicationContext(data ApplicationContextData) error {
	profile := data.ApplicationProfile
	switch {
	case strings.TrimSpace(profile.ProfileID) == "":
		return fmt.Errorf("application_profile.profile_id is required")
	case strings.TrimSpace(profile.AppID) == "":
		return fmt.Errorf("application_profile.app_id is required")
	case strings.TrimSpace(profile.AppVersion) == "":
		return fmt.Errorf("application_profile.app_version is required")
	}
	return nil
}

func validateResourceRecommendation(recommendation ResourceRecommendation) error {
	switch {
	case strings.TrimSpace(recommendation.RecommendationID) == "":
		return fmt.Errorf("resource_recommendation.recommendation_id is required")
	case strings.TrimSpace(recommendation.ProfileID) == "":
		return fmt.Errorf("resource_recommendation.profile_id is required")
	case strings.TrimSpace(recommendation.Status) == "":
		return fmt.Errorf("resource_recommendation.status is required")
	case len(recommendation.Candidates) == 0:
		return fmt.Errorf("resource_recommendation.candidates is required")
	}
	for _, candidate := range recommendation.Candidates {
		if candidate.Scores.Total < 0 || candidate.Scores.Total > 1 {
			return fmt.Errorf("candidate score must be between 0 and 1")
		}
	}
	return nil
}

func validateDeploymentStatus(status DeploymentStatus) error {
	switch {
	case strings.TrimSpace(status.DeploymentID) == "":
		return fmt.Errorf("deployment_status.deployment_id is required")
	case strings.TrimSpace(status.DecisionID) == "":
		return fmt.Errorf("deployment_status.decision_id is required")
	case !validDeploymentState(status.State):
		return fmt.Errorf("deployment_status.state is not supported: %s", status.State)
	}
	if _, err := time.Parse(time.RFC3339, status.UpdatedAt); err != nil {
		return fmt.Errorf("deployment_status.updated_at must be RFC3339: %w", err)
	}
	return nil
}

func validDeploymentState(state string) bool {
	switch state {
	case DeploymentStateQueued,
		DeploymentStateProvisioning,
		DeploymentStateDeploying,
		DeploymentStateRunning,
		DeploymentStateFailed,
		DeploymentStateStopping,
		DeploymentStateStopped:
		return true
	default:
		return false
	}
}

func validateOptimizationFeedback(feedback OptimizationFeedback) error {
	switch {
	case strings.TrimSpace(feedback.FeedbackID) == "":
		return fmt.Errorf("optimization_feedback.feedback_id is required")
	case strings.TrimSpace(feedback.DecisionID) == "":
		return fmt.Errorf("optimization_feedback.decision_id is required")
	case strings.TrimSpace(feedback.DeploymentID) == "":
		return fmt.Errorf("optimization_feedback.deployment_id is required")
	case strings.TrimSpace(feedback.Outcome) == "":
		return fmt.Errorf("optimization_feedback.outcome is required")
	case feedback.Metrics.Resource.CPUAveragePercent < 0 ||
		feedback.Metrics.Resource.CPUAveragePercent > 100:
		return fmt.Errorf("cpu_average_percent must be between 0 and 100")
	case feedback.Metrics.Resource.AcceleratorAveragePercent < 0 ||
		feedback.Metrics.Resource.AcceleratorAveragePercent > 100:
		return fmt.Errorf("accelerator_average_percent must be between 0 and 100")
	case feedback.Metrics.Inference.ErrorRatePercent < 0 ||
		feedback.Metrics.Inference.ErrorRatePercent > 100:
		return fmt.Errorf("error_rate_percent must be between 0 and 100")
	case feedback.Metrics.Resource.MemoryPeakMiB < 0 ||
		feedback.Metrics.Resource.AcceleratorMemoryPeakMiB < 0 ||
		feedback.Metrics.Inference.LatencyP95MS < 0 ||
		feedback.Metrics.Inference.ThroughputRPS < 0 ||
		feedback.Metrics.Cost.EstimatedCost < 0:
		return fmt.Errorf("feedback metrics must not be negative")
	}
	startedAt, err := time.Parse(time.RFC3339, feedback.ObservationWindow.StartedAt)
	if err != nil {
		return fmt.Errorf("observation_window.started_at must be RFC3339: %w", err)
	}
	endedAt, err := time.Parse(time.RFC3339, feedback.ObservationWindow.EndedAt)
	if err != nil {
		return fmt.Errorf("observation_window.ended_at must be RFC3339: %w", err)
	}
	if endedAt.Before(startedAt) {
		return fmt.Errorf("observation_window.ended_at must not be before started_at")
	}
	if _, err := time.Parse(time.RFC3339, feedback.CreatedAt); err != nil {
		return fmt.Errorf("optimization_feedback.created_at must be RFC3339: %w", err)
	}
	return nil
}

func ensureFeedbackIdentity(
	flow Flow,
	envelope Envelope,
	decisionID string,
	deploymentID string,
) error {
	if flow.TraceID != envelope.TraceID {
		return fmt.Errorf("trace_id does not match the existing correlation flow")
	}
	if flow.Decision == nil || flow.Decision.DecisionID != decisionID {
		return fmt.Errorf("decision_id does not match the existing Agent Control decision")
	}
	if flow.DeploymentStatus != nil {
		currentDeploymentID := flow.DeploymentStatus.Data.DeploymentStatus.DeploymentID
		if currentDeploymentID != deploymentID {
			return fmt.Errorf("deployment_id does not match the existing deployment status")
		}
	}
	return nil
}

func summarizeFeedback(flow Flow) *FeedbackSummary {
	if flow.DeploymentStatus == nil {
		return nil
	}
	status := flow.DeploymentStatus.Data.DeploymentStatus
	summary := &FeedbackSummary{
		DeploymentID:    status.DeploymentID,
		DecisionID:      status.DecisionID,
		DeploymentState: status.State,
		Success:         status.State == DeploymentStateRunning,
		Cause:           strings.TrimSpace(status.Message),
		UpdatedAt:       status.UpdatedAt,
	}
	if summary.Cause == "" {
		summary.Cause = "Deployment state changed to " + status.State + "."
	}
	if flow.OptimizationFeedback == nil {
		return summary
	}

	feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
	metrics := feedback.Metrics
	summary.Outcome = feedback.Outcome
	summary.Success = feedback.Outcome == FeedbackOutcomeSucceeded
	summary.SLOViolations = append([]string(nil), feedback.SLOViolations...)
	summary.Metrics = &metrics
	summary.UpdatedAt = feedback.CreatedAt
	switch {
	case summary.Success && len(feedback.SLOViolations) == 0:
		summary.Cause = "Deployment succeeded and the reported metrics satisfy the SLO."
	case summary.Success:
		summary.Cause = "Deployment succeeded, but SLO violations were reported: " +
			strings.Join(feedback.SLOViolations, "; ")
	case strings.TrimSpace(status.Message) != "":
		summary.Cause = "Deployment failed: " + status.Message
	default:
		summary.Cause = "Deployment feedback reported outcome " + feedback.Outcome + "."
	}
	return summary
}

func ensureFlowIdentity(flow Flow, envelope Envelope, profileID string) error {
	if flow.CorrelationID == "" {
		return nil
	}
	if flow.TraceID != envelope.TraceID {
		return fmt.Errorf("trace_id does not match the existing correlation flow")
	}
	if flow.ProfileID != "" && flow.ProfileID != profileID {
		return fmt.Errorf("profile_id does not match the existing correlation flow")
	}
	return nil
}

func cloneFlow(flow Flow) Flow {
	result := flow
	if flow.ApplicationContext != nil {
		value := cloneApplicationContextEnvelope(*flow.ApplicationContext)
		result.ApplicationContext = &value
	}
	if flow.ResourceRecommendation != nil {
		value := cloneResourceRecommendationEnvelope(*flow.ResourceRecommendation)
		result.ResourceRecommendation = &value
	}
	if flow.AgentAuthorization != nil {
		value := *flow.AgentAuthorization
		result.AgentAuthorization = &value
	}
	if flow.AgentExecution != nil {
		value := cloneDecisionAgentResult(*flow.AgentExecution)
		result.AgentExecution = &value
	}
	if flow.Decision != nil {
		value := *flow.Decision
		if flow.Decision.CorrectionRequest != nil {
			correction := *flow.Decision.CorrectionRequest
			correction.Issues = append([]ValidationIssue(nil), flow.Decision.CorrectionRequest.Issues...)
			value.CorrectionRequest = &correction
		}
		result.Decision = &value
	}
	if flow.DeploymentPlan != nil {
		value := *flow.DeploymentPlan
		value.ResourceHints = append([]string(nil), flow.DeploymentPlan.ResourceHints...)
		result.DeploymentPlan = &value
	}
	if flow.Guard != nil {
		value := *flow.Guard
		value.Checks = append([]GuardCheck(nil), flow.Guard.Checks...)
		value.Issues = append([]ValidationIssue(nil), flow.Guard.Issues...)
		result.Guard = &value
	}
	if flow.DeploymentRequest != nil {
		value := cloneDeploymentCreateRequestEnvelope(*flow.DeploymentRequest)
		result.DeploymentRequest = &value
	}
	if flow.DeploymentStatus != nil {
		value := cloneDeploymentStatusEnvelope(*flow.DeploymentStatus)
		result.DeploymentStatus = &value
	}
	if flow.OptimizationFeedback != nil {
		value := cloneOptimizationFeedbackEnvelope(*flow.OptimizationFeedback)
		result.OptimizationFeedback = &value
	}
	if flow.FeedbackSummary != nil {
		value := *flow.FeedbackSummary
		value.SLOViolations = append([]string(nil), flow.FeedbackSummary.SLOViolations...)
		if flow.FeedbackSummary.Metrics != nil {
			metrics := *flow.FeedbackSummary.Metrics
			value.Metrics = &metrics
		}
		result.FeedbackSummary = &value
	}
	if flow.ScalingDecision != nil {
		value := *flow.ScalingDecision
		value.Evidence = append([]string(nil), flow.ScalingDecision.Evidence...)
		result.ScalingDecision = &value
	}
	if flow.ReasoningComparison != nil {
		value := *flow.ReasoningComparison
		result.ReasoningComparison = &value
	}
	return result
}

func cloneDecisionAgentResult(result DecisionAgentResult) DecisionAgentResult {
	result.RequestGuard.Checks = append([]GuardCheck(nil), result.RequestGuard.Checks...)
	result.RequestGuard.Issues = append([]ValidationIssue(nil), result.RequestGuard.Issues...)
	result.ResultGuard.Checks = append([]GuardCheck(nil), result.ResultGuard.Checks...)
	result.ResultGuard.Issues = append([]ValidationIssue(nil), result.ResultGuard.Issues...)
	if result.Decision.CorrectionRequest != nil {
		correction := *result.Decision.CorrectionRequest
		correction.Issues = append([]ValidationIssue(nil), correction.Issues...)
		result.Decision.CorrectionRequest = &correction
	}
	return result
}

func cloneApplicationContextEnvelope(message ApplicationContextEnvelope) ApplicationContextEnvelope {
	result := message
	result.Data.ApplicationProfile.Analysis.Assumptions = append(
		[]string(nil),
		message.Data.ApplicationProfile.Analysis.Assumptions...,
	)
	result.Data.ApplicationProfile.Analysis.MissingFields = append(
		[]string(nil),
		message.Data.ApplicationProfile.Analysis.MissingFields...,
	)
	result.Data.ApplicationProfile.Analysis.Warnings = append(
		[]string(nil),
		message.Data.ApplicationProfile.Analysis.Warnings...,
	)
	if message.Data.ApplicationProfile.Artifact != nil {
		artifact := *message.Data.ApplicationProfile.Artifact
		artifact.Entrypoint = append([]string(nil), message.Data.ApplicationProfile.Artifact.Entrypoint...)
		result.Data.ApplicationProfile.Artifact = &artifact
	}
	return result
}

func cloneResourceRecommendationEnvelope(
	message ResourceRecommendationEnvelope,
) ResourceRecommendationEnvelope {
	result := message
	result.Data.ResourceRecommendation.Candidates = make(
		[]ResourceCandidate,
		len(message.Data.ResourceRecommendation.Candidates),
	)
	for index, candidate := range message.Data.ResourceRecommendation.Candidates {
		candidate.ResourceHints = append([]string(nil), candidate.ResourceHints...)
		candidate.RejectionReasons = append([]string(nil), candidate.RejectionReasons...)
		result.Data.ResourceRecommendation.Candidates[index] = candidate
	}
	return result
}

func cloneDeploymentCreateRequestEnvelope(
	message DeploymentCreateRequestEnvelope,
) DeploymentCreateRequestEnvelope {
	result := message
	request := &result.Data.DeploymentRequest
	if message.Data.DeploymentRequest.Application.Artifact != nil {
		artifact := *message.Data.DeploymentRequest.Application.Artifact
		artifact.Entrypoint = append(
			[]string(nil),
			message.Data.DeploymentRequest.Application.Artifact.Entrypoint...,
		)
		request.Application.Artifact = &artifact
	}
	manifest := &request.DeploymentManifest
	sourceManifest := message.Data.DeploymentRequest.DeploymentManifest
	manifest.ResourceHints = append([]string(nil), sourceManifest.ResourceHints...)
	manifest.Runtime.Command = append([]string(nil), sourceManifest.Runtime.Command...)
	manifest.Runtime.Args = append([]string(nil), sourceManifest.Runtime.Args...)
	manifest.Runtime.Ports = append([]RuntimePort(nil), sourceManifest.Runtime.Ports...)
	if sourceManifest.Runtime.Environment != nil {
		manifest.Runtime.Environment = make(map[string]string, len(sourceManifest.Runtime.Environment))
		for key, value := range sourceManifest.Runtime.Environment {
			manifest.Runtime.Environment[key] = value
		}
	}
	return result
}

func cloneDeploymentStatusEnvelope(message DeploymentStatusEnvelope) DeploymentStatusEnvelope {
	result := message
	result.Data.DeploymentStatus.ActualInfrastructure.ResourceIDs = append(
		[]string(nil),
		message.Data.DeploymentStatus.ActualInfrastructure.ResourceIDs...,
	)
	return result
}

func cloneOptimizationFeedbackEnvelope(
	message OptimizationFeedbackEnvelope,
) OptimizationFeedbackEnvelope {
	result := message
	result.Data.OptimizationFeedback.SLOViolations = append(
		[]string(nil),
		message.Data.OptimizationFeedback.SLOViolations...,
	)
	return result
}
