package trustedorchestration

import (
	"context"
	"testing"

	"kyunghee-aiops/service-control-api/internal/audittrail"
)

func TestNormalizedAuditAcceleratorTypeUsesBoundedEnums(t *testing.T) {
	tests := []struct {
		name     string
		required bool
		value    string
		want     string
	}{
		{name: "gpu", required: true, value: "gpu", want: "GPU"},
		{name: "nvidia gpu", required: true, value: "NVIDIA_GPU", want: "NVIDIA_GPU"},
		{name: "not required", value: "", want: "NONE"},
		{name: "missing required type", required: true, value: "", want: "OTHER"},
		{name: "arbitrary value", required: true, value: "Bearer secret-token", want: "OTHER"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := normalizedAuditAcceleratorType(test.required, test.value); got != test.want {
				t.Fatalf("normalized accelerator = %q, want %q", got, test.want)
			}
		})
	}
}

func TestApprovedFlowRecordsEveryTrustedStageInOrder(t *testing.T) {
	input := approvedTestInput()
	recorder := &collectingAuditRecorder{}
	ctx := audittrail.WithRecorder(context.Background(), recorder)
	orchestrator := NewFlowOnly(
		&recordingReviewer{result: approvedSafeguard(input.Request)},
		integrationAutomationRunner(),
	)

	result, err := orchestrator.RunApprovedFlow(ctx, input)
	if err != nil {
		t.Fatalf("run audited flow: %v", err)
	}
	if result.Status != StatusApprovedFlowReady {
		t.Fatalf("status = %q", result.Status)
	}
	want := []struct {
		stage  string
		action string
	}{
		{stage: audittrail.StageSafeguard, action: "review_started"},
		{stage: audittrail.StageSafeguard, action: "review_completed"},
		{stage: audittrail.StageSafeguard, action: "approval_binding_validated"},
		{stage: audittrail.StageGeon, action: "revision_flow_started"},
		{stage: audittrail.StageGeon, action: "revision_flow_completed"},
	}
	if len(recorder.events) != len(want) {
		t.Fatalf("audit event count = %d, want %d: %#v", len(recorder.events), len(want), recorder.events)
	}
	for index, expected := range want {
		event := recorder.events[index]
		if event.Stage != expected.stage || event.Action != expected.action {
			t.Fatalf("event %d = %s/%s, want %s/%s", index, event.Stage, event.Action, expected.stage, expected.action)
		}
	}
	completed := recorder.events[len(recorder.events)-1]
	if completed.Evidence.CandidateIDSHA256 == "" ||
		completed.Evidence.Resources == nil ||
		completed.Evidence.Resources.AcceleratorType != "NONE" {
		t.Fatalf("bounded geon evidence = %#v", completed.Evidence)
	}
	if recorder.identity.RunID == "" || recorder.identity.CorrelationID != input.Request.CorrelationID {
		t.Fatalf("bound automation identity = %#v", recorder.identity)
	}
}

type collectingAuditRecorder struct {
	events   []audittrail.Event
	identity audittrail.Identity
}

func (recorder *collectingAuditRecorder) Record(event audittrail.Event) error {
	recorder.events = append(recorder.events, event)
	return nil
}

func (recorder *collectingAuditRecorder) BindIdentity(identity audittrail.Identity) error {
	if identity.RequestID != "" {
		recorder.identity.RequestID = identity.RequestID
	}
	if identity.MessageID != "" {
		recorder.identity.MessageID = identity.MessageID
	}
	if identity.CorrelationID != "" {
		recorder.identity.CorrelationID = identity.CorrelationID
	}
	if identity.TraceID != "" {
		recorder.identity.TraceID = identity.TraceID
	}
	if identity.RunID != "" {
		recorder.identity.RunID = identity.RunID
	}
	return nil
}
