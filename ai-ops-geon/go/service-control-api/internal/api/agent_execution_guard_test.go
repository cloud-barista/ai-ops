package api

import "testing"

func TestValidateAgentExecutionRequestRejectsUnregisteredCapabilityAndAction(t *testing.T) {
	agent := executionGuardTestAgent()

	wrongCapability := validateAgentExecutionRequest(agent, AgentExecutionRequest{
		Capability: "cost_review",
		Action:     "review_deployment_plan",
	})
	if wrongCapability.Valid || wrongCapability.Status != "rejected" {
		t.Fatalf("unregistered capability was not rejected: %#v", wrongCapability)
	}

	wrongAction := validateAgentExecutionRequest(agent, AgentExecutionRequest{
		Capability: "deployment_review",
		Action:     "restart_application",
	})
	if wrongAction.Valid || wrongAction.Status != "rejected" {
		t.Fatalf("unbounded action was not rejected: %#v", wrongAction)
	}
}

func TestValidateAgentExecutionRequestRejectsCredentialAndEndpointOverrideFields(t *testing.T) {
	agent := executionGuardTestAgent()
	for _, test := range []struct {
		name  string
		input map[string]any
	}{
		{name: "token", input: map[string]any{"token": "secret-value"}},
		{name: "nested password", input: map[string]any{"auth": map[string]any{"password": "secret-value"}}},
		{name: "private key", input: map[string]any{"private_key": "pem-value"}},
		{name: "credential", input: map[string]any{"credential": "cred-001"}},
		{name: "endpoint override", input: map[string]any{"endpoint": "https://unregistered.example.test"}},
		{name: "target url override", input: map[string]any{"target_url": "https://unregistered.example.test"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			decision := validateAgentExecutionRequest(agent, AgentExecutionRequest{
				Capability: "deployment_review",
				Action:     "review_deployment_plan",
				Input:      test.input,
			})
			if decision.Valid || decision.Status != "rejected" {
				t.Fatalf("unsafe input was not rejected: %#v", decision)
			}
		})
	}
}

func TestValidateAgentExecutionResultRejectsMismatchedRunAgentAndAction(t *testing.T) {
	agent := executionGuardTestAgent()
	request := AgentDispatchRequest{
		RunID:      "run-001",
		Agent:      agent.Name,
		Capability: "deployment_review",
		Action:     "review_deployment_plan",
	}
	tests := []struct {
		name   string
		result AgentExecutionResult
	}{
		{
			name: "run",
			result: AgentExecutionResult{
				RunID: "run-other", Agent: agent.Name, Status: "completed",
				Proposal: AgentProposal{Action: "review_deployment_plan"},
			},
		},
		{
			name: "agent",
			result: AgentExecutionResult{
				RunID: "run-001", Agent: "OtherAgent", Status: "completed",
				Proposal: AgentProposal{Action: "review_deployment_plan"},
			},
		},
		{
			name: "action",
			result: AgentExecutionResult{
				RunID: "run-001", Agent: agent.Name, Status: "completed",
				Proposal: AgentProposal{Action: "restart_application"},
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			decision := validateAgentExecutionResult(agent, request, test.result)
			if decision.Valid || decision.Status != "rejected" {
				t.Fatalf("mismatched result was not rejected: %#v", decision)
			}
		})
	}
}

func TestValidateAgentExecutionResultApprovesBoundedCompletedResult(t *testing.T) {
	agent := executionGuardTestAgent()
	request := AgentDispatchRequest{
		RunID:      "run-001",
		Agent:      agent.Name,
		Capability: "deployment_review",
		Action:     "review_deployment_plan",
	}
	result := AgentExecutionResult{
		RunID:  "run-001",
		Agent:  agent.Name,
		Status: "completed",
		Proposal: AgentProposal{
			Action:     "review_deployment_plan",
			Parameters: map[string]any{"decision": "approved"},
		},
		Result:           map[string]any{"review": "approved"},
		DomainValidation: "not_registered",
	}

	decision := validateAgentExecutionResult(agent, request, result)
	if !decision.Valid || decision.Status != "approved" {
		t.Fatalf("bounded completed result was rejected: %#v", decision)
	}
}

func executionGuardTestAgent() AgentProfile {
	return AgentProfile{
		Name:           "ExternalResearchAgent",
		Enabled:        true,
		Capabilities:   []string{"deployment_review"},
		BoundedActions: []string{"review_deployment_plan"},
		Source:         agentSourceRuntime,
	}
}
