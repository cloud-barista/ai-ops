package llmop

import (
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestNormalizerExcludesStaleObservationFromPromptContext(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now.Add(-30 * time.Minute),
			Items: []appdeploy.DeploymentLog{{
				Message: "stale latency alarm",
			}},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.ObservationStatus != "stale" {
		t.Fatalf("expected stale, got %s", result.ObservationStatus)
	}
	if result.DeploymentLogs != nil {
		t.Fatal("stale logs must not be included in the Qwen prompt context")
	}
	if len(result.StaleSources) != 1 || result.StaleSources[0] != "deployment_logs" {
		t.Fatalf("unexpected stale sources: %#v", result.StaleSources)
	}
}

func TestNormalizerRejectsFutureObservation(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	_, err := normalizer.Normalize(OperationContext{
		MetricsSummary: &MetricsObservation{
			Source:     "appdeploy-metrics",
			ObservedAt: now.Add(5 * time.Minute),
		},
	})
	if err == nil {
		t.Fatal("expected future observation rejection")
	}
}

func TestNormalizerLimitsLogs(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	normalizer.MaxLogs = 2

	result, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now,
			Items: []appdeploy.DeploymentLog{
				{
					Timestamp: now.Add(-3 * time.Minute).Format(time.RFC3339Nano),
					Message:   "old",
				},
				{
					Timestamp: now.Add(-time.Minute).Format(time.RFC3339Nano),
					Message:   "latest",
				},
				{
					Timestamp: now.Add(-2 * time.Minute).Format(time.RFC3339Nano),
					Message:   "middle",
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.DroppedLogs != 1 {
		t.Fatalf("expected one dropped log, got %d", result.DroppedLogs)
	}
	if result.DeploymentLogs == nil || len(result.DeploymentLogs.Items) != 2 {
		t.Fatal("expected only the two newest logs")
	}
	if result.DeploymentLogs.Items[0].Message != "latest" {
		t.Fatalf("unexpected retained log: %s", result.DeploymentLogs.Items[0].Message)
	}
	if result.DeploymentLogs.Items[1].Message != "middle" {
		t.Fatalf("unexpected second retained log: %s", result.DeploymentLogs.Items[1].Message)
	}
}

func TestNormalizerStableSortsEqualLogTimestamps(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	normalizer.MaxLogs = 2
	timestamp := now.Add(-time.Minute).Format(time.RFC3339Nano)

	result, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now,
			Items: []appdeploy.DeploymentLog{
				{Timestamp: timestamp, Message: "first"},
				{Timestamp: timestamp, Message: "second"},
				{Timestamp: timestamp, Message: "third"},
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.DeploymentLogs == nil || len(result.DeploymentLogs.Items) != 2 {
		t.Fatal("expected two retained logs")
	}
	if result.DeploymentLogs.Items[0].Message != "first" ||
		result.DeploymentLogs.Items[1].Message != "second" {
		t.Fatalf("equal timestamps did not preserve input order: %#v", result.DeploymentLogs.Items)
	}
}

func TestNormalizerDropsMalformedAndStaleLogTimestamps(t *testing.T) {
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
				{
					Timestamp: now.Add(-time.Minute).Format(time.RFC3339Nano),
					Message:   "fresh",
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.DroppedLogs != 2 {
		t.Fatalf("expected two dropped logs, got %d", result.DroppedLogs)
	}
	if result.DeploymentLogs == nil || len(result.DeploymentLogs.Items) != 1 {
		t.Fatal("expected one retained log")
	}
	if result.DeploymentLogs.Items[0].Message != "fresh" {
		t.Fatalf("unexpected retained log: %#v", result.DeploymentLogs.Items[0])
	}
	if result.ObservationStatus != "mixed" {
		t.Fatalf("expected mixed freshness evidence, got %s", result.ObservationStatus)
	}
	if len(result.StaleSources) != 1 || result.StaleSources[0] != "deployment_logs.items" {
		t.Fatalf("unexpected stale sources: %#v", result.StaleSources)
	}
}

func TestNormalizerRejectsLogTimestampLaterThanWrapper(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	_, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now.Add(-3 * time.Minute),
			Items: []appdeploy.DeploymentLog{{
				Timestamp: now.Format(time.RFC3339Nano),
			}},
		},
	})
	if err == nil {
		t.Fatal("expected a log timestamp later than its wrapper to be rejected")
	}
}

func TestNormalizerRejectsTypedInternalTimestampLaterThanParent(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	observedAt := now.Add(-3 * time.Minute)
	cases := []struct {
		name    string
		context OperationContext
	}{
		{
			name: "resource runtime last checked",
			context: OperationContext{ResourceSnapshot: &ResourceSnapshot{
				Source:     "appdeploy-monitoring",
				ObservedAt: observedAt,
				Targets: []appdeploy.RuntimeHealthSnapshot{{
					LastCheckedAt: now,
				}},
			}},
		},
		{
			name: "monitoring generated at",
			context: OperationContext{MonitoringSummary: &MonitoringObservation{
				Source:     "appdeploy-monitoring",
				ObservedAt: observedAt,
				Summary: appdeploy.MonitoringSummaryResponse{
					GeneratedAt: now,
				},
			}},
		},
		{
			name: "monitoring runtime last checked",
			context: OperationContext{MonitoringSummary: &MonitoringObservation{
				Source:     "appdeploy-monitoring",
				ObservedAt: now,
				Summary: appdeploy.MonitoringSummaryResponse{
					GeneratedAt: observedAt,
					RuntimeHealth: []appdeploy.RuntimeHealthSnapshot{{
						LastCheckedAt: now,
					}},
				},
			}},
		},
		{
			name: "monitoring alarm latest at",
			context: OperationContext{MonitoringSummary: &MonitoringObservation{
				Source:     "appdeploy-monitoring",
				ObservedAt: now,
				Summary: appdeploy.MonitoringSummaryResponse{
					GeneratedAt: observedAt,
					Alarms: []appdeploy.DeploymentAlarmSummary{{
						LatestAt: now,
					}},
				},
			}},
		},
	}

	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }

			_, err := normalizer.Normalize(test.context)
			if err == nil {
				t.Fatal("expected an internal timestamp later than its parent to be rejected")
			}
		})
	}
}

func TestNormalizerCrossChecksMonitoringTimestamps(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := normalizer.Normalize(OperationContext{
		MonitoringSummary: &MonitoringObservation{
			Source:     "appdeploy-monitoring",
			ObservedAt: now,
			Summary: appdeploy.MonitoringSummaryResponse{
				GeneratedAt: now,
				RuntimeHealth: []appdeploy.RuntimeHealthSnapshot{
					{
						TargetProfileID: "target-stale",
						LastCheckedAt:   now.Add(-30 * time.Minute),
					},
					{
						TargetProfileID: "target-fresh",
						LastCheckedAt:   now.Add(-time.Minute),
					},
				},
				Alarms: []appdeploy.DeploymentAlarmSummary{
					{LatestAt: now.Add(-30 * time.Minute), LatestMessage: "stale"},
					{LatestAt: now.Add(-time.Minute), LatestMessage: "fresh"},
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.MonitoringSummary == nil {
		t.Fatal("expected a retained monitoring summary")
	}
	if len(result.MonitoringSummary.Summary.RuntimeHealth) != 1 ||
		result.MonitoringSummary.Summary.RuntimeHealth[0].TargetProfileID != "target-fresh" {
		t.Fatalf(
			"unexpected runtime health snapshots: %#v",
			result.MonitoringSummary.Summary.RuntimeHealth,
		)
	}
	if len(result.MonitoringSummary.Summary.Alarms) != 1 ||
		result.MonitoringSummary.Summary.Alarms[0].LatestMessage != "fresh" {
		t.Fatalf("unexpected alarms: %#v", result.MonitoringSummary.Summary.Alarms)
	}
	if result.ObservationStatus != "mixed" {
		t.Fatalf("expected mixed, got %s", result.ObservationStatus)
	}
	if len(result.StaleSources) != 2 {
		t.Fatalf("unexpected stale sources: %#v", result.StaleSources)
	}
}

func TestNormalizerExcludesSummaryWithStaleGeneratedAt(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := normalizer.Normalize(OperationContext{
		MonitoringSummary: &MonitoringObservation{
			Source:     "appdeploy-monitoring",
			ObservedAt: now,
			Summary: appdeploy.MonitoringSummaryResponse{
				GeneratedAt: now.Add(-30 * time.Minute),
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.MonitoringSummary != nil {
		t.Fatal("stale generated_at must exclude the monitoring summary")
	}
	if result.ObservationStatus != "stale" {
		t.Fatalf("expected stale, got %s", result.ObservationStatus)
	}
}

func TestNormalizerRejectsOversizedCollections(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	cases := []struct {
		name      string
		configure func(*Normalizer)
		context   OperationContext
	}{
		{
			name: "resource targets",
			configure: func(normalizer *Normalizer) {
				normalizer.MaxTargets = 1
			},
			context: OperationContext{ResourceSnapshot: &ResourceSnapshot{
				Source:     "appdeploy-monitoring",
				ObservedAt: now,
				Targets: []appdeploy.RuntimeHealthSnapshot{
					{LastCheckedAt: now},
					{LastCheckedAt: now},
				},
			}},
		},
		{
			name: "monitoring runtime health",
			configure: func(normalizer *Normalizer) {
				normalizer.MaxRuntimeHealth = 1
			},
			context: monitoringContext(now, 2, 0, nil),
		},
		{
			name: "monitoring alarms",
			configure: func(normalizer *Normalizer) {
				normalizer.MaxAlarms = 1
			},
			context: monitoringContext(now, 0, 2, nil),
		},
		{
			name: "monitoring status buckets",
			configure: func(normalizer *Normalizer) {
				normalizer.MaxStatusBuckets = 1
			},
			context: monitoringContext(now, 0, 0, map[string]int{
				"RUNNING": 1,
				"FAILED":  1,
			}),
		},
		{
			name: "log inputs",
			configure: func(normalizer *Normalizer) {
				normalizer.MaxLogInputs = 1
			},
			context: OperationContext{DeploymentLogs: &LogObservation{
				Source:     "appdeploy-logs",
				ObservedAt: now,
				Items: []appdeploy.DeploymentLog{
					{Timestamp: now.Format(time.RFC3339Nano)},
					{Timestamp: now.Format(time.RFC3339Nano)},
				},
			}},
		},
	}

	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			normalizer := NewNormalizer()
			normalizer.Now = func() time.Time { return now }
			test.configure(&normalizer)

			_, err := normalizer.Normalize(test.context)
			if err == nil {
				t.Fatal("expected an oversized collection to be rejected")
			}
		})
	}
}

func TestNormalizerBoundsAndRedactsLogStrings(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	normalizer.MaxFieldRunes = 5
	normalizer.MaxLogRunes = 20

	result, err := normalizer.Normalize(OperationContext{
		DeploymentLogs: &LogObservation{
			Source:     "appdeploy-logs",
			ObservedAt: now,
			Items: []appdeploy.DeploymentLog{
				{
					Timestamp: now.Format(time.RFC3339Nano),
					Component: "runtime-component",
					Message:   "token=demo-secret " + strings.Repeat("x", 40),
				},
				{
					Timestamp: now.Format(time.RFC3339Nano),
					RequestID: "request-id-too-long",
					Message:   "drop me",
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.DeploymentLogs == nil || len(result.DeploymentLogs.Items) != 1 {
		t.Fatal("expected the log with an oversized identifier to be dropped")
	}
	retained := result.DeploymentLogs.Items[0]
	if len([]rune(retained.Component)) > normalizer.MaxFieldRunes {
		t.Fatalf("component was not bounded: %q", retained.Component)
	}
	if len([]rune(retained.Message)) > normalizer.MaxLogRunes {
		t.Fatalf("message was not bounded: %q", retained.Message)
	}
	if strings.Contains(retained.Message, "demo-secret") {
		t.Fatalf("sensitive value was not redacted: %q", retained.Message)
	}
	if result.RedactedValues != 1 {
		t.Fatalf("expected one redaction, got %d", result.RedactedValues)
	}
	if result.DroppedLogs != 1 {
		t.Fatalf("expected one dropped log, got %d", result.DroppedLogs)
	}
}

func TestNormalizerRedactsMonitoringStatusKeys(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := normalizer.Normalize(monitoringContext(
		now,
		0,
		0,
		map[string]int{"token=demo-secret": 1},
	))
	if err != nil {
		t.Fatalf("Normalize returned an unexpected error: %v", err)
	}
	if result.MonitoringSummary == nil {
		t.Fatal("expected a retained monitoring summary")
	}
	for status := range result.MonitoringSummary.Summary.Deployments.ByStatus {
		if strings.Contains(status, "demo-secret") {
			t.Fatalf("sensitive status key was not redacted: %q", status)
		}
	}
	if result.RedactedValues != 1 {
		t.Fatalf("expected one redaction, got %d", result.RedactedValues)
	}
}

func monitoringContext(
	now time.Time,
	runtimeHealthCount int,
	alarmCount int,
	byStatus map[string]int,
) OperationContext {
	runtimeHealth := make([]appdeploy.RuntimeHealthSnapshot, runtimeHealthCount)
	for index := range runtimeHealth {
		runtimeHealth[index].LastCheckedAt = now
	}
	alarms := make([]appdeploy.DeploymentAlarmSummary, alarmCount)
	for index := range alarms {
		alarms[index].LatestAt = now
	}
	return OperationContext{MonitoringSummary: &MonitoringObservation{
		Source:     "appdeploy-monitoring",
		ObservedAt: now,
		Summary: appdeploy.MonitoringSummaryResponse{
			GeneratedAt:   now,
			RuntimeHealth: runtimeHealth,
			Alarms:        alarms,
			Deployments: appdeploy.DeploymentMonitorSummary{
				ByStatus: byStatus,
			},
		},
	}}
}
