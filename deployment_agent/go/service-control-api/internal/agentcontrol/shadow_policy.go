package agentcontrol

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// RecordShadowPolicyAssessment appends model evidence without changing a
// deterministic decision, deployment specification, or adapter request.
func (service *Service) RecordShadowPolicyAssessment(
	ctx context.Context,
	correlationID string,
	assessment ShadowPolicyAssessment,
) (Flow, error) {
	if err := ctx.Err(); err != nil {
		return Flow{}, err
	}
	correlationID = strings.TrimSpace(correlationID)
	if correlationID == "" {
		return Flow{}, fmt.Errorf("correlation_id is required")
	}
	if phase := strings.TrimSpace(assessment.Phase); phase != "planning" && phase != "operation" {
		return Flow{}, fmt.Errorf("shadow policy phase must be planning or operation")
	}
	if strings.TrimSpace(assessment.Mode) == "" {
		assessment.Mode = "shadow"
	}
	if strings.TrimSpace(assessment.ProposedAction) == "" {
		assessment.ProposedAction = "ABSTAIN"
	}
	if strings.TrimSpace(assessment.GuardStatus) == "" {
		assessment.GuardStatus = GuardNotApplied
	}
	if strings.TrimSpace(assessment.CreatedAt) == "" {
		assessment.CreatedAt = service.now().Format(time.RFC3339Nano)
	}
	assessment.Warnings = append([]string(nil), assessment.Warnings...)

	service.mu.Lock()
	defer service.mu.Unlock()
	flow, ok := service.flows[correlationID]
	if !ok {
		return Flow{}, fmt.Errorf("correlation_id does not identify an Agent Control flow")
	}
	flow.ShadowPolicyAssessments = append(flow.ShadowPolicyAssessments, assessment)
	flow.UpdatedAt = service.now().Format(time.RFC3339Nano)
	service.flows[correlationID] = cloneFlow(flow)
	return cloneFlow(flow), nil
}
