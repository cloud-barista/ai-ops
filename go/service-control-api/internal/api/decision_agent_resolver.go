package api

import (
	"fmt"
	"sort"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func resolveDecisionAgent(
	registry AgentRegistry,
	runtimeAgents []AgentProfile,
	requestedName string,
) (AgentProfile, error) {
	requestedName = strings.TrimSpace(requestedName)
	if requestedName == "" {
		requestedName = strings.TrimSpace(registry.Defaults[agentcontrol.AutomationCapability])
		if requestedName == "" {
			return AgentProfile{}, fmt.Errorf(
				"Agent Registry default is required for capability %s",
				agentcontrol.AutomationCapability,
			)
		}
	}

	for _, agent := range appendDecisionAgents(registry, runtimeAgents) {
		if agent.Name != requestedName {
			continue
		}
		if err := validateDecisionAgent(agent); err != nil {
			return AgentProfile{}, err
		}
		return agent, nil
	}
	return AgentProfile{}, fmt.Errorf("decision Agent is not registered: %s", requestedName)
}

func eligibleDecisionAgents(registry AgentRegistry, runtimeAgents []AgentProfile) []AgentProfile {
	agents := appendDecisionAgents(registry, runtimeAgents)
	eligible := make([]AgentProfile, 0, len(agents))
	for _, agent := range agents {
		if validateDecisionAgent(agent) == nil {
			eligible = append(eligible, agent)
		}
	}
	sort.SliceStable(eligible, func(i, j int) bool {
		return eligible[i].Name < eligible[j].Name
	})
	return eligible
}

func appendDecisionAgents(registry AgentRegistry, runtimeAgents []AgentProfile) []AgentProfile {
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

func validateDecisionAgent(agent AgentProfile) error {
	switch {
	case !agent.Enabled:
		return fmt.Errorf("decision Agent is disabled: %s", agent.Name)
	case !contains(agent.Capabilities, agentcontrol.AutomationCapability):
		return fmt.Errorf(
			"decision Agent %s does not provide capability %s",
			agent.Name,
			agentcontrol.AutomationCapability,
		)
	case !contains(agent.BoundedActions, agentcontrol.AutomationDecisionAction):
		return fmt.Errorf(
			"decision Agent %s does not authorize action %s",
			agent.Name,
			agentcontrol.AutomationDecisionAction,
		)
	default:
		return nil
	}
}
