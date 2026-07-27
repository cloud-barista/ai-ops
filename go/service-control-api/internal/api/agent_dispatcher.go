package api

import (
	"context"
	"errors"
	"fmt"
)

var errInternalAgentExecutorNotRegistered = errors.New("internal Agent executor is not registered")

type agentDispatcher struct {
	internal map[string]agentExecutor
	runtime  agentExecutor
}

func newAgentDispatcher(internal map[string]agentExecutor, runtime agentExecutor) *agentDispatcher {
	executors := make(map[string]agentExecutor, len(internal))
	for name, executor := range internal {
		executors[name] = executor
	}
	return &agentDispatcher{internal: executors, runtime: runtime}
}

func (dispatcher *agentDispatcher) Dispatch(
	ctx context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	if dispatcher == nil {
		return AgentExecutionResult{}, fmt.Errorf("Agent Dispatcher is required")
	}
	var executor agentExecutor
	switch agent.Source {
	case agentSourceConfiguration:
		var ok bool
		executor, ok = dispatcher.internal[agent.Name]
		if !ok || executor == nil {
			return AgentExecutionResult{}, fmt.Errorf(
				"%w: %s",
				errInternalAgentExecutorNotRegistered,
				agent.Name,
			)
		}
	case agentSourceRuntime:
		executor = dispatcher.runtime
		if executor == nil {
			return AgentExecutionResult{}, fmt.Errorf("runtime Agent executor is not registered")
		}
	default:
		return AgentExecutionResult{}, fmt.Errorf("unsupported Agent source: %s", agent.Source)
	}
	return executor.Execute(ctx, agent, request)
}
