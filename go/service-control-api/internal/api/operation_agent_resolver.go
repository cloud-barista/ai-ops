package api

import (
	"fmt"
	"sort"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func resolveOperationAgent(
	registry AgentRegistry,
	runtimeAgents []AgentProfile,
	requestedName string,
) (AgentProfile, error) {
	requestedName = strings.TrimSpace(requestedName)
	if requestedName == "" {
		requestedName = strings.TrimSpace(registry.Defaults[agentcontrol.OperationOptimizationCapability])
		if requestedName == "" {
			return AgentProfile{}, fmt.Errorf(
				"Agent Registry default is required for capability %s",
				agentcontrol.OperationOptimizationCapability,
			)
		}
	}

	for _, agent := range appendOperationAgents(registry, runtimeAgents) {
		if agent.Name != requestedName {
			continue
		}
		if err := validateOperationAgent(agent); err != nil {
			return AgentProfile{}, err
		}
		return agent, nil
	}
	return AgentProfile{}, fmt.Errorf("operation Agent is not registered: %s", requestedName)
}

func eligibleOperationAgents(registry AgentRegistry, runtimeAgents []AgentProfile) []AgentProfile {
	agents := appendOperationAgents(registry, runtimeAgents)
	eligible := make([]AgentProfile, 0, len(agents))
	for _, agent := range agents {
		if validateOperationAgent(agent) == nil {
			eligible = append(eligible, agent)
		}
	}
	sort.SliceStable(eligible, func(i, j int) bool {
		return eligible[i].Name < eligible[j].Name
	})
	return eligible
}

func appendOperationAgents(registry AgentRegistry, runtimeAgents []AgentProfile) []AgentProfile {
	agents := make([]AgentProfile, 0, len(registry.Agents)+len(runtimeAgents))
	for _, agent := range registry.Agents {
		if strings.TrimSpace(agent.Source) == "" {
			agent.Source = agentSourceConfiguration
		}
		agents = append(agents, agent)
	}
	agents = append(agents, runtimeAgents...)
	return agents
}

func validateOperationAgent(agent AgentProfile) error {
	switch {
	case !agent.Enabled:
		return fmt.Errorf("operation Agent is disabled: %s", agent.Name)
	case !contains(agent.Capabilities, agentcontrol.OperationOptimizationCapability):
		return fmt.Errorf(
			"operation Agent %s does not provide capability %s",
			agent.Name,
			agentcontrol.OperationOptimizationCapability,
		)
	case !contains(agent.BoundedActions, agentcontrol.OperationOptimizationDecisionAction):
		return fmt.Errorf(
			"operation Agent %s does not authorize action %s",
			agent.Name,
			agentcontrol.OperationOptimizationDecisionAction,
		)
	default:
		return nil
	}
}
