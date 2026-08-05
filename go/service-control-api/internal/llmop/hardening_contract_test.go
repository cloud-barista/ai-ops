package llmop

import (
	"context"
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type hardeningCompletionClient struct {
	content    string
	completion *llmclient.Completion
	userPrompt string
	calls      int
}

func (client *hardeningCompletionClient) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	_ string,
	userPrompt string,
) (llmclient.Completion, error) {
	client.calls++
	client.userPrompt = userPrompt
	if client.completion != nil {
		return *client.completion, nil
	}
	return llmclient.Completion{
		Status:      "executed",
		Content:     client.content,
		LatencyMS:   12,
		Provider:    candidate.Provider,
		CandidateID: candidate.CandidateID,
		ActualModel: candidate.ActualModel,
	}, nil
}

func TestHardeningValidateRequestRejectsParameterEnvelopeLimits(t *testing.T) {
	tests := []struct {
		name       string
		parameters func() map[string]any
	}{
		{
			name: "bytes",
			parameters: func() map[string]any {
				return map[string]any{
					"notes": strings.Repeat("x", maxParameterBytes),
				}
			},
		},
		{
			name: "depth",
			parameters: func() map[string]any {
				var value any = "leaf"
				for depth := 0; depth < maxParameterDepth+4; depth++ {
					value = map[string]any{"layer": value}
				}
				return value.(map[string]any)
			},
		},
		{
			name: "nodes",
			parameters: func() map[string]any {
				items := make([]any, maxParameterNodes+32)
				for index := range items {
					items[index] = index
				}
				return map[string]any{"items": items}
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := guardedRequest()
			request.Application.Parameters = test.parameters()

			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
			if decision.Valid {
				t.Fatal("expected the parameter envelope limit to reject the request")
			}
			assertFailedGuardCheck(t, decision, "bounded_parameters")
		})
	}
}

func TestHardeningValidateRequestRejectsOperationalParameterKeysAndValues(t *testing.T) {
	tests := []struct {
		name       string
		parameters map[string]any
		check      string
	}{
		{
			name:       "runtime adapter key",
			parameters: map[string]any{"runtime_adapter": "python"},
			check:      "responsibility_parameter_keys",
		},
		{
			name: "nested target vm key",
			parameters: map[string]any{
				"placement": map[string]any{"targetVm": "vm-a"},
			},
			check: "responsibility_parameter_keys",
		},
		{
			name:       "endpoint value",
			parameters: map[string]any{"note": "send to https://planner.invalid/v1"},
			check:      "responsibility_parameter_values",
		},
		{
			name:       "command value",
			parameters: map[string]any{"note": "run kubectl apply for this plan"},
			check:      "responsibility_parameter_values",
		},
		{
			name:       "destructive shell value",
			parameters: map[string]any{"note": "rm -rf /tmp/workload"},
			check:      "responsibility_parameter_values",
		},
		{
			name:       "provider value",
			parameters: map[string]any{"note": "place this on AWS"},
			check:      "responsibility_parameter_values",
		},
		{
			name:       "korean responsibility value",
			parameters: map[string]any{"note": "쿠버네티스에 배포"},
			check:      "responsibility_parameter_values",
		},
		{
			name:       "url key",
			parameters: map[string]any{"url": "ftp://host.invalid/path"},
			check:      "responsibility_parameter_keys",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := guardedRequest()
			request.Application.Parameters = test.parameters

			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
			if decision.Valid {
				t.Fatal("expected operational parameters to be rejected")
			}
			assertFailedGuardCheck(t, decision, test.check)
		})
	}
}

func TestHardeningValidateRequestRedactsCanonicalTrustedIDsBeforeScopeTextCheck(t *testing.T) {
	request := guardedRequest()
	request.Application.AppVersionID = "app-version-id-123"
	request.Application.DeploymentID = "deployment-id-123"
	request.Application.TargetProfileID = "target-profile-id-123"
	request.Application.UserRequest = "Prepare a GPU plan for app-version-id-123 " +
		"deployment-id-123 with hint target-profile-id-123."

	decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
	if !decision.Valid {
		t.Fatalf("trusted identifier references should be redacted before scope text checks: %s", decision.Reason)
	}
}

func TestHardeningValidateRequestRejectsShortStrongIdentifier(t *testing.T) {
	request := guardedRequest()
	request.Application.AppVersionID = "abc"

	decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
	if decision.Valid {
		t.Fatal("expected a short strong trusted identifier to be rejected")
	}
	assertFailedGuardCheck(t, decision, "bounded_identifiers")
}

func TestHardeningRequestRejectionDoesNotExposeParameterInput(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	privateKey := "targetVmSelector"
	privateValue := "https://tenant-private.invalid/admin"
	request.Application.Parameters = map[string]any{privateKey: privateValue}
	client := &hardeningCompletionClient{}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil {
		t.Fatal("expected operational parameter rejection")
	}
	if result.Status != StatusRequestRejected {
		t.Fatalf("expected %s, got %s", StatusRequestRejected, result.Status)
	}
	if client.calls != 0 {
		t.Fatalf("Qwen must not be called for rejected parameters, got %d calls", client.calls)
	}

	publicReasons := []string{
		result.Decision.Reason,
		result.Safeguard.Request.Reason,
		err.Error(),
	}
	for _, check := range result.Safeguard.Request.Checks {
		publicReasons = append(publicReasons, check.Reason)
	}
	for _, publicReason := range publicReasons {
		for _, privateInput := range []string{privateKey, privateValue} {
			if strings.Contains(publicReason, privateInput) {
				t.Fatalf("public reason exposed parameter input %q", privateInput)
			}
		}
	}
}

func TestHardeningValidateRequestRejectsPerDeploymentObservationsWithoutTrustedScope(t *testing.T) {
	tests := []struct {
		name    string
		context OperationContext
	}{
		{
			name: "metrics",
			context: OperationContext{MetricsSummary: &MetricsObservation{
				DeploymentID: "dep-untrusted",
			}},
		},
		{
			name: "logs",
			context: OperationContext{DeploymentLogs: &LogObservation{
				Items: []appdeploy.DeploymentLog{{DeploymentID: "dep-untrusted"}},
			}},
		},
		{
			name: "alarm",
			context: OperationContext{MonitoringSummary: &MonitoringObservation{
				Summary: appdeploy.MonitoringSummaryResponse{
					Alarms: []appdeploy.DeploymentAlarmSummary{{
						LatestDeploymentID: "dep-untrusted",
					}},
				},
			}},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := guardedRequest()
			request.Application.DeploymentID = ""
			request.OperationContext = test.context

			decision := ValidateRequest(request, guardPolicyWithoutSensitiveKeys())
			if decision.Valid {
				t.Fatal("expected per-deployment evidence without trusted scope to be rejected")
			}
			assertFailedGuardCheck(t, decision, "observation_scope")
		})
	}
}

func TestHardeningNormalizerRejectsURLObservationSources(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	urlSource := "https://observability.invalid/v1"
	tests := []struct {
		name    string
		context OperationContext
	}{
		{
			name: "resource snapshot",
			context: OperationContext{ResourceSnapshot: &ResourceSnapshot{
				Source: urlSource, ObservedAt: now,
			}},
		},
		{
			name: "monitoring summary",
			context: OperationContext{MonitoringSummary: &MonitoringObservation{
				Source: urlSource, ObservedAt: now,
				Summary: appdeploy.MonitoringSummaryResponse{GeneratedAt: now},
			}},
		},
		{
			name: "deployment logs",
			context: OperationContext{DeploymentLogs: &LogObservation{
				Source: urlSource, ObservedAt: now,
			}},
		},
		{
			name: "metrics summary",
			context: OperationContext{MetricsSummary: &MetricsObservation{
				Source: urlSource, ObservedAt: now,
			}},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			if _, err := normalizer.Normalize(test.context); err == nil {
				t.Fatal("expected a URL-shaped observation source to be rejected")
			}
		})
	}
}

func TestHardeningNormalizerRedactsQuotedJSONSecrets(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	message := `{"api_key":"json-secret","authorization":"Bearer auth-secret","safe":"visible"} token='quoted-secret'`

	result, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now,
			Items: []appdeploy.DeploymentLog{{
				Timestamp: now.Format(time.RFC3339Nano),
				Message:   message,
			}},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.DeploymentLogs == nil || len(result.DeploymentLogs.Items) != 1 {
		t.Fatal("expected the redacted log to remain included")
	}
	redacted := result.DeploymentLogs.Items[0].Message
	for _, secret := range []string{"json-secret", "auth-secret", "quoted-secret"} {
		if strings.Contains(redacted, secret) {
			t.Fatalf("quoted secret %q survived normalization", secret)
		}
	}
	if !strings.Contains(redacted, `"safe":"visible"`) {
		t.Fatal("normalization removed unrelated JSON content")
	}
	if result.RedactedValues < 3 {
		t.Fatalf("expected at least three redactions, got %d", result.RedactedValues)
	}
}

func TestHardeningNormalizerMarksAllDroppedLogsExcludedAndMixed(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now,
			Items: []appdeploy.DeploymentLog{
				{Timestamp: "not-a-timestamp", Message: "malformed"},
				{
					Timestamp: now.Add(-30 * time.Minute).Format(time.RFC3339Nano),
					Message:   "stale",
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.DeploymentLogs != nil {
		t.Fatal("an all-dropped log observation must not remain included")
	}
	if result.DroppedLogs != 2 {
		t.Fatalf("expected two dropped logs, got %d", result.DroppedLogs)
	}
	if result.ObservationStatus != "mixed" {
		t.Fatalf("expected mixed observation status, got %s", result.ObservationStatus)
	}
	if summary := inputSummary(Request{}, result); summary.LogsIncluded {
		t.Fatal("all-dropped logs must report logs_included=false")
	}
}

func TestHardeningNormalizerRejectsInvalidMetrics(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name   string
		mutate func(*MetricsObservation)
	}{
		{name: "nan latency", mutate: func(value *MetricsObservation) { value.LatencyP95MS = math.NaN() }},
		{name: "infinite latency", mutate: func(value *MetricsObservation) { value.LatencyP95MS = math.Inf(1) }},
		{name: "negative latency", mutate: func(value *MetricsObservation) { value.LatencyP95MS = -1 }},
		{name: "nan throughput", mutate: func(value *MetricsObservation) { value.ThroughputRPS = math.NaN() }},
		{name: "infinite throughput", mutate: func(value *MetricsObservation) { value.ThroughputRPS = math.Inf(1) }},
		{name: "negative throughput", mutate: func(value *MetricsObservation) { value.ThroughputRPS = -1 }},
		{name: "nan error rate", mutate: func(value *MetricsObservation) { value.ErrorRate = math.NaN() }},
		{name: "infinite error rate", mutate: func(value *MetricsObservation) { value.ErrorRate = math.Inf(1) }},
		{name: "negative error rate", mutate: func(value *MetricsObservation) { value.ErrorRate = -0.01 }},
		{name: "error rate above one", mutate: func(value *MetricsObservation) { value.ErrorRate = 1.01 }},
		{name: "negative sample count", mutate: func(value *MetricsObservation) { value.SampleCount = -1 }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			metrics := &MetricsObservation{
				Source:        "appdeploy-metrics",
				ObservedAt:    now,
				DeploymentID: "dep-observed-001",
				LatencyP95MS: 10,
				ThroughputRPS: 20,
				ErrorRate:     0.1,
				SampleCount:   10,
			}
			test.mutate(metrics)
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			if _, err := normalizer.Normalize(OperationContext{MetricsSummary: metrics}); err == nil {
				t.Fatal("expected invalid metrics to be rejected")
			}
		})
	}
}

func TestHardeningPlannerRemovesRepeatedTrustedIdentifiersFromPrompt(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.TargetProfileID = "target-hint-001"
	trustedIdentifiers := []string{
		request.RequestID,
		request.CorrelationID,
		request.TraceID,
		request.CandidateID,
		request.RequestedBy,
		request.Application.AppVersionID,
		request.Application.DeploymentID,
		request.Application.TargetProfileID,
	}
	identifierList := strings.Join(trustedIdentifiers, " ")
	request.Application.UserRequest = "Prepare a plan for CPU 4, GPU 1, memory 16Gi, and storage 20Gi. References: " +
		identifierList + " repeated: " + identifierList + " adjacent: " +
		request.RequestID + " " + request.RequestID + " " + request.RequestID
	request.OperationContext.ResourceSnapshot.Targets[0].TargetProfileID =
		request.Application.TargetProfileID
	request.OperationContext.MonitoringSummary.Summary.Alarms[0].LatestMessage +=
		" " + request.CorrelationID
	request.OperationContext.MonitoringSummary.Summary.Alarms[0].ErrorCode =
		request.TraceID
	request.OperationContext.DeploymentLogs.Items[0].Message +=
		" " + request.Application.AppVersionID
	request.OperationContext.DeploymentLogs.Items[0].Component = request.CandidateID
	client := &hardeningCompletionClient{
		content: hardeningCreateProposalJSON(t, hardeningResources(), 0.95),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
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
	for _, identifier := range trustedIdentifiers {
		if strings.Contains(strings.ToLower(client.userPrompt), strings.ToLower(identifier)) {
			t.Fatalf("trusted identifier reached the Qwen prompt: %s", identifier)
		}
	}
}

func TestHardeningPlannerDoesNotTreatShortGPUIdentifierAsReasonEcho(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.TargetProfileID = "gpu"
	request.Application.UserRequest = "Prepare a deployment plan with NVIDIA GPU 1, CPU 4, memory 16Gi, and storage 20Gi."
	request.OperationContext.ResourceSnapshot.Targets[0].TargetProfileID =
		request.Application.TargetProfileID
	client := &hardeningCompletionClient{
		content: hardeningCreateProposalJSON(t, hardeningResources(), 0.95),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("ordinary GPU wording must not be mistaken for a short trusted ID: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
	if !strings.Contains(strings.ToLower(client.userPrompt), "gpu") {
		t.Fatal("ordinary GPU wording was removed from the bounded prompt")
	}
}

func TestHardeningPlannerRejectsResourcesAboveCeilings(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name   string
		mutate func(*appdeploy.ResourceRequirements)
	}{
		{name: "cpu", mutate: func(value *appdeploy.ResourceRequirements) { value.CPU = "1000000" }},
		{name: "memory", mutate: func(value *appdeploy.ResourceRequirements) { value.Memory = "1000000Ti" }},
		{name: "gpu", mutate: func(value *appdeploy.ResourceRequirements) { value.GPU = "1000000" }},
		{name: "storage", mutate: func(value *appdeploy.ResourceRequirements) { value.Storage = "1000000Ti" }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			resources := hardeningResources()
			test.mutate(&resources)
			client := &hardeningCompletionClient{
				content: hardeningCreateProposalJSON(t, resources, 0.95),
			}
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			result, err := NewPlanner(client, normalizer).Prepare(
				context.Background(),
				testCandidate(),
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err == nil {
				t.Fatal("expected an over-ceiling resource proposal to be rejected")
			}
			if result.Status != StatusManifestRejected {
				t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
			}
			if result.Manifest != nil {
				t.Fatal("an over-ceiling proposal must not produce a manifest")
			}
		})
	}
}

func TestHardeningPlannerRejectsLowConfidenceCreateProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	client := &hardeningCompletionClient{
		content: hardeningCreateProposalJSON(t, hardeningResources(), 0.49),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		testRequest(t, now),
	)
	if err == nil {
		t.Fatal("expected a low-confidence create proposal to be rejected")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
}

func TestHardeningPlannerRejectsOversizedPromptBeforeCompletion(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	alarms := make([]appdeploy.DeploymentAlarmSummary, defaultMaxAlarms)
	for index := range alarms {
		alarms[index] = appdeploy.DeploymentAlarmSummary{
			Severity:           "warning",
			ErrorCode:          "LARGE_CONTEXT",
			Count:              1,
			LatestDeploymentID: request.Application.DeploymentID,
			LatestStage:        "RUNNING",
			LatestMessage:      strings.Repeat("A", defaultMaxLogRunes),
			LatestAt:           now.Add(-4 * time.Minute),
			Retryable:          true,
		}
	}
	request.OperationContext.MonitoringSummary = &MonitoringObservation{
		Source:     "appdeploy-monitoring",
		ObservedAt: now.Add(-4 * time.Minute),
		Summary: appdeploy.MonitoringSummaryResponse{
			GeneratedAt: now.Add(-4 * time.Minute),
			Status:      "degraded",
			Alarms:      alarms,
		},
	}
	logs := make([]appdeploy.DeploymentLog, defaultMaxLogs)
	for index := range logs {
		logs[index] = appdeploy.DeploymentLog{
			Timestamp:    now.Add(-3 * time.Minute).Format(time.RFC3339Nano),
			Level:        "WARN",
			DeploymentID: request.Application.DeploymentID,
			Component:    "planner",
			Stage:        "RUNNING",
			Message:      strings.Repeat("B", defaultMaxLogRunes),
			ErrorCode:    "LARGE_CONTEXT",
		}
	}
	request.OperationContext.DeploymentLogs = &LogObservation{
		Source:     "appdeploy-logs",
		ObservedAt: now.Add(-3 * time.Minute),
		Items:      logs,
	}
	client := &hardeningCompletionClient{
		content: hardeningCreateProposalJSON(t, hardeningResources(), 0.95),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err == nil {
		t.Fatal("expected an oversized prompt to be rejected")
	}
	if result.Status != StatusRequestRejected {
		t.Fatalf("expected %s, got %s", StatusRequestRejected, result.Status)
	}
	if client.calls != 0 {
		t.Fatalf("completion must not run for an oversized prompt, got %d calls", client.calls)
	}
	if result.Evidence.Input.ResourceSnapshotIncluded ||
		result.Evidence.Input.MonitoringIncluded ||
		result.Evidence.Input.LogsIncluded ||
		result.Evidence.Input.MetricsIncluded {
		t.Fatal("rejected prompt budget must not claim that observations were included")
	}
}

func TestHardeningPlannerRejectsInvalidCompletionEnvelope(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	candidate := testCandidate()
	validContent := hardeningCreateProposalJSON(t, hardeningResources(), 0.95)
	tests := []struct {
		name   string
		mutate func(*llmclient.Completion)
	}{
		{
			name: "proposal exceeds 64 KiB",
			mutate: func(completion *llmclient.Completion) {
				completion.Content = validContent + strings.Repeat(" ", (64<<10)+1)
			},
		},
		{
			name: "empty proposal",
			mutate: func(completion *llmclient.Completion) {
				completion.Content = " \n\t"
			},
		},
		{
			name: "negative latency",
			mutate: func(completion *llmclient.Completion) {
				completion.LatencyMS = -1
			},
		},
		{
			name: "invalid completion status",
			mutate: func(completion *llmclient.Completion) {
				completion.Status = "not_executed"
			},
		},
		{
			name: "missing candidate id",
			mutate: func(completion *llmclient.Completion) {
				completion.CandidateID = ""
			},
		},
		{
			name: "missing provider",
			mutate: func(completion *llmclient.Completion) {
				completion.Provider = ""
			},
		},
		{
			name: "missing actual model",
			mutate: func(completion *llmclient.Completion) {
				completion.ActualModel = ""
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			completion := llmclient.Completion{
				Status:      "executed",
				Content:     validContent,
				LatencyMS:   12,
				Provider:    candidate.Provider,
				CandidateID: candidate.CandidateID,
				ActualModel: candidate.ActualModel,
			}
			test.mutate(&completion)
			client := &hardeningCompletionClient{completion: &completion}
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			result, err := NewPlanner(client, normalizer).Prepare(
				context.Background(),
				candidate,
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err == nil {
				t.Fatal("expected the invalid completion envelope to be rejected")
			}
			if result.Status != StatusModelUnavailable {
				t.Fatalf("expected %s, got %s", StatusModelUnavailable, result.Status)
			}
			if result.Manifest != nil {
				t.Fatal("an invalid completion must not produce a manifest")
			}
		})
	}
}

func TestHardeningPlannerRejectsInvalidConfiguredCandidateMetadata(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name   string
		mutate func(*llmclient.Candidate)
	}{
		{
			name: "missing provider",
			mutate: func(candidate *llmclient.Candidate) {
				candidate.Provider = ""
			},
		},
		{
			name: "missing actual model",
			mutate: func(candidate *llmclient.Candidate) {
				candidate.ActualModel = ""
			},
		},
		{
			name: "json mode disabled",
			mutate: func(candidate *llmclient.Candidate) {
				candidate.JSONMode = false
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := testCandidate()
			test.mutate(&candidate)
			client := &hardeningCompletionClient{
				content: hardeningCreateProposalJSON(t, hardeningResources(), 0.95),
			}
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			result, err := NewPlanner(client, normalizer).Prepare(
				context.Background(),
				candidate,
				testGuardPolicy(),
				testRequest(t, now),
			)
			if err == nil {
				t.Fatal("expected invalid configured candidate metadata to be rejected")
			}
			if result.Status != StatusModelUnavailable {
				t.Fatalf("expected %s, got %s", StatusModelUnavailable, result.Status)
			}
			if client.calls != 0 {
				t.Fatalf("completion must not run for invalid candidate metadata, got %d calls", client.calls)
			}
		})
	}
}

func hardeningResources() appdeploy.ResourceRequirements {
	return appdeploy.ResourceRequirements{
		CPU:     "4",
		Memory:  "16Gi",
		GPU:     "1",
		Storage: "20Gi",
	}
}

func hardeningCreateProposalJSON(
	t *testing.T,
	resources appdeploy.ResourceRequirements,
	confidence float64,
) string {
	t.Helper()
	content, err := json.Marshal(Proposal{
		Action:      ActionCreateManifest,
		ReasonCode:  "HARDENING_VALID_CREATE",
		Reason:      "Recent evidence supports the requested GPU deployment plan.",
		Confidence:  &confidence,
		Accelerator: "nvidia",
		Resources:   &resources,
		Assumptions: []string{},
	})
	if err != nil {
		t.Fatalf("marshal hardening proposal: %v", err)
	}
	return string(content)
}
