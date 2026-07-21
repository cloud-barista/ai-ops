package api

import (
	"context"
	"errors"
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

func TestAutomationFeedbackListAndDeletePreserveApprovedCorrelation(t *testing.T) {
	service := NewService(NewServerConfig())
	service.automationFeedback.register("automation-first", "GenericDeploymentExecutor")
	service.automationFeedback.register("automation-second", "GenericDeploymentExecutor")
	for _, correlationID := range []string{"automation-first", "automation-second"} {
		if _, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
			CorrelationID: correlationID,
			Executor:      "GenericDeploymentExecutor",
			Status:        "running",
		}); err != nil {
			t.Fatal(err)
		}
	}

	records, err := service.ListAutomationFeedback(context.Background())
	if err != nil || len(records) != 2 {
		t.Fatalf("unexpected feedback list: records=%#v err=%v", records, err)
	}
	deleted, err := service.DeleteAutomationFeedback(context.Background(), "automation-first")
	if err != nil || deleted.CorrelationID != "automation-first" {
		t.Fatalf("unexpected feedback deletion: deleted=%#v err=%v", deleted, err)
	}
	if _, err := service.DeleteAutomationFeedback(context.Background(), "automation-first"); !errors.Is(err, errAutomationFeedbackNotFound) {
		t.Fatalf("missing feedback error=%v", err)
	}
	if _, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID: "automation-first",
		Executor:      "GenericDeploymentExecutor",
		Status:        "succeeded",
	}); err != nil {
		t.Fatalf("approved correlation could not be recorded again: %v", err)
	}
}

func TestClearAutomationFeedbackPreservesApprovedCorrelations(t *testing.T) {
	service := NewService(NewServerConfig())
	service.automationFeedback.register("automation-clear", "GenericDeploymentExecutor")
	if _, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID: "automation-clear",
		Executor:      "GenericDeploymentExecutor",
		Status:        "running",
	}); err != nil {
		t.Fatal(err)
	}

	deleted, err := service.ClearAutomationFeedback(context.Background())
	if err != nil || deleted != 1 {
		t.Fatalf("unexpected feedback clear: deleted=%d err=%v", deleted, err)
	}
	records, err := service.ListAutomationFeedback(context.Background())
	if err != nil || len(records) != 0 {
		t.Fatalf("feedback remained after clear: records=%#v err=%v", records, err)
	}
	if _, err := service.RecordAutomationFeedback(context.Background(), AutomationFeedbackRequest{
		CorrelationID: "automation-clear",
		Executor:      "GenericDeploymentExecutor",
		Status:        "succeeded",
	}); err != nil {
		t.Fatalf("approved correlation could not be recorded after clear: %v", err)
	}
}
