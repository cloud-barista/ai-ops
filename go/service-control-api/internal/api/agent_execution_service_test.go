package api

import (
	"context"
	"errors"
	"testing"

	"kyunghee-aiops/service-control-api/internal/controlrun"
)

type echoAgentExecutor struct {
	calls int
	err   error
}

func (executor *echoAgentExecutor) Execute(
	_ context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	executor.calls++
	if executor.err != nil {
		return AgentExecutionResult{}, executor.err
	}
	return AgentExecutionResult{
		RunID:  request.RunID,
		Agent:  agent.Name,
		Status: "completed",
		Proposal: AgentProposal{
			Action:     request.Action,
			Parameters: map[string]any{"decision": "approved"},
		},
		Result:           map[string]any{"review": "approved"},
		Evidence:         map[string]any{"source": "test-executor"},
		Message:          "review completed",
		LatencyMS:        12,
		DomainValidation: "not_registered",
	}, nil
}

func TestExecuteRuntimeAgentRecordsApprovedControlRun(t *testing.T) {
	service := NewService(NewServerConfig())
	registerRuntimeExecutionAgent(t, service)
	executor := &echoAgentExecutor{}
	dispatcher := newAgentDispatcher(nil, executor)

	response, err := service.ExecuteAgentWithDispatcher(
		context.Background(),
		"ExternalResearchAgent",
		AgentExecutionRequest{
			Capability: "deployment_review",
			Action:     "review_deployment_plan",
			Input:      map[string]any{"workload": "llm-chat-inference"},
		},
		dispatcher,
	)
	if err != nil {
		t.Fatalf("execute runtime Agent: %v", err)
	}
	if executor.calls != 1 || response.RunID == "" {
		t.Fatalf("Dispatcher execution evidence is missing: calls=%d response=%#v", executor.calls, response)
	}
	if !response.RequestGuard.Valid || !response.ResultGuard.Valid || response.Execution.Status != "completed" {
		t.Fatalf("unexpected guarded execution response: %#v", response)
	}

	run, ok := service.GetControlRun(response.RunID)
	if !ok {
		t.Fatal("Agent execution ControlRun was not stored")
	}
	if run.Status != controlrun.StatusAgentCompleted || run.Execution == nil {
		t.Fatalf("unexpected Agent ControlRun: %#v", run)
	}
	if run.Execution.Result["review"] != "approved" || run.Execution.GuardStatus != "approved" {
		t.Fatalf("Agent result was not attached to ControlRun: %#v", run.Execution)
	}
	wantStages := []string{"agent_registry", "agent_request_guard", "agent_dispatch", "agent_result_guard"}
	if len(run.Stages) != len(wantStages) {
		t.Fatalf("unexpected Agent stages: %#v", run.Stages)
	}
	for index, want := range wantStages {
		if run.Stages[index].Name != want || run.Stages[index].Status != "approved" {
			t.Fatalf("stage %d=%#v want=%s approved", index, run.Stages[index], want)
		}
	}
}

func TestExecuteRuntimeAgentStopsBeforeDispatchWhenGuardRejects(t *testing.T) {
	service := NewService(NewServerConfig())
	registerRuntimeExecutionAgent(t, service)
	executor := &echoAgentExecutor{}

	response, err := service.ExecuteAgentWithDispatcher(
		context.Background(),
		"ExternalResearchAgent",
		AgentExecutionRequest{
			Capability: "deployment_review",
			Action:     "restart_application",
		},
		newAgentDispatcher(nil, executor),
	)
	if err == nil {
		t.Fatal("expected unbounded action to be rejected")
	}
	if executor.calls != 0 || response.RequestGuard.Valid {
		t.Fatalf("rejected request reached Dispatcher: calls=%d response=%#v", executor.calls, response)
	}
	run, ok := service.GetControlRun(response.RunID)
	if !ok || run.Status != controlrun.StatusAgentRejected {
		t.Fatalf("Guard rejection was not recorded: %#v", run)
	}
}

func TestExecuteRuntimeAgentRecordsEndpointFailure(t *testing.T) {
	service := NewService(NewServerConfig())
	registerRuntimeExecutionAgent(t, service)
	executor := &echoAgentExecutor{err: errors.New("endpoint unavailable")}

	response, err := service.ExecuteAgentWithDispatcher(
		context.Background(),
		"ExternalResearchAgent",
		AgentExecutionRequest{
			Capability: "deployment_review",
			Action:     "review_deployment_plan",
		},
		newAgentDispatcher(nil, executor),
	)
	if err == nil {
		t.Fatal("expected endpoint failure")
	}
	run, ok := service.GetControlRun(response.RunID)
	if !ok || run.Status != controlrun.StatusAgentFailed || run.Execution == nil {
		t.Fatalf("endpoint failure was not recorded: %#v", run)
	}
	if run.Execution.Status != "failed" || run.Execution.Message == "" {
		t.Fatalf("endpoint failure evidence is incomplete: %#v", run.Execution)
	}
}

func registerRuntimeExecutionAgent(t *testing.T, service Service) {
	t.Helper()
	_, err := service.RegisterExternalAgent(context.Background(), ExternalAgentRegistrationRequest{
		Name:           "ExternalResearchAgent",
		Version:        "0.1.0",
		Role:           "Review deployment research evidence.",
		Endpoint:       "http://127.0.0.1:19090",
		InvocationPath: "/invoke",
		Capabilities:   []string{"deployment_review"},
		BoundedActions: []string{"review_deployment_plan"},
	})
	if err != nil {
		t.Fatalf("register runtime Agent: %v", err)
	}
}
