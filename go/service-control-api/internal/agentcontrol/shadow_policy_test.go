package agentcontrol

import (
	"context"
	"testing"
)

func TestRecordShadowPolicyAssessmentDoesNotChangeRuleDecision(t *testing.T) {
	service := NewService()
	service.flows["flow-1"] = Flow{
		CorrelationID: "flow-1",
		Decision:      &AutomationDecision{Action: ActionDeploy, SelectedCandidateID: "candidate-a"},
	}
	flow, err := service.RecordShadowPolicyAssessment(context.Background(), "flow-1", ShadowPolicyAssessment{
		Phase:          "planning",
		ProposedAction: "SCALE_OUT",
		GuardStatus:    GuardNotApplied,
	})
	if err != nil {
		t.Fatalf("RecordShadowPolicyAssessment() error = %v", err)
	}
	if flow.Decision.Action != ActionDeploy {
		t.Fatalf("rule action = %q, want %q", flow.Decision.Action, ActionDeploy)
	}
	if len(flow.ShadowPolicyAssessments) != 1 || flow.ShadowPolicyAssessments[0].ProposedAction != "SCALE_OUT" {
		t.Fatalf("shadow assessments = %#v", flow.ShadowPolicyAssessments)
	}
}

func TestApplyDeploymentContextProducesMockSafeUpdateRevision(t *testing.T) {
	service := NewService()
	request := DeploymentCreateRequestEnvelope{}
	request.Data.DeploymentRequest = DeploymentRequest{DeploymentManifest: DeploymentManifest{}}
	service.flows["flow-2"] = Flow{
		CorrelationID:         "flow-2",
		Decision:              &AutomationDecision{Action: ActionDeploy},
		DeploymentPlan:        &DeploymentPlan{Operation: ActionDeploy},
		DesiredDeploymentSpec: &DesiredDeploymentSpec{SpecVersion: ContractVersionV1, Operation: ActionDeploy},
		DeploymentRequest:     &request,
		ManifestRevisions: []ManifestRevision{{
			TriggerAction:         ActionDeploy,
			DesiredDeploymentSpec: DesiredDeploymentSpec{SpecVersion: ContractVersionV1, Operation: ActionDeploy},
			DeploymentRequest:     request,
		}},
	}
	flow, err := service.ApplyDeploymentContext(context.Background(), "flow-2", DeploymentContext{
		DeploymentRef: "logical-app-a",
		SpecDrift:     true,
	})
	if err != nil {
		t.Fatalf("ApplyDeploymentContext() error = %v", err)
	}
	if flow.State != StateUpdateApproved || flow.Decision.Action != ActionUpdate {
		t.Fatalf("update decision = state=%q action=%q", flow.State, flow.Decision.Action)
	}
	if flow.DesiredDeploymentSpec.Operation != ActionUpdate || flow.DeploymentRequest.Data.DeploymentRequest.DeploymentManifest.Operation != ActionUpdate {
		t.Fatalf("update operation was not propagated: %#v", flow)
	}
	if flow.ManifestRevisions[0].TriggerAction != ActionUpdate {
		t.Fatalf("revision trigger = %q, want %q", flow.ManifestRevisions[0].TriggerAction, ActionUpdate)
	}
}
