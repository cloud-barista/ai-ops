package agentcontrol

import (
	"context"
	"testing"
	"time"
)

func TestAutomationRunnerCompletesNaturalLanguageFlow(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType:   InputTypeNaturalLanguage,
		Request:     "Qwen 추론 서비스를 CPU 4코어, 메모리 16GiB, GPU 1개, VRAM 16GiB, 스토리지 20GiB로 배포해 주세요.",
		RequestedBy: "test",
	})
	if err != nil {
		t.Fatalf("run automation: %v", err)
	}

	if run.Status != AutomationRunStatusCompleted {
		t.Fatalf("status = %q, want %q", run.Status, AutomationRunStatusCompleted)
	}
	if run.RequirementAnalysis == nil || run.ResourceRecommendation == nil || run.Flow == nil {
		t.Fatalf("automation stages are incomplete: %#v", run)
	}
	if run.Flow.Decision == nil || run.Flow.Decision.Action != ActionDeploy {
		t.Fatalf("decision = %#v, want DEPLOY", run.Flow.Decision)
	}
	if run.DesiredDeploymentSpec == nil {
		t.Fatal("approved run did not return DesiredDeploymentSpec")
	}
	if run.RequirementAnalysis.Mode != AnalysisModeLocalRule {
		t.Fatalf("analysis mode = %q", run.RequirementAnalysis.Mode)
	}
}

func TestAutomationRunnerCompletesStructuredFlow(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "cpu-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run structured automation: %v", err)
	}
	if run.Flow == nil || run.Flow.Decision == nil || run.Flow.Decision.Action != ActionDeploy {
		t.Fatalf("structured decision = %#v", run.Flow)
	}
	if run.RequirementAnalysis.Mode != AnalysisModeStructured {
		t.Fatalf("analysis mode = %q, want structured", run.RequirementAnalysis.Mode)
	}
}

func TestAutomationRunnerReturnsRejectForInvalidRequirements(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "invalid-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 3,
			ReplicasMax: 1,
		},
	})
	if err != nil {
		t.Fatalf("run invalid automation: %v", err)
	}
	if run.Flow == nil || run.Flow.Decision == nil || run.Flow.Decision.Action != ActionReject {
		t.Fatalf("invalid requirements decision = %#v, want REJECT", run.Flow)
	}
	if run.DesiredDeploymentSpec != nil {
		t.Fatal("REJECT run must not return DesiredDeploymentSpec")
	}
}

func TestAutomationRunnerReturnsRetryWhenCatalogCannotSatisfyProfile(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:                "oversized-service",
			CPUCores:             32,
			MemoryMiB:            131072,
			StorageGiB:           1000,
			AcceleratorType:      "GPU",
			AcceleratorCount:     4,
			AcceleratorMemoryMiB: 98304,
			ReplicasMin:          1,
			ReplicasMax:          2,
		},
	})
	if err != nil {
		t.Fatalf("run oversized automation: %v", err)
	}
	if run.Flow == nil || run.Flow.Decision == nil || run.Flow.Decision.Action != ActionRetry {
		t.Fatalf("oversized decision = %#v, want RETRY", run.Flow)
	}
	if run.DesiredDeploymentSpec != nil {
		t.Fatal("RETRY run must not return DesiredDeploymentSpec")
	}
}

func TestAutomationRunnerPreservesGeneratedTraceIdentifiers(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "trace-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run automation: %v", err)
	}
	if run.RunID != "run-test" || run.CorrelationID != "flow-test" || run.TraceID != "trace-test" {
		t.Fatalf("run identifiers = %#v", run)
	}
	application := run.Flow.ApplicationContext
	recommendation := run.Flow.ResourceRecommendation
	if application == nil || recommendation == nil ||
		application.CorrelationID != run.CorrelationID ||
		recommendation.CorrelationID != run.CorrelationID ||
		application.TraceID != run.TraceID ||
		recommendation.TraceID != run.TraceID {
		t.Fatalf("stage identifiers are inconsistent: %#v", run)
	}
}

func TestAutomationRunnerStopsWhenAgentRegistryRejectsAuthorization(t *testing.T) {
	service := NewServiceWithDependencies(
		nil,
		stubAuthorizer{
			result: AgentAuthorization{
				Authorized: false,
				Reason:     "Agent is disabled",
			},
		},
	)
	runner := testAutomationRunner(t, service)
	run, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "unauthorized-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run unauthorized automation: %v", err)
	}
	if run.Flow == nil || run.Flow.State != StateAgentAuthorizationRejected {
		t.Fatalf("authorization state = %#v", run.Flow)
	}
	if run.Flow.Decision != nil || run.DesiredDeploymentSpec != nil {
		t.Fatalf("unauthorized run created output: %#v", run)
	}
}

func TestAutomationRunnerStoresRunForLookup(t *testing.T) {
	runner := testAutomationRunner(t, NewService())
	created, err := runner.Run(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:       "stored-service",
			CPUCores:    2,
			MemoryMiB:   4096,
			StorageGiB:  20,
			ReplicasMin: 1,
			ReplicasMax: 2,
		},
	})
	if err != nil {
		t.Fatalf("run automation: %v", err)
	}

	stored, ok := runner.Get(created.RunID)
	if !ok {
		t.Fatalf("run %q was not stored", created.RunID)
	}
	if stored.CorrelationID != created.CorrelationID {
		t.Fatalf("stored run = %#v, created = %#v", stored, created)
	}
}

func testAutomationRunner(t *testing.T, service *Service) *AutomationRunner {
	t.Helper()
	runner := NewAutomationRunner(
		LocalRequirementAnalyzer{},
		CatalogResourceRecommender{
			Catalog: testResourceCatalog(),
			Now: func() time.Time {
				return time.Date(2026, 7, 29, 8, 0, 0, 0, time.UTC)
			},
		},
		service,
	)
	runner.Now = func() time.Time {
		return time.Date(2026, 7, 29, 8, 0, 0, 0, time.UTC)
	}
	runner.IDGenerator = func(prefix string) string {
		return prefix + "-test"
	}
	return runner
}
