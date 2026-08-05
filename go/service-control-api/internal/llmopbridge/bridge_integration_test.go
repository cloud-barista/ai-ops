package llmopbridge

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type offlineBridgeCompletionClient struct {
	content string
	calls   int
}

func (client *offlineBridgeCompletionClient) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	_ string,
	_ string,
) (llmclient.Completion, error) {
	client.calls++
	return llmclient.Completion{
		Status:      "executed",
		Content:     client.content,
		LatencyMS:   1,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, nil
}

func TestOfflineCommonJSONToAppDeployPreparedRequest(t *testing.T) {
	projection, err := Project(localAnalyzerCatalogBridgeInput(t))
	if err != nil {
		t.Fatalf("project Common JSON chain: %v", err)
	}
	safeguardClient := &offlineBridgeCompletionClient{content: `{
		"decision":"allow_request",
		"reason_code":"COMMON_JSON_REQUEST_ALLOWED",
		"reason":"The request is bounded to prepare-only resource planning.",
		"confidence":0.98
	}`}
	proposalClient := &offlineBridgeCompletionClient{content: `{
		"action":"create_deployment_manifest",
		"reason_code":"COMMON_JSON_RESOURCE_PLAN_READY",
		"reason":"The recommended resources satisfy the trusted planning minima.",
		"confidence":0.95,
		"accelerator":"none",
		"resources":{"cpu":"4","memory":"8Gi","gpu":"0","storage":"100Gi"},
		"assumptions":[]
	}`}
	candidate := llmclient.Candidate{
		CandidateID: projection.Request.CandidateID,
		Provider:    "fixture-openai-compatible",
		ActualModel: "qwen-fixture-only",
		Enabled:     true,
		JSONMode:    true,
	}
	policy := plannerguard.Policy{
		Version:           "v1",
		MaxRequestLength:  8000,
		AllowedRequesters: []string{projection.Request.RequestedBy},
		ForbiddenRequestTerms: []string{
			"kubernetes",
			"container",
			"password",
			"api key",
			"private key",
		},
		ForbiddenParameterKeys: []string{
			"password",
			"token",
			"secret",
			"credential",
			"api_key",
			"private_key",
		},
	}

	result, err := llmop.NewSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		llmop.NewNormalizer(),
	).Prepare(
		context.Background(),
		candidate,
		policy,
		projection.Request,
	)
	if err != nil {
		t.Fatalf("prepare projected request: %v", err)
	}
	if safeguardClient.calls != 1 || proposalClient.calls != 1 {
		t.Fatalf(
			"expected one offline safeguard and proposal completion, got %d/%d",
			safeguardClient.calls,
			proposalClient.calls,
		)
	}
	if result.Status != llmop.StatusHandoffReady ||
		result.Manifest == nil ||
		result.Handoff.PreparedRequest == nil {
		t.Fatalf("expected a prepared AppDeploy request, got status %s", result.Status)
	}
	if !reflect.DeepEqual(result.Handoff.PreparedRequest.Manifest, *result.Manifest) {
		t.Fatal("prepared request did not preserve the safeguarded Manifest")
	}
	spec := result.Handoff.PreparedRequest.Manifest.Spec
	if spec.AppVersionID != "appver-registered-001" ||
		spec.TargetProfileID != "target-hint-001" ||
		spec.RequestedBy != "ai-ops-geon-planner" ||
		spec.Accelerator != "none" ||
		spec.Resources.CPU != "4" ||
		spec.Resources.Memory != "8Gi" ||
		spec.Resources.GPU != "0" ||
		spec.Resources.Storage != "100Gi" {
		t.Fatalf("unexpected exact AppDeploy handoff body: %#v", spec)
	}

	content, err := json.Marshal(result.Handoff.PreparedRequest)
	if err != nil {
		t.Fatalf("marshal prepared AppDeploy request: %v", err)
	}
	for _, forbidden := range []string{
		projection.Evidence.SourceProfileID,
		projection.Evidence.SourceRecommendationID,
		projection.Evidence.SourceSnapshotID,
		projection.Evidence.SelectedResourceCandidateID,
		"model-must-not-project",
		"artifact-must-not-project",
		"resource-hint-must-not-project",
	} {
		if strings.Contains(string(content), forbidden) {
			t.Fatalf("Common JSON integration-only value reached AppDeploy body: %s", forbidden)
		}
	}
}
