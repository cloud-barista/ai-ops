package agentcontrol

import (
	"context"
	"fmt"
	"reflect"
	"strings"
)

// ReceiveExternalInputs validates the pair before publishing either input.
// Replays cannot replace a completed Flow with a different upstream payload.
func (service *Service) ReceiveExternalInputs(ctx context.Context, application ApplicationContextEnvelope, recommendation ResourceRecommendationEnvelope, agent string) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	if err := validateEnvelope(application.Envelope, MessageApplicationContextCreated); err != nil {
		return Flow{}, err
	}
	if err := validateEnvelope(recommendation.Envelope, MessageResourceRecommendationCreated); err != nil {
		return Flow{}, err
	}
	if err := validateApplicationContext(application.Data); err != nil {
		return Flow{}, err
	}
	if err := validateResourceRecommendation(recommendation.Data.ResourceRecommendation); err != nil {
		return Flow{}, err
	}
	if application.CorrelationID != recommendation.CorrelationID || application.TraceID != recommendation.TraceID || application.Data.ApplicationProfile.ProfileID != recommendation.Data.ResourceRecommendation.ProfileID {
		return Flow{}, fmt.Errorf("external input identities must match")
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	if existing, ok := service.flows[application.CorrelationID]; ok {
		if reflect.DeepEqual(existing.ApplicationContext, &application) && reflect.DeepEqual(existing.ResourceRecommendation, &recommendation) && existing.RequestedDecisionAgent == strings.TrimSpace(agent) {
			return cloneFlow(existing), nil
		}
		return Flow{}, fmt.Errorf("correlation_id already belongs to another input; use a new Flow")
	}
	appCopy := cloneApplicationContextEnvelope(application)
	recCopy := cloneResourceRecommendationEnvelope(recommendation)
	flow := Flow{CorrelationID: application.CorrelationID, TraceID: application.TraceID, ProfileID: application.Data.ApplicationProfile.ProfileID, ApplicationContext: &appCopy, ResourceRecommendation: &recCopy, RequestedDecisionAgent: strings.TrimSpace(agent), State: StateReady, UpdatedAt: service.now().Format("2006-01-02T15:04:05.999999999Z07:00")}
	flow = service.evaluateFlow(ctx, flow)
	service.flows[flow.CorrelationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
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
