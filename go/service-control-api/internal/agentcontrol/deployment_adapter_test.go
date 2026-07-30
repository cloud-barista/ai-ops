package agentcontrol

import (
	"context"
	"testing"
	"time"
)

func TestMockDeploymentAdapterMarksSubmissionSimulated(t *testing.T) {
	adapter := MockDeploymentAdapter{Now: adapterTestTime}

	result, err := adapter.Submit(context.Background(), adapterTestDeploymentRequest())
	if err != nil {
		t.Fatalf("submit mock deployment: %v", err)
	}
	if result.Adapter != DeploymentAdapterMock {
		t.Fatalf("adapter = %q, want %q", result.Adapter, DeploymentAdapterMock)
	}
	if result.Status != DeploymentSubmissionSimulated {
		t.Fatalf("status = %q, want %q", result.Status, DeploymentSubmissionSimulated)
	}
	if !result.Simulated {
		t.Fatal("mock submission must be marked simulated")
	}
	if result.RequestID != "deploy-request-test" {
		t.Fatalf("request_id = %q", result.RequestID)
	}
	if result.SubmittedAt != "2026-07-30T02:00:00Z" {
		t.Fatalf("submitted_at = %q", result.SubmittedAt)
	}
}

func TestHandoffDeploymentAdapterMarksRequestReady(t *testing.T) {
	adapter := HandoffDeploymentAdapter{Now: adapterTestTime}

	result, err := adapter.Submit(context.Background(), adapterTestDeploymentRequest())
	if err != nil {
		t.Fatalf("submit handoff deployment: %v", err)
	}
	if result.Adapter != DeploymentAdapterHandoff {
		t.Fatalf("adapter = %q, want %q", result.Adapter, DeploymentAdapterHandoff)
	}
	if result.Status != DeploymentSubmissionReady {
		t.Fatalf("status = %q, want %q", result.Status, DeploymentSubmissionReady)
	}
	if result.Simulated {
		t.Fatal("handoff submission must not be marked simulated")
	}
	if result.RequestID != "deploy-request-test" {
		t.Fatalf("request_id = %q", result.RequestID)
	}
}

func TestDeploymentAdaptersRejectMissingRequestID(t *testing.T) {
	for name, adapter := range map[string]DeploymentAdapter{
		"mock":    MockDeploymentAdapter{},
		"handoff": HandoffDeploymentAdapter{},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := adapter.Submit(context.Background(), DeploymentCreateRequestEnvelope{})
			if err == nil {
				t.Fatal("missing request_id was accepted")
			}
		})
	}
}

func TestNewDeploymentAdapterRejectsUnknownMode(t *testing.T) {
	adapter, err := NewDeploymentAdapter("external")
	if err == nil {
		t.Fatal("unknown deployment adapter mode was accepted")
	}
	if adapter != nil {
		t.Fatalf("adapter = %#v, want nil", adapter)
	}
}

func adapterTestDeploymentRequest() DeploymentCreateRequestEnvelope {
	return DeploymentCreateRequestEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-deploy-test",
			MessageType:     MessageDeploymentCreateRequest,
			OccurredAt:      "2026-07-30T01:59:00Z",
			CorrelationID:   "flow-test",
			TraceID:         "trace-test",
		},
		Data: DeploymentCreateRequestData{
			DeploymentRequest: DeploymentRequest{
				RequestID:  "deploy-request-test",
				DecisionID: "decision-test",
			},
		},
	}
}

func adapterTestTime() time.Time {
	return time.Date(2026, 7, 30, 2, 0, 0, 0, time.UTC)
}
