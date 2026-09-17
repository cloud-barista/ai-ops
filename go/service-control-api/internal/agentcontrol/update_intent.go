package agentcontrol

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

// ApplyDeploymentContext keeps an UPDATE request platform-neutral. A logical
// deployment reference and a changed desired-spec digest are sufficient; no
// VM ID or provider-specific operation is required.
func (service *Service) ApplyDeploymentContext(
	ctx context.Context,
	correlationID string,
	deploymentContext DeploymentContext,
) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	correlationID = strings.TrimSpace(correlationID)
	if correlationID == "" {
		return Flow{}, fmt.Errorf("correlation_id is required")
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	flow, ok := service.flows[correlationID]
	if !ok {
		return Flow{}, fmt.Errorf("correlation_id does not identify an Agent Control flow")
	}
	contextCopy := deploymentContext
	flow.DeploymentContext = &contextCopy
	if flow.Decision == nil || flow.DeploymentPlan == nil || flow.DesiredDeploymentSpec == nil || flow.DeploymentRequest == nil {
		service.flows[correlationID] = cloneFlow(flow)
		return cloneFlow(flow), nil
	}
	requestedUpdate := deploymentContext.SpecDrift
	if current := strings.TrimSpace(deploymentContext.CurrentSpecDigest); current != "" {
		requestedUpdate = requestedUpdate || current != desiredSpecDigest(*flow.DesiredDeploymentSpec)
	}
	if strings.TrimSpace(deploymentContext.DeploymentRef) == "" || !requestedUpdate {
		flow.UpdatedAt = service.now().Format("2006-01-02T15:04:05.999999999Z07:00")
		service.flows[correlationID] = cloneFlow(flow)
		return cloneFlow(flow), nil
	}
	flow.State = StateUpdateApproved
	flow.Decision.Action = ActionUpdate
	flow.Decision.Reason = "The logical deployment context differs from the desired platform-neutral Deployment Spec."
	flow.DeploymentPlan.Operation = ActionUpdate
	flow.DesiredDeploymentSpec.Operation = ActionUpdate
	flow.DeploymentRequest.Data.DeploymentRequest.Operation = ActionUpdate
	flow.DeploymentRequest.Data.DeploymentRequest.DeploymentManifest.Operation = ActionUpdate
	if count := len(flow.ManifestRevisions); count > 0 {
		flow.ManifestRevisions[count-1].TriggerAction = ActionUpdate
		flow.ManifestRevisions[count-1].DesiredDeploymentSpec.Operation = ActionUpdate
		flow.ManifestRevisions[count-1].DeploymentRequest.Data.DeploymentRequest.Operation = ActionUpdate
		flow.ManifestRevisions[count-1].DeploymentRequest.Data.DeploymentRequest.DeploymentManifest.Operation = ActionUpdate
	}
	flow.UpdatedAt = service.now().Format("2006-01-02T15:04:05.999999999Z07:00")
	service.flows[correlationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
}

func desiredSpecDigest(spec DesiredDeploymentSpec) string {
	payload, err := json.Marshal(spec)
	if err != nil {
		return ""
	}
	digest := sha256.Sum256(payload)
	return "sha256:" + hex.EncodeToString(digest[:])
}
