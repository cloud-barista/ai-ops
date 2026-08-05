package api

import (
	"context"
	"fmt"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

type agentControlRegistryAuthorizer struct {
	config ServerConfig
}

func newAgentControlRegistryAuthorizer(config ServerConfig) agentControlRegistryAuthorizer {
	return agentControlRegistryAuthorizer{config: config}
}

func (authorizer agentControlRegistryAuthorizer) Authorize(
	ctx context.Context,
	request agentcontrol.AgentAuthorizationRequest,
) (agentcontrol.AgentAuthorization, error) {
	result := agentcontrol.AgentAuthorization{
		AgentName:  request.AgentName,
		Capability: request.Capability,
		Action:     request.Action,
	}
	if err := ensureContext(ctx); err != nil {
		return result, err
	}

	registry, err := loadAgentRegistry(authorizer.config.path("config", "agent_registry.json"))
	if err != nil {
		return result, err
	}
	agent, err := findAgent(registry.Agents, request.AgentName)
	if err != nil {
		result.Reason = "The required automation Agent is not registered."
		return result, nil
	}
	switch {
	case !agent.Enabled:
		result.Reason = "The required automation Agent is disabled."
	case !contains(agent.Capabilities, request.Capability):
		result.Reason = fmt.Sprintf(
			"Agent Registry does not grant capability %q.",
			request.Capability,
		)
	case !contains(agent.BoundedActions, request.Action):
		result.Reason = fmt.Sprintf(
			"Agent Registry does not grant bounded action %q.",
			request.Action,
		)
	default:
		result.Authorized = true
		result.Reason = "Agent Registry authorizes the required capability and bounded action."
	}
	return result, nil
}
