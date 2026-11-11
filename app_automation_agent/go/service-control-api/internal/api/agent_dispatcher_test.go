package api

import (
	"context"
	"errors"
	"testing"
)

type recordingAgentExecutor struct {
	calls   int
	agent   AgentProfile
	request AgentDispatchRequest
	result  AgentExecutionResult
	err     error
}

func (executor *recordingAgentExecutor) Execute(
	_ context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	executor.calls++
	executor.agent = agent
	executor.request = request
	return executor.result, executor.err
}

func TestAgentDispatcherUsesInternalExecutorForConfigurationAgent(t *testing.T) {
	internal := &recordingAgentExecutor{result: AgentExecutionResult{Status: "completed"}}
	runtime := &recordingAgentExecutor{}
	dispatcher := newAgentDispatcher(
		map[string]agentExecutor{"AIApplicationAutomationAgent": internal},
		runtime,
	)
	agent := AgentProfile{
		Name:   "AIApplicationAutomationAgent",
		Source: agentSourceConfiguration,
	}
	request := AgentDispatchRequest{RunID: "run-001", Agent: agent.Name}

	result, err := dispatcher.Dispatch(context.Background(), agent, request)
	if err != nil || result.Status != "completed" {
		t.Fatalf("dispatch internal Agent: result=%#v err=%v", result, err)
	}
	if internal.calls != 1 || runtime.calls != 0 || internal.agent.Name != agent.Name {
		t.Fatalf("wrong executor selected: internal=%d runtime=%d agent=%#v", internal.calls, runtime.calls, internal.agent)
	}
}

func TestAgentDispatcherUsesHTTPExecutorForRuntimeAgent(t *testing.T) {
	internal := &recordingAgentExecutor{}
	runtime := &recordingAgentExecutor{result: AgentExecutionResult{Status: "completed"}}
	dispatcher := newAgentDispatcher(
		map[string]agentExecutor{"AIApplicationAutomationAgent": internal},
		runtime,
	)
	agent := AgentProfile{Name: "ExternalResearchAgent", Source: agentSourceRuntime}
	request := AgentDispatchRequest{RunID: "run-002", Agent: agent.Name}

	if _, err := dispatcher.Dispatch(context.Background(), agent, request); err != nil {
		t.Fatalf("dispatch runtime Agent: %v", err)
	}
	if internal.calls != 0 || runtime.calls != 1 || runtime.request.RunID != "run-002" {
		t.Fatalf("wrong executor selected: internal=%d runtime=%d request=%#v", internal.calls, runtime.calls, runtime.request)
	}
}

func TestAgentDispatcherRejectsUnknownInternalAgent(t *testing.T) {
	dispatcher := newAgentDispatcher(nil, &recordingAgentExecutor{})
	_, err := dispatcher.Dispatch(
		context.Background(),
		AgentProfile{Name: "UnknownInternalAgent", Source: agentSourceConfiguration},
		AgentDispatchRequest{RunID: "run-003", Agent: "UnknownInternalAgent"},
	)
	if !errors.Is(err, errInternalAgentExecutorNotRegistered) {
		t.Fatalf("error=%v want internal executor registration error", err)
	}
}

func TestAgentDispatcherRejectsUnsupportedSource(t *testing.T) {
	dispatcher := newAgentDispatcher(nil, &recordingAgentExecutor{})
	if _, err := dispatcher.Dispatch(
		context.Background(),
		AgentProfile{Name: "UnsupportedAgent", Source: "unknown"},
		AgentDispatchRequest{RunID: "run-004", Agent: "UnsupportedAgent"},
	); err == nil {
		t.Fatal("expected unsupported Agent source to be rejected")
	}
}
