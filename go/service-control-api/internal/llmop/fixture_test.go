package llmop

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
	"time"
)

// NewPlanner is intentionally test-only. Production integrations must use
// newSafeguardedPlanner so the natural-language review cannot be bypassed.
func NewPlanner(client CompletionClient, normalizer Normalizer) Planner {
	return newProposalPlanner(client, normalizer)
}

func TestGoldenFixtureProducesSafeguardedHandoff(t *testing.T) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve fixture test source path")
	}
	repositoryRoot := filepath.Clean(filepath.Join(filepath.Dir(currentFile), "../../../.."))
	requestContent := mustReadFixture(
		t,
		filepath.Join(
			repositoryRoot,
			"examples",
			"llm-op",
			"fresh-latency-request.json",
		),
	)
	proposalContent := mustReadFixture(
		t,
		filepath.Join(
			repositoryRoot,
			"examples",
			"llm-op",
			"fresh-latency-qwen-proposal.json",
		),
	)
	safeguardContent := mustReadFixture(
		t,
		filepath.Join(
			repositoryRoot,
			"examples",
			"llm-op",
			"fresh-latency-safeguard-review.json",
		),
	)
	expectedRequestContent := mustReadFixture(
		t,
		filepath.Join(
			repositoryRoot,
			"examples",
			"llm-op",
			"fresh-latency-appdeploy-request.expected.json",
		),
	)

	var request Request
	if err := json.Unmarshal(requestContent, &request); err != nil {
		t.Fatalf("decode request fixture: %v", err)
	}
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	safeguardClient := &capturingCompletionClient{content: string(safeguardContent)}
	proposalClient := &capturingCompletionClient{content: string(proposalContent)}

	result, err := newSafeguardedPlanner(
		safeguardClient,
		proposalClient,
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("golden fixture should produce a handoff: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
	if safeguardClient.calls != 1 || proposalClient.calls != 1 {
		t.Fatalf("expected both fixture stages once, got %d/%d", safeguardClient.calls, proposalClient.calls)
	}
	if result.Evidence.SafeguardReview == nil ||
		result.Evidence.SafeguardReview.Decision != SafeguardDecisionAllow {
		t.Fatalf("golden fixture did not preserve safeguard evidence: %#v", result.Evidence.SafeguardReview)
	}
	if result.Manifest == nil {
		t.Fatal("expected fixture proposal to map to a manifest")
	}
	if result.Manifest.Spec.AppVersionID != request.Application.AppVersionID {
		t.Fatal("non-LLM app_version_id was not injected into the manifest")
	}
	if result.Manifest.Spec.Resources.GPU != "1" {
		t.Fatalf("expected one GPU, got %q", result.Manifest.Spec.Resources.GPU)
	}
	if !result.Evidence.Input.ResourceSnapshotIncluded {
		t.Fatal("resource snapshot usage was not recorded")
	}
	if result.Evidence.Input.RedactedValues != 1 {
		t.Fatalf("expected one redacted fixture value, got %d", result.Evidence.Input.RedactedValues)
	}
	if result.Handoff.NextEndpoint != "/api/v1/deployments" {
		t.Fatalf("unexpected handoff endpoint: %q", result.Handoff.NextEndpoint)
	}
	if result.Handoff.PreparedRequest == nil {
		t.Fatal("expected an exact AppDeploy request payload")
	}
	if !reflect.DeepEqual(result.Handoff.PreparedRequest.Manifest, *result.Manifest) {
		t.Fatal("prepared AppDeploy request must contain the approved manifest unchanged")
	}
	actualRequestContent, err := json.Marshal(result.Handoff.PreparedRequest)
	if err != nil {
		t.Fatalf("encode prepared AppDeploy request: %v", err)
	}
	actualRequestJSON := decodeFixtureJSON(t, actualRequestContent)
	expectedRequestJSON := decodeFixtureJSON(t, expectedRequestContent)
	if !reflect.DeepEqual(actualRequestJSON, expectedRequestJSON) {
		t.Fatalf(
			"prepared AppDeploy request did not match the exact golden shape: %s",
			actualRequestContent,
		)
	}
}

func decodeFixtureJSON(t *testing.T, content []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode fixture JSON: %v", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			t.Fatal("fixture JSON must contain exactly one value")
		}
		t.Fatalf("decode trailing fixture JSON: %v", err)
	}
	return value
}

func mustReadFixture(t *testing.T, path string) []byte {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture %s: %v", path, err)
	}
	return content
}
