package llmop

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type capturingCompletionClient struct {
	content    string
	err        error
	userPrompt string
	calls      int
}

func (client *capturingCompletionClient) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	_ string,
	userPrompt string,
) (llmclient.Completion, error) {
	client.calls++
	client.userPrompt = userPrompt
	if client.err != nil {
		return llmclient.Completion{
			Status:      "not_executed",
			Provider:    candidate.Provider,
			ActualModel: candidate.ActualModel,
			CandidateID: candidate.CandidateID,
		}, client.err
	}
	return llmclient.Completion{
		Status:      "executed",
		Content:     client.content,
		LatencyMS:   12,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, nil
}

func TestPrepareCreatesHandoffReadyManifest(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"create_deployment_manifest",
		"reason_code":"FRESH_LATENCY_SIGNAL_AND_EXPLICIT_GPU_REQUIREMENT",
		"reason":"Recent latency evidence and the explicit request support a GPU deployment plan.",
		"confidence":0.95,
		"accelerator":"nvidia",
		"resources":{"cpu":"4","memory":"16Gi","gpu":"1","storage":"20Gi"},
		"assumptions":[]
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	planner := NewPlanner(client, normalizer)
	request := testRequest(t, now)

	result, err := planner.Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("Prepare returned an unexpected error: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
	if result.Manifest == nil {
		t.Fatal("expected a manifest")
	}
	if result.Manifest.Spec.AppVersionID != "appver-llm-inference-v1" {
		t.Fatalf("unexpected app_version_id: %s", result.Manifest.Spec.AppVersionID)
	}
	if result.Manifest.Spec.RequestedBy != "ai-ops-geon-planner" {
		t.Fatalf("unexpected requested_by: %s", result.Manifest.Spec.RequestedBy)
	}
	if result.Manifest.Spec.TargetProfileID != "" {
		t.Fatalf("LLM_Op must not select a target: %s", result.Manifest.Spec.TargetProfileID)
	}
	if !result.Safeguard.Request.Valid || !result.Safeguard.Manifest.Valid {
		t.Fatal("expected both safeguards to approve the request")
	}
	if !result.Evidence.Input.ResourceSnapshotIncluded {
		t.Fatal("expected resource snapshot usage evidence")
	}
	if result.Evidence.Input.RedactedValues != 1 {
		t.Fatalf("expected one redacted value, got %d", result.Evidence.Input.RedactedValues)
	}
	if strings.Contains(client.userPrompt, "demo-secret") {
		t.Fatal("secret value reached the Qwen prompt")
	}
	if !strings.Contains(client.userPrompt, "[REDACTED]") {
		t.Fatal("expected redaction marker in the Qwen prompt")
	}
	for _, trustedIdentifier := range []string{
		"appver-llm-inference-v1",
		"dep-observed-001",
		"target-gpu-ready",
	} {
		if strings.Contains(client.userPrompt, trustedIdentifier) {
			t.Fatalf("trusted identifier reached the bounded Qwen prompt: %s", trustedIdentifier)
		}
	}
	if strings.Contains(client.userPrompt, `"deployments"`) ||
		strings.Contains(client.userPrompt, `"status":"degraded"`) {
		t.Fatal("fleet-wide monitoring aggregate reached a deployment-scoped prompt")
	}
	if result.Handoff.SubmissionMode != "not_submitted" {
		t.Fatalf("unexpected submission mode: %s", result.Handoff.SubmissionMode)
	}
	if result.Handoff.NextEndpoint != "/api/v1/deployments" {
		t.Fatalf("unexpected handoff endpoint: %s", result.Handoff.NextEndpoint)
	}
	if result.Handoff.PreparedRequest == nil {
		t.Fatal("expected an exact AppDeploy request payload")
	}
	result.Manifest.Spec.Resources.CPU = "8"
	if result.Handoff.PreparedRequest.Manifest.Spec.Resources.CPU != "4" {
		t.Fatal("prepared AppDeploy request must be an immutable copy of the approved manifest")
	}
}

func TestPrepareRejectsAllUntypedManifestParametersBeforeCompletion(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.Parameters = map[string]any{"safe_mode": "fixture"}
	client := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil || result.Status != StatusRequestRejected {
		t.Fatalf("expected untyped parameter rejection, got %s, %v", result.Status, err)
	}
	if client.calls != 0 {
		t.Fatalf("Qwen must not be called for untyped parameters, got %d calls", client.calls)
	}
}

func TestCloneRequestDeepCopiesMutableInputs(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.Parameters = map[string]any{
		"labels": map[string]any{"tier": "demo"},
	}
	request.Application.PlanningConstraints = &PlanningConstraints{
		SourceProfileID:        "profile-snapshot-001",
		SourceRecommendationID: "recommendation-snapshot-001",
		RecommendationFeasible: true,
		CPUCoresMin:            4,
		MemoryMiBMin:           16 * 1024,
		GPUCountMin:            1,
		StorageGiBMin:          20,
		Accelerator:            "nvidia",
	}

	snapshot, err := cloneRequest(request)
	if err != nil {
		t.Fatalf("clone request: %v", err)
	}
	request.Application.Parameters["labels"].(map[string]any)["tier"] = "mutated"
	request.Application.PlanningConstraints.CPUCoresMin = 99
	request.OperationContext.ResourceSnapshot.Targets[0].GPUAvailable = false

	labels := snapshot.Application.Parameters["labels"].(map[string]any)
	if labels["tier"] != "demo" ||
		snapshot.Application.PlanningConstraints.CPUCoresMin != 4 ||
		!snapshot.OperationContext.ResourceSnapshot.Targets[0].GPUAvailable {
		t.Fatal("request snapshot retained mutable aliases to caller-owned input")
	}
}

func TestCloneRequestRejectsOversizedRawObservationBeforeMarshal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.DeploymentLogs.Items[0].Message = strings.Repeat(
		"x",
		maxRawObservationFieldBytes+1,
	)

	if _, err := cloneRequest(request); err == nil {
		t.Fatal("oversized raw observation must fail before the request snapshot marshal")
	}
}

func TestPrepareRejectsUnsupportedSubmissionMode(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Policy.Mode = "approved_submit"
	request.Policy.ApprovalReference = "unverified-approval"
	client := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil {
		t.Fatal("expected unsupported submission mode to be rejected")
	}
	if result.Status != StatusRequestRejected {
		t.Fatalf("expected %s, got %s", StatusRequestRejected, result.Status)
	}
	if client.calls != 0 {
		t.Fatalf("Qwen must not be called for a rejected request, got %d calls", client.calls)
	}
}

func TestPrepareRejectsCredentialValueInTrustedParameters(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.Parameters = map[string]any{
		"note": "Authorization: Bearer parameter-secret",
	}
	client := &capturingCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil {
		t.Fatal("expected credential-like parameter value rejection")
	}
	if result.Status != StatusRequestRejected {
		t.Fatalf("expected %s, got %s", StatusRequestRejected, result.Status)
	}
	if client.calls != 0 {
		t.Fatalf("Qwen must not be called for secret-bearing parameters, got %d calls", client.calls)
	}
}

func TestPreparePreservesModelUnavailable(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{err: errors.New("Qwen endpoint unavailable")}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected model failure")
	}
	if result.Status != StatusModelUnavailable {
		t.Fatalf("expected %s, got %s", StatusModelUnavailable, result.Status)
	}
	if result.Manifest != nil {
		t.Fatal("model failure must not create a fallback manifest")
	}
}

func TestPrepareRejectsUnknownProposalFields(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"create_deployment_manifest",
		"reason_code":"UNSAFE_TARGET_SELECTION",
		"reason":"Attempted target selection.",
		"confidence":0.8,
		"accelerator":"nvidia",
		"resources":{"cpu":"4","memory":"16Gi","gpu":"1","storage":"20Gi"},
		"assumptions":[],
		"target_profile_id":"model-invented-target"
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected unknown proposal field rejection")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
	if result.Manifest != nil {
		t.Fatal("rejected proposal must not create a manifest")
	}
}

func TestPrepareReturnsClarificationWithoutManifest(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &capturingCompletionClient{content: `{
		"action":"request_clarification",
		"reason_code":"MISSING_RESOURCE_REQUIREMENTS",
		"reason":"The requested resource envelope is not specific enough.",
		"confidence":0.7,
		"assumptions":[]
	}`}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err != nil {
		t.Fatalf("clarification is a valid bounded result: %v", err)
	}
	if result.Status != StatusClarificationNeeded {
		t.Fatalf("expected %s, got %s", StatusClarificationNeeded, result.Status)
	}
	if result.Manifest != nil {
		t.Fatal("clarification result must not include a manifest")
	}
}

func testRequest(t *testing.T, now time.Time) Request {
	t.Helper()
	return Request{
		APIVersion:    APIVersion,
		RequestID:     "req-llmop-demo-001",
		CorrelationID: "corr-llmop-demo-001",
		TraceID:       "trace-llmop-demo-001",
		CandidateID:   "qwen3.5-ops-planner",
		RequestedBy:   "ai-ops-geon-planner",
		Application: Application{
			AppVersionID: "appver-llm-inference-v1",
			DeploymentID: "dep-observed-001",
			UserRequest:  "지연 경보가 난 추론 서비스를 NVIDIA GPU 1개, CPU 4개, 메모리 16Gi, 저장소 20Gi 기준으로 재배포 계획만 작성해줘. 아직 실행하지 마.",
		},
		OperationContext: OperationContext{
			ResourceSnapshot: &ResourceSnapshot{
				Source:     "appdeploy-monitoring",
				ObservedAt: now.Add(-5 * time.Minute),
				Targets: []appdeploy.RuntimeHealthSnapshot{{
					TargetProfileID: "target-gpu-ready",
					Status:          "available",
					RuntimeHealth:   "ok",
					CPUAvailable:    true,
					MemoryAvailable: true,
					GPUAvailable:    true,
					StorageAvailable: true,
					LastCheckedAt:   now.Add(-5 * time.Minute),
				}},
			},
			MonitoringSummary: &MonitoringObservation{
				Source:     "appdeploy-monitoring",
				ObservedAt: now.Add(-4 * time.Minute),
				Summary: appdeploy.MonitoringSummaryResponse{
					GeneratedAt: now.Add(-4 * time.Minute),
					Status:      "degraded",
					Alarms: []appdeploy.DeploymentAlarmSummary{{
						Severity:           "warning",
						ErrorCode:          "LATENCY_SLO",
						LatestDeploymentID: "dep-observed-001",
						LatestMessage:      "latency p95 threshold exceeded",
						LatestAt:           now.Add(-4 * time.Minute),
						Retryable:          true,
					}},
				},
			},
			DeploymentLogs: &LogObservation{
				Source:     "appdeploy-logs",
				ObservedAt: now.Add(-3 * time.Minute),
				Items: []appdeploy.DeploymentLog{{
					Timestamp:    now.Add(-3 * time.Minute).Format(time.RFC3339),
					Level:        "WARN",
					DeploymentID: "dep-observed-001",
					Component:    "runtime",
					Stage:        "RUNNING",
					Message:      "latency p95 threshold exceeded; Authorization: Bearer demo-secret",
					ErrorCode:    "LATENCY_SLO",
				}},
			},
			MetricsSummary: &MetricsObservation{
				Source:        "appdeploy-metrics",
				ObservedAt:    now.Add(-2 * time.Minute),
				DeploymentID:  "dep-observed-001",
				LatencyP95MS:  950,
				ThroughputRPS: 12,
				ErrorRate:     0.02,
				SampleCount:   100,
			},
		},
		Policy: RequestPolicy{Mode: ModePrepareOnly},
	}
}

func testCandidate() llmclient.Candidate {
	return llmclient.Candidate{
		CandidateID: "qwen3.5-ops-planner",
		Provider:    "fixture-openai-compatible",
		ActualModel: "qwen3.5:4b",
		Enabled:     true,
		JSONMode:    true,
	}
}

func testGuardPolicy() plannerguard.Policy {
	return plannerguard.Policy{
		Version:           "v1",
		MaxRequestLength:  8000,
		AllowedRequesters: []string{"ai-ops-geon-planner"},
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
}

func mustTime(t *testing.T, value string) time.Time {
	t.Helper()
	result, err := time.Parse(time.RFC3339, value)
	if err != nil {
		t.Fatalf("parse time: %v", err)
	}
	return result
}
