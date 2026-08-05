package api

import (
	"context"
	"errors"
	"testing"
)

func TestRegisterExternalAgentAndBuildInvocationPlan(t *testing.T) {
	service := NewService(NewServerConfig())
	ctx := context.Background()

	registered, err := service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "ResearchModelMonitorAgent",
		KoreanName:     "연구 모델 모니터링 에이전트",
		Version:        "0.1.0",
		Role:           "Review model runtime signals.",
		Endpoint:       "http://model-monitor.internal:8090",
		InvocationPath: "/v1/actions",
		Capabilities:   []string{"model_monitoring"},
		BoundedActions: []string{"collect_model_metrics"},
	})
	if err != nil {
		t.Fatalf("RegisterExternalAgent returned error: %v", err)
	}
	if registered.Source != agentSourceRuntime {
		t.Fatalf("expected runtime source, got %s", registered.Source)
	}
	if !registered.Enabled {
		t.Fatal("new external agent must be enabled by default")
	}

	valid, err := service.ValidateAgentAction(ctx, registered.Name, "collect_model_metrics")
	if err != nil {
		t.Fatalf("ValidateAgentAction returned error: %v", err)
	}
	if !valid {
		t.Fatal("registered bounded action must be valid")
	}

	plan, err := service.BuildAgentInvocationPlan(ctx, registered.Name, AgentInvocationPlanRequest{
		Capability: "model_monitoring",
		Action:     "collect_model_metrics",
		Parameters: map[string]any{"workload": "llm-chat-inference"},
	})
	if err != nil {
		t.Fatalf("BuildAgentInvocationPlan returned error: %v", err)
	}
	if !plan.Valid || plan.ExecutionStatus != invocationStatusNotExecuted {
		t.Fatalf("unexpected invocation plan: %#v", plan)
	}
	if plan.TargetURL != "http://model-monitor.internal:8090/v1/actions" {
		t.Fatalf("unexpected target URL: %s", plan.TargetURL)
	}
}

func TestRegisterExternalAgentRejectsDuplicateAndUnsafeEndpoint(t *testing.T) {
	service := NewService(NewServerConfig())
	ctx := context.Background()

	_, err := service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "AIApplicationAutomationAgent",
		Version:        "0.1.0",
		Role:           "Duplicate static agent.",
		Endpoint:       "https://agent.example.com",
		InvocationPath: "/actions",
		Capabilities:   []string{"deployment_planning"},
		BoundedActions: []string{"app_plan_deployment"},
	})
	if err == nil {
		t.Fatal("expected duplicate static agent name to be rejected")
	}

	_, err = service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "UnsafeEndpointAgent",
		Version:        "0.1.0",
		Role:           "Invalid endpoint example.",
		Endpoint:       "file:///etc/passwd",
		InvocationPath: "/actions",
		Capabilities:   []string{"deployment_planning"},
		BoundedActions: []string{"app_plan_deployment"},
	})
	if err == nil {
		t.Fatal("expected non-HTTP endpoint to be rejected")
	}
}

func TestRegisterExternalAgentValidatesAuthTokenEnvironmentReference(t *testing.T) {
	service := NewService(NewServerConfig())
	ctx := context.Background()
	registered, err := service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "AuthenticatedResearchAgent",
		Version:        "0.1.0",
		Role:           "Review deployment research evidence.",
		Endpoint:       "https://agent.example.com",
		InvocationPath: "/v1/actions",
		Capabilities:   []string{"deployment_review"},
		BoundedActions: []string{"review_deployment_plan"},
		AuthTokenEnv:   "RESEARCH_AGENT_TOKEN",
	})
	if err != nil {
		t.Fatalf("register authenticated Agent: %v", err)
	}
	if registered.AuthTokenEnv != "RESEARCH_AGENT_TOKEN" {
		t.Fatalf("auth token environment reference was not retained: %#v", registered)
	}

	_, err = service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "UnsafeAuthReferenceAgent",
		Version:        "0.1.0",
		Role:           "Invalid authentication reference.",
		Endpoint:       "https://agent.example.com",
		InvocationPath: "/v1/actions",
		Capabilities:   []string{"deployment_review"},
		BoundedActions: []string{"review_deployment_plan"},
		AuthTokenEnv:   "value-with-lowercase",
	})
	if err == nil {
		t.Fatal("expected unsafe authentication environment name to be rejected")
	}
}

func TestBuildAgentInvocationPlanRejectsUnboundedCapabilityAndAction(t *testing.T) {
	service := NewService(NewServerConfig())
	ctx := context.Background()
	_, err := service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "PlacementReviewAgent",
		Version:        "0.1.0",
		Role:           "Review placement plans.",
		Endpoint:       "https://placement-agent.example.com",
		InvocationPath: "/v1/actions",
		Capabilities:   []string{"placement_review"},
		BoundedActions: []string{"review_vm_placement"},
	})
	if err != nil {
		t.Fatalf("RegisterExternalAgent returned error: %v", err)
	}

	_, err = service.BuildAgentInvocationPlan(ctx, "PlacementReviewAgent", AgentInvocationPlanRequest{
		Capability: "cost_review",
		Action:     "review_vm_placement",
	})
	if err == nil {
		t.Fatal("expected unregistered capability to be rejected")
	}

	_, err = service.BuildAgentInvocationPlan(ctx, "PlacementReviewAgent", AgentInvocationPlanRequest{
		Capability: "placement_review",
		Action:     "restart_vm",
	})
	if err == nil {
		t.Fatal("expected unbounded action to be rejected")
	}
}

func TestDeleteExternalAgentRemovesRuntimeAgent(t *testing.T) {
	service := NewService(NewServerConfig())
	ctx := context.Background()
	registered, err := service.RegisterExternalAgent(ctx, ExternalAgentRegistrationRequest{
		Name:           "ResearchModelMonitorAgent",
		Version:        "0.1.0",
		Role:           "Review model runtime signals.",
		Endpoint:       "http://model-monitor.internal:8090",
		InvocationPath: "/v1/actions",
		Capabilities:   []string{"model_monitoring"},
		BoundedActions: []string{"collect_model_metrics"},
	})
	if err != nil {
		t.Fatalf("register runtime agent: %v", err)
	}

	deleted, err := service.DeleteExternalAgent(ctx, registered.Name)
	if err != nil || deleted.Name != registered.Name || deleted.Source != agentSourceRuntime {
		t.Fatalf("unexpected deletion: deleted=%#v err=%v", deleted, err)
	}
	if _, err := service.ShowAgent(ctx, registered.Name); err == nil {
		t.Fatal("deleted runtime agent is still visible")
	}
	if _, err := service.BuildAgentInvocationPlan(ctx, registered.Name, AgentInvocationPlanRequest{
		Capability: "model_monitoring",
		Action:     "collect_model_metrics",
	}); err == nil {
		t.Fatal("deleted runtime agent still produces invocation plans")
	}
}

func TestDeleteExternalAgentRejectsConfiguredAndMissingAgents(t *testing.T) {
	service := NewService(NewServerConfig())
	ctx := context.Background()

	if _, err := service.DeleteExternalAgent(ctx, "AIApplicationAutomationAgent"); !errors.Is(err, errConfiguredAgentProtected) {
		t.Fatalf("configured agent deletion error=%v", err)
	}
	if _, err := service.DeleteExternalAgent(ctx, "MissingRuntimeAgent"); !errors.Is(err, errRuntimeAgentNotFound) {
		t.Fatalf("missing agent deletion error=%v", err)
	}
}
