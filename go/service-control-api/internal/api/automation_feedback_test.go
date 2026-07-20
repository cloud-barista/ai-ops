package api

import (
	"context"
	"testing"
)

func TestRecordAutomationFeedbackRequiresKnownCorrelationID(t *testing.T) {
	service := NewService(NewServerConfig())
	_, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID: "automation-unknown",
		Executor:      "GenericDeploymentExecutor",
		Status:        "running",
	})
	if err == nil {
		t.Fatal("expected unknown correlation id rejection")
	}
}

func TestRecordAutomationFeedbackStoresExecutionStatusAndMetrics(t *testing.T) {
	service := NewService(NewServerConfig())
	service.automationFeedback.register("automation-known", "GenericDeploymentExecutor")
	latency := 87.5
	throughput := 12.0

	record, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID:       "automation-known",
		Executor:            "GenericDeploymentExecutor",
		Status:              "succeeded",
		ExternalExecutionID: "deployment-123",
		LatencyMS:           &latency,
		ThroughputRPS:       &throughput,
		Message:             "deployment completed",
	})
	if err != nil {
		t.Fatalf("RecordAutomationFeedback returned error: %v", err)
	}
	if record.Status != "succeeded" || record.ExternalExecutionID != "deployment-123" {
		t.Fatalf("unexpected feedback record: %#v", record)
	}
	if record.LatencyMS == nil || *record.LatencyMS != latency || record.ThroughputRPS == nil || *record.ThroughputRPS != throughput {
		t.Fatalf("expected measured performance: %#v", record)
	}
}

func TestAutomationFeedbackRejectsCredentialLikeMessage(t *testing.T) {
	service := NewService(NewServerConfig())
	service.automationFeedback.register("automation-known", "GenericDeploymentExecutor")
	_, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID: "automation-known",
		Executor:      "GenericDeploymentExecutor",
		Status:        "failed",
		Message:       "aws_secret_access_key was invalid",
	})
	if err == nil {
		t.Fatal("expected credential-like feedback rejection")
	}
}
