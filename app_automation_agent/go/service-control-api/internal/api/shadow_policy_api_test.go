package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

func TestShadowPolicyPayloadRejectsDesiredShapeWithoutObservedEvidence(t *testing.T) {
	flow := agentcontrol.Flow{
		ApplicationContext: &agentcontrol.ApplicationContextEnvelope{},
		ResourceRecommendation: &agentcontrol.ResourceRecommendationEnvelope{
			Data: agentcontrol.ResourceRecommendationData{
				ResourceRecommendation: agentcontrol.ResourceRecommendation{
					Candidates: []agentcontrol.ResourceCandidate{{
						CandidateID: "node-a",
						Feasible:    true,
						DesiredInfrastructure: agentcontrol.DesiredInfrastructure{
							CPUCoresPerNode:  8,
							MemoryMiBPerNode: 16384,
						},
						Scores: agentcontrol.ResourceScores{CostEfficiency: 0.8},
					}},
				},
			},
		},
	}
	for _, phase := range []string{"planning", "operation"} {
		payload, err := shadowPolicyPayload(flow, phase)
		if payload != nil || !errors.Is(err, errShadowPolicyEvidenceUnavailable) {
			t.Fatalf("shadowPolicyPayload(%q) = (%#v, %v), want no payload and unavailable evidence", phase, payload, err)
		}
	}
}

func TestFocusedPlanningAbstainsWithoutSendingProxyFeatures(t *testing.T) {
	var calls atomic.Int32
	policy := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		calls.Add(1)
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"proposal":{"action":"DEPLOY"}}`))
	}))
	defer policy.Close()
	t.Setenv(shadowPolicyModeEnv, "shadow")
	t.Setenv(shadowPolicyURLEnv, policy.URL)

	server := NewFocusedServer(NewServerConfig())
	request := focusedPlanningRequest()
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(encoded))
	if response.Code != http.StatusCreated {
		t.Fatalf("planning status = %d: %s", response.Code, response.Body.String())
	}
	var flow agentcontrol.Flow
	if err := json.Unmarshal(response.Body.Bytes(), &flow); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 0 {
		t.Fatalf("policy service was called %d times without observed input provenance", calls.Load())
	}
	if flow.Decision == nil || flow.Decision.Action != agentcontrol.ActionDeploy || flow.DeploymentPlan == nil {
		t.Fatalf("deterministic deployment decision changed: %#v", flow)
	}
	if len(flow.ShadowPolicyAssessments) != 1 {
		t.Fatalf("shadow assessments = %#v, want one abstention", flow.ShadowPolicyAssessments)
	}
	assessment := flow.ShadowPolicyAssessments[0]
	if assessment.ProposedAction != "ABSTAIN" || assessment.RuleAction != agentcontrol.ActionDeploy ||
		len(assessment.Warnings) == 0 || assessment.Warnings[0] != "policy_input_missing_provenance" {
		t.Fatalf("shadow assessment = %#v, want provenance abstention and unchanged rule action", assessment)
	}
}

func TestCommonResourceLabelsKeepsOnlySharedConditions(t *testing.T) {
	labels := commonResourceLabels([]ResourceOpsRankedResource{
		{Resource: ResourceOpsProfile{Labels: map[string]string{"accelerator": "nvidia", "zone": "a"}}},
		{Resource: ResourceOpsProfile{Labels: map[string]string{"accelerator": "nvidia", "zone": "b"}}},
	})
	want := map[string]string{"accelerator": "nvidia"}
	if !reflect.DeepEqual(labels, want) {
		t.Fatalf("commonResourceLabels() = %#v, want %#v", labels, want)
	}
}

func TestCommonResourceLabelsDropsMissingEmptyValuedKey(t *testing.T) {
	labels := commonResourceLabels([]ResourceOpsRankedResource{
		{Resource: ResourceOpsProfile{Labels: map[string]string{"zone": ""}}},
		{Resource: ResourceOpsProfile{Labels: map[string]string{}}},
	})
	if len(labels) != 0 {
		t.Fatalf("commonResourceLabels() = %#v, want no shared label", labels)
	}
}

func TestInvokeShadowPolicyHonorsTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		time.Sleep(50 * time.Millisecond)
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte("{\"proposal\":{\"action\":\"KEEP\"}}"))
	}))
	defer server.Close()
	t.Setenv(shadowPolicyTimeoutEnv, "0.001")

	if _, err := invokeShadowPolicy(context.Background(), server.URL, map[string]any{}); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("invokeShadowPolicy() error = %v, want deadline exceeded", err)
	}
}
