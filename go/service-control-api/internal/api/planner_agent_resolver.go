package api

import (
	"fmt"
	"strings"

	"kyunghee-aiops/service-control-api/internal/controlrun"
)

const (
	capabilityDeploymentManifestPlanning = "deployment_manifest_planning"
	actionGenerateDeploymentManifest     = "generate_deployment_manifest"
	actionSubmitDeploymentManifest       = "submit_deployment_manifest"
)

func resolvePlannerAgent(registry AgentRegistry, requestedName string, action string) (controlrun.AgentSelection, error) {
	requestedName = strings.TrimSpace(requestedName)
	if requestedName != "" {
		for _, agent := range registry.Agents {
			if agent.Name == requestedName {
				return validatePlannerAgent(agent, action)
			}
		}
		return controlrun.AgentSelection{}, fmt.Errorf("Planner Agent is not registered: %s", requestedName)
	}

	eligible := make([]controlrun.AgentSelection, 0, 1)
	for _, agent := range registry.Agents {
		selection, err := validatePlannerAgent(agent, action)
		if err == nil {
			eligible = append(eligible, selection)
		}
	}
	if len(eligible) == 1 {
		return eligible[0], nil
	}
	if len(eligible) > 1 {
		return controlrun.AgentSelection{}, fmt.Errorf(
			"multiple enabled configuration Agents authorize capability %s and action %s; agent_name is required",
			capabilityDeploymentManifestPlanning,
			action,
		)
	}
	return controlrun.AgentSelection{}, fmt.Errorf(
		"no enabled configuration Agent authorizes capability %s and action %s",
		capabilityDeploymentManifestPlanning,
		action,
	)
}

func validatePlannerAgent(agent AgentProfile, action string) (controlrun.AgentSelection, error) {
	source := strings.TrimSpace(agent.Source)
	if source == "" {
		source = agentSourceConfiguration
	}
	if source != agentSourceConfiguration {
		return controlrun.AgentSelection{}, fmt.Errorf("Planner Agent must be an internal configuration Agent: %s", agent.Name)
	}
	if !agent.Enabled {
		return controlrun.AgentSelection{}, fmt.Errorf("Planner Agent is disabled: %s", agent.Name)
	}
	if !contains(agent.Capabilities, capabilityDeploymentManifestPlanning) {
		return controlrun.AgentSelection{}, fmt.Errorf(
			"Planner Agent %s does not provide capability %s",
			agent.Name,
			capabilityDeploymentManifestPlanning,
		)
	}
	if !contains(agent.BoundedActions, action) {
		return controlrun.AgentSelection{}, fmt.Errorf(
			"Planner Agent %s does not authorize action %s",
			agent.Name,
			action,
		)
	}
	return controlrun.AgentSelection{
		Name:       agent.Name,
		Capability: capabilityDeploymentManifestPlanning,
		Action:     action,
		Source:     source,
		Reason:     "Agent Registry authorizes the internal Manifest Planner capability and bounded action",
	}, nil
}
