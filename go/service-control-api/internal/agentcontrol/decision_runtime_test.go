package agentcontrol

import (
	"context"
	"errors"
	"testing"
)

type stubDecisionAgentRuntime struct {
	result  DecisionAgentResult
	err     error
	request DecisionAgentRequest
}

func (runtime *stubDecisionAgentRuntime) Decide(
	_ context.Context,
	request DecisionAgentRequest,
) (DecisionAgentResult, error) {
	runtime.request = request
	return runtime.result, runtime.err
}

func TestServiceUsesSelectedDecisionAgent(t *testing.T) {
	runtime := &stubDecisionAgentRuntime{result: approvedRuntimeDecision("candidate-001")}
	service := NewServiceWithDecisionRuntime(nil, nil, runtime)

	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	flow, err := service.ReceiveResourceRecommendationForAgent(
		context.Background(),
		validResourceRecommendationEnvelope(),
		"RuntimeDeploymentAgent",
		"run-001",
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}

	if runtime.request.RequestedAgent != "RuntimeDeploymentAgent" || runtime.request.RunID != "run-001" {
		t.Fatalf("decision request = %#v", runtime.request)
	}
	if flow.AgentExecution == nil || flow.AgentExecution.AgentName != "RuntimeDeploymentAgent" {
		t.Fatalf("Agent execution = %#v", flow.AgentExecution)
	}
	if flow.State != StateDecisionApproved || flow.Decision == nil || flow.Decision.Action != ActionDeploy {
		t.Fatalf("flow decision = %#v, state = %s", flow.Decision, flow.State)
	}
	if flow.DesiredDeploymentSpec == nil || flow.DeploymentRequest == nil {
		t.Fatal("approved Runtime Agent decision must create a DesiredDeploymentSpec and deployment request")
	}
}

func TestServiceRejectsUnknownDecisionCandidate(t *testing.T) {
	runtime := &stubDecisionAgentRuntime{result: approvedRuntimeDecision("missing-candidate")}
	service := NewServiceWithDecisionRuntime(nil, nil, runtime)

	_, _ = service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope())
	flow, err := service.ReceiveResourceRecommendationForAgent(
		context.Background(),
		validResourceRecommendationEnvelope(),
		"RuntimeDeploymentAgent",
		"run-002",
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}

	if flow.State != StateRetryRequired {
		t.Fatalf("state = %q, want %q", flow.State, StateRetryRequired)
	}
	if flow.Guard == nil || flow.Guard.Status != GuardRetryRequired {
		t.Fatalf("guard = %#v", flow.Guard)
	}
	if flow.DesiredDeploymentSpec != nil || flow.DeploymentRequest != nil {
		t.Fatal("unknown decision candidate must not create deployment output")
	}
}

func TestServiceRecordsDecisionAgentExecutionFailure(t *testing.T) {
	runtime := &stubDecisionAgentRuntime{
		result: DecisionAgentResult{
			AgentName:    "RuntimeDeploymentAgent",
			Source:       "runtime",
			Status:       "failed",
			RequestGuard: approvedDecisionGuard("request approved"),
		},
		err: errors.New("runtime endpoint unavailable"),
	}
	service := NewServiceWithDecisionRuntime(nil, nil, runtime)

	_, _ = service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope())
	flow, err := service.ReceiveResourceRecommendationForAgent(
		context.Background(),
		validResourceRecommendationEnvelope(),
		"RuntimeDeploymentAgent",
		"run-003",
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}

	if flow.State != StateAgentExecutionFailed {
		t.Fatalf("state = %q, want %q", flow.State, StateAgentExecutionFailed)
	}
	if flow.AgentExecution == nil || flow.AgentExecution.Status != "failed" {
		t.Fatalf("Agent execution = %#v", flow.AgentExecution)
	}
	if flow.DesiredDeploymentSpec != nil || flow.DeploymentRequest != nil {
		t.Fatal("failed Runtime Agent must not create deployment output")
	}
}

func TestDecisionAgentGuardRejectsOutOfRangeConfidence(t *testing.T) {
	profile := validApplicationContextEnvelope().Data.ApplicationProfile
	recommendation := validResourceRecommendationEnvelope().Data.ResourceRecommendation
	result := approvedRuntimeDecision("candidate-001")
	result.Decision.Confidence = 1.1

	guard := validateDecisionAgentResult(profile, recommendation, result)
	if guard.Status != GuardRejected {
		t.Fatalf("guard = %#v", guard)
	}
}

func approvedRuntimeDecision(candidateID string) DecisionAgentResult {
	return DecisionAgentResult{
		AgentName:    "RuntimeDeploymentAgent",
		Source:       "runtime",
		Status:       "completed",
		RequestGuard: approvedDecisionGuard("request approved"),
		ResultGuard:  approvedDecisionGuard("result approved"),
		Decision: AutomationDecision{
			Action:              ActionDeploy,
			Reason:              "The Runtime Agent selected a feasible resource candidate.",
			ReasoningMode:       "runtime_agent",
			Confidence:          0.9,
			SelectedCandidateID: candidateID,
		},
	}
}

func approvedDecisionGuard(reason string) GuardResult {
	return GuardResult{
		Status: GuardApproved,
		Checks: []GuardCheck{{Name: "agent_contract", Passed: true, Reason: reason}},
	}
}
