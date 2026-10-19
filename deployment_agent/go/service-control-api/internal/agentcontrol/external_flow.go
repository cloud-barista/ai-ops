package agentcontrol

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"time"
)

// ReceiveExternalInputs validates the pair before publishing either input.
// Replays cannot replace a completed Flow with a different upstream payload.
func (service *Service) ReceiveExternalInputs(ctx context.Context, application ApplicationContextEnvelope, recommendation ResourceRecommendationEnvelope, agent string) (Flow, error) {
	return service.ReceiveExternalInputsFrom(ctx, application, recommendation, agent, "external-api")
}

// ReceiveExternalInputsFrom records the integration origin with the immutable
// input pair so the UI can distinguish external handoffs from local runs.
func (service *Service) ReceiveExternalInputsFrom(ctx context.Context, application ApplicationContextEnvelope, recommendation ResourceRecommendationEnvelope, agent, inputOrigin string) (Flow, error) {
	flow, _, err := service.receiveExternalInputsFrom(ctx, application, recommendation, agent, inputOrigin, "", nil)
	return flow, err
}

// ReceiveFocusedInputs treats the decoded focused request as one immutable
// input. The digest includes raw Resource Ops observations, which the adapted
// Common JSON recommendation does not retain. A replay may regenerate the
// synthetic occurred_at values but may not change any request field.
func (service *Service) ReceiveFocusedInputs(ctx context.Context, application ApplicationContextEnvelope, recommendation ResourceRecommendationEnvelope, agent, inputDigest string, deploymentContext *DeploymentContext) (Flow, bool, error) {
	if strings.TrimSpace(inputDigest) == "" {
		return Flow{}, false, fmt.Errorf("focused input digest is required")
	}
	return service.receiveExternalInputsFrom(ctx, application, recommendation, agent, "focused-planning", inputDigest, deploymentContext)
}

func (service *Service) receiveExternalInputsFrom(ctx context.Context, application ApplicationContextEnvelope, recommendation ResourceRecommendationEnvelope, agent, inputOrigin, inputDigest string, deploymentContext *DeploymentContext) (Flow, bool, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, false, err
	}
	if err := validateEnvelope(application.Envelope, MessageApplicationContextCreated); err != nil {
		return Flow{}, false, err
	}
	if err := validateEnvelope(recommendation.Envelope, MessageResourceRecommendationCreated); err != nil {
		return Flow{}, false, err
	}
	if err := validateApplicationContext(application.Data); err != nil {
		return Flow{}, false, err
	}
	if err := validateResourceRecommendation(recommendation.Data.ResourceRecommendation); err != nil {
		return Flow{}, false, err
	}
	if application.CorrelationID != recommendation.CorrelationID || application.TraceID != recommendation.TraceID || application.Data.ApplicationProfile.ProfileID != recommendation.Data.ResourceRecommendation.ProfileID {
		return Flow{}, false, fmt.Errorf("external input identities must match")
	}
	origin := strings.TrimSpace(inputOrigin)
	if origin == "" {
		origin = "external-api"
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	if existing, ok := service.flows[application.CorrelationID]; ok {
		if inputDigest != "" {
			if existing.InputOrigin == "focused-planning" && existing.focusedInputDigest == inputDigest {
				return cloneFlow(existing), true, nil
			}
		} else if reflect.DeepEqual(existing.ApplicationContext, &application) && reflect.DeepEqual(existing.ResourceRecommendation, &recommendation) && existing.RequestedDecisionAgent == strings.TrimSpace(agent) && existing.InputOrigin == origin {
			return cloneFlow(existing), true, nil
		}
		return Flow{}, false, fmt.Errorf("correlation_id already belongs to another input; use a new Flow")
	}
	appCopy := cloneApplicationContextEnvelope(application)
	recCopy := cloneResourceRecommendationEnvelope(recommendation)
	acceptedAt := service.now().UTC().Format(time.RFC3339Nano)
	flow := Flow{CorrelationID: application.CorrelationID, TraceID: application.TraceID, InputOrigin: origin, ProfileID: application.Data.ApplicationProfile.ProfileID, ApplicationContext: &appCopy, ResourceRecommendation: &recCopy, RequestedDecisionAgent: strings.TrimSpace(agent), State: StateReady, UpdatedAt: acceptedAt, focusedInputDigest: inputDigest}
	flow.applicationContextAcceptedAt = acceptedAt
	flow.resourceRecommendationAcceptedAt = acceptedAt
	flow = service.evaluateFlow(ctx, flow)
	if deploymentContext != nil {
		flow = applyDeploymentContextToFlow(flow, *deploymentContext, service.now().UTC().Format(time.RFC3339Nano))
	}
	service.flows[flow.CorrelationID] = cloneFlow(flow)
	return cloneFlow(flow), false, nil
}

// WithApprovedInitialFlow keeps the approval stable while the bounded submission
// is recorded and sent; input replacement and deletion use the same lock.
func (service *Service) WithApprovedInitialFlow(id string, use func(Flow) error) error {
	service.mu.Lock()
	defer service.mu.Unlock()
	flow, ok := service.flows[id]
	if !ok || flow.State != StateDecisionApproved || flow.Decision == nil || flow.Decision.Action != ActionDeploy || flow.Guard == nil || flow.Guard.Status != GuardApproved || flow.AgentAuthorization == nil || !flow.AgentAuthorization.Authorized || flow.AgentExecution == nil || flow.AgentExecution.Status != "completed" || flow.AgentExecution.RequestGuard.Status != GuardApproved || flow.AgentExecution.ResultGuard.Status != GuardApproved {
		return fmt.Errorf("an approved Agent decision and all Guards are required")
	}
	if len(flow.ManifestRevisions) != 1 || flow.ManifestRevisions[0].Revision != 1 || flow.ManifestRevisions[0].Phase != ManifestPhaseInitial || flow.ManifestRevisions[0].TriggerAction != ActionDeploy || flow.DeploymentStatus != nil || flow.OptimizationFeedback != nil {
		return fmt.Errorf("only an undeployed initial Revision 1 can be submitted")
	}
	return use(cloneFlow(flow))
}
