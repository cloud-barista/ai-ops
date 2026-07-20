package automation

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func TestPlannerReturnsExecutedStructuredProposal(t *testing.T) {
	planner, candidate, closeServer := plannerWithResponse(t, `{
  "action":"observe_status",
  "reason":"Performance evidence is not measured.",
  "confidence":0.82,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"recorded-vm-id",
  "parameters":{"next_check":"measure_workload_performance"}
}`)
	defer closeServer()

	result, err := planner.Plan(context.Background(), candidate, decisionContext())
	if err != nil {
		t.Fatalf("Plan returned error: %v", err)
	}
	if result.ExecutionStatus != "executed" {
		t.Fatalf("expected executed, got %s", result.ExecutionStatus)
	}
	if result.Proposal.Action != "observe_status" || result.Proposal.TargetVMID != "recorded-vm-id" {
		t.Fatalf("unexpected proposal: %#v", result.Proposal)
	}
	if result.ActualModel != "test-model" || result.Provider != "test-provider" {
		t.Fatalf("missing execution metadata: %#v", result)
	}
}

func TestPlannerRejectsMalformedJSON(t *testing.T) {
	planner, candidate, closeServer := plannerWithResponse(t, `not-json`)
	defer closeServer()

	result, err := planner.Plan(context.Background(), candidate, decisionContext())
	if err == nil || !strings.Contains(err.Error(), "parse action proposal") {
		t.Fatalf("expected parse error, got result=%#v err=%v", result, err)
	}
	if result.ExecutionStatus != "rejected" {
		t.Fatalf("expected rejected, got %s", result.ExecutionStatus)
	}
}

func TestPlannerRejectsConfidenceOutsideZeroToOne(t *testing.T) {
	planner, candidate, closeServer := plannerWithResponse(t, `{
  "action":"observe_status",
  "reason":"invalid confidence",
  "confidence":1.2,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"recorded-vm-id"
}`)
	defer closeServer()

	result, err := planner.Plan(context.Background(), candidate, decisionContext())
	if err == nil || !strings.Contains(err.Error(), "confidence") {
		t.Fatalf("expected confidence error, got result=%#v err=%v", result, err)
	}
}

func TestPlannerRejectsTargetVMSubstitution(t *testing.T) {
	planner, candidate, closeServer := plannerWithResponse(t, `{
  "action":"observe_status",
  "reason":"changed target",
  "confidence":0.7,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"different-vm"
}`)
	defer closeServer()

	result, err := planner.Plan(context.Background(), candidate, decisionContext())
	if err == nil || !strings.Contains(err.Error(), "target VM") {
		t.Fatalf("expected target VM error, got result=%#v err=%v", result, err)
	}
}

func TestPlannerPromptRequiresExactAllowedActionValue(t *testing.T) {
	client := &promptCaptureClient{}
	planner := NewPlanner(client)
	_, err := planner.Plan(context.Background(), llmclient.Candidate{
		CandidateID: "test-candidate",
		Provider:    "test-provider",
		ActualModel: "test-model",
		Enabled:     true,
	}, decisionContext())
	if err != nil {
		t.Fatalf("Plan returned error: %v", err)
	}
	if !strings.Contains(client.userPrompt, "Choose exactly one action string verbatim from allowed_actions") {
		t.Fatalf("prompt does not state the exact bounded Action rule: %s", client.userPrompt)
	}
	if !strings.Contains(client.userPrompt, "Do not return allow, deny, approve, or reject as the action") {
		t.Fatalf("prompt does not reject generic policy words: %s", client.userPrompt)
	}
	if !strings.Contains(client.userPrompt, "Do not repeat the Decision context or add keys such as checks or allowed_actions") {
		t.Fatalf("prompt does not forbid copied context fields: %s", client.userPrompt)
	}
	if !strings.Contains(client.userPrompt, `"parameters":{}`) {
		t.Fatalf("prompt does not provide an exact output template: %s", client.userPrompt)
	}
}

type promptCaptureClient struct {
	userPrompt string
}

func (client *promptCaptureClient) Complete(
	_ context.Context,
	_ llmclient.Candidate,
	_ string,
	userPrompt string,
) (llmclient.Completion, error) {
	client.userPrompt = userPrompt
	return llmclient.Completion{
		Status: "executed",
		Content: `{
  "action":"observe_status",
  "reason":"Performance evidence is not measured.",
  "confidence":0.82,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"recorded-vm-id"
}`,
	}, nil
}

func plannerWithResponse(t *testing.T, content string) (Planner, llmclient.Candidate, func()) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("content-type", "application/json")
		_, _ = fmt.Fprintf(writer, `{"choices":[{"message":{"content":%q}}]}`, content)
	}))
	candidate := llmclient.Candidate{
		CandidateID: "test-candidate",
		Provider:    "test-provider",
		ActualModel: "test-model",
		Endpoint:    server.URL,
		Enabled:     true,
	}
	return NewPlanner(llmclient.NewClient(nil)), candidate, server.Close
}

func decisionContext() DecisionContext {
	return DecisionContext{
		Workload:            "llm-chat-inference",
		ServiceName:         "llm-chat-inference",
		TargetVMID:          "recorded-vm-id",
		CompatibilityStatus: "provisionally_compatible",
		AllowedActions:      []string{"deploy_application", "observe_status"},
		RequiredCapability:  "ai_application_deployment_control",
	}
}
