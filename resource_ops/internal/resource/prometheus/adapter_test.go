package prometheus

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"ai-mcmp/internal/recommendation"
	"ai-mcmp/internal/resource"
)

func TestAdapterCollectsPrometheusMetricsAsRawFacts(t *testing.T) {
	fixed := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	var mutex sync.Mutex
	queryTimes := map[string]struct{}{}
	requestCount := 0
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/query" {
			t.Errorf("path = %q", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("authorization header missing")
		}
		query := request.URL.Query().Get("query")
		mutex.Lock()
		queryTimes[request.URL.Query().Get("time")] = struct{}{}
		requestCount++
		mutex.Unlock()

		labels := map[string]string{
			"node_id": "node-a", "hostname": "worker-a", "node_ip": "10.0.0.1",
		}
		value := 1.0
		switch {
		case strings.Contains(query, "node_cpu_seconds_total") && strings.HasPrefix(query, "count"):
			value = 16
		case strings.Contains(query, "node_cpu_seconds_total"):
			value = 25
		case strings.Contains(query, "MemTotal"):
			value = 64 * 1024 * 1024 * 1024
		case strings.Contains(query, "MemAvailable"):
			value = 48 * 1024 * 1024 * 1024
		case strings.Contains(query, "filesystem_size"):
			value = 200 * 1024 * 1024 * 1024
		case strings.Contains(query, "filesystem_avail"):
			value = 150 * 1024 * 1024 * 1024
		case strings.Contains(query, "DCGM_FI_DEV"):
			labels["UUID"] = "gpu-0"
			labels["gpu"] = "0"
			labels["modelName"] = "Test GPU"
			switch {
			case strings.Contains(query, "GPU_UTIL"):
				value = 10
			case strings.Contains(query, "FB_FREE"):
				value = 40_000
			case strings.Contains(query, "FB_USED"):
				value = 7_000
			case strings.Contains(query, "FB_RESERVED"):
				value = 1_000
			}
		case strings.Contains(query, "node_uname_info"):
			labels["machine"] = "x86_64"
		case strings.Contains(query, "node_os_info"):
			labels["pretty_name"] = "Test Linux"
		}
		writeVector(writer, fixed, labels, value)
	}))
	defer server.Close()

	adapter, err := New(Config{
		BaseURL: server.URL, Timeout: time.Second, BearerToken: "test-token", Now: func() time.Time { return fixed },
	})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := adapter.Collect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if raw.Source != "prometheus" || len(raw.Facts) != len(queries) {
		t.Fatalf("source=%q facts=%d, want %d", raw.Source, len(raw.Facts), len(queries))
	}
	if requestCount != len(queries) || len(queryTimes) != 1 {
		t.Fatalf("requestCount=%d queryTimes=%v", requestCount, queryTimes)
	}
	if !hasFact(raw.Facts, resource.FactCPUCount, "node-a", "") || !hasFact(raw.Facts, resource.FactGPUMemoryFreeMiB, "node-a", "gpu-0") {
		t.Fatalf("expected CPU and GPU facts: %#v", raw.Facts)
	}
}

func TestAdapterFailsWhenRequiredMetricQueryFails(t *testing.T) {
	fixed := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if strings.HasPrefix(request.URL.Query().Get("query"), "count") {
			http.Error(writer, "broken", http.StatusInternalServerError)
			return
		}
		writeVector(writer, fixed, map[string]string{"node_id": "node-a"}, 1)
	}))
	defer server.Close()
	adapter, err := New(Config{BaseURL: server.URL, Timeout: time.Second, Now: func() time.Time { return fixed }})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := adapter.Collect(context.Background()); err == nil || !strings.Contains(err.Error(), resource.FactCPUCount) {
		t.Fatalf("error = %v, want required CPU metric failure", err)
	}
}

func TestAdapterDropsStaleAndFutureFactsBeforeRecommendation(t *testing.T) {
	fixed := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var kind string
		for _, definition := range queries {
			if definition.promQL == request.URL.Query().Get("query") {
				kind = definition.kind
				break
			}
		}
		if kind == "" {
			t.Errorf("unknown query %q", request.URL.Query().Get("query"))
			http.Error(writer, "unknown query", http.StatusBadRequest)
			return
		}
		value := sampleValue(kind)
		samples := make([]vectorSample, 0, 2)
		for _, node := range []string{"node-a", "node-b"} {
			observedAt := fixed.Add(-20 * time.Second)
			if node == "node-b" {
				observedAt = fixed.Add(-30 * time.Second)
				switch kind {
				case resource.FactCPUUtilizationPercent, resource.FactGPUMemoryFreeMiB:
					observedAt = fixed.Add(-3 * time.Minute)
				case resource.FactMemoryAvailableBytes:
					observedAt = fixed.Add(time.Second)
				}
			}
			labels := map[string]string{"node_id": node, "hostname": node}
			if strings.HasPrefix(kind, "gpu_") {
				labels["UUID"] = node + "-gpu-0"
				labels["gpu"] = "0"
			}
			samples = append(samples, vectorSample{observedAt: observedAt, labels: labels, value: value})
		}
		writeVectorSamples(writer, samples)
	}))
	defer server.Close()

	adapter, err := New(Config{
		BaseURL: server.URL, Timeout: time.Second, MaxSampleAge: 2 * time.Minute,
		Now: func() time.Time { return fixed },
	})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := adapter.Collect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !raw.ObservedAt.Equal(fixed.Add(-30 * time.Second)) {
		t.Fatalf("raw observation time = %s, want oldest accepted sample", raw.ObservedAt)
	}
	for _, kind := range []string{resource.FactCPUUtilizationPercent, resource.FactMemoryAvailableBytes, resource.FactGPUMemoryFreeMiB} {
		if hasFact(raw.Facts, kind, "node-b", "") || hasFact(raw.Facts, kind, "node-b", "node-b-gpu-0") {
			t.Fatalf("rejected %s sample from node-b was retained", kind)
		}
	}
	for _, warning := range []string{
		"STALE_PROMETHEUS_SAMPLES_DROPPED:" + resource.FactCPUUtilizationPercent + ":1",
		"FUTURE_PROMETHEUS_SAMPLES_DROPPED:" + resource.FactMemoryAvailableBytes + ":1",
		"STALE_PROMETHEUS_SAMPLES_DROPPED:" + resource.FactGPUMemoryFreeMiB + ":1",
	} {
		if !hasWarning(raw.Warnings, warning) {
			t.Fatalf("warnings %v omit %q", raw.Warnings, warning)
		}
	}

	service, err := recommendation.New(adapter)
	if err != nil {
		t.Fatal(err)
	}
	result, err := service.Recommend(context.Background(), resource.ResourceQuery{
		RequestID: "mixed-observations",
		Constraints: resource.QueryConstraints{
			CPUCoresMin: 8, MemoryMiBMin: 16 * 1024,
			Accelerator: resource.AcceleratorConstraint{Required: true, Type: "GPU", CountMin: 1, MemoryMiBMinPerDevice: 4 * 1024},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.ObservedAt.Equal(fixed.Add(-30*time.Second)) || len(result.Recommendations) != 1 ||
		result.Recommendations[0].Resource.ResourceID != "node-a" ||
		!result.Recommendations[0].Resource.ObservedAt.Equal(fixed.Add(-20*time.Second)) {
		t.Fatalf("unexpected recommendation observation/feasibility: %#v", result)
	}
	if len(result.ExcludedResources) != 1 || result.ExcludedResources[0].ResourceID != "node-b" {
		t.Fatalf("node-b was not excluded: %#v", result.ExcludedResources)
	}
	wantFailures := map[string]bool{
		"CPU_AVAILABILITY_UNKNOWN":           false,
		"MEMORY_AVAILABILITY_UNKNOWN":        false,
		"ACCELERATOR_AVAILABLE_INSUFFICIENT": false,
	}
	for _, failure := range result.ExcludedResources[0].Failures {
		if _, ok := wantFailures[failure.Code]; ok {
			wantFailures[failure.Code] = true
		}
	}
	for code, found := range wantFailures {
		if !found {
			t.Fatalf("missing %s from node-b exclusions: %#v", code, result.ExcludedResources[0].Failures)
		}
	}
}

func TestAdapterRejectsRequiredQueryWithOnlyStaleSamples(t *testing.T) {
	fixed := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		observedAt := fixed.Add(-10 * time.Second)
		if strings.HasPrefix(request.URL.Query().Get("query"), "count") {
			observedAt = fixed.Add(-3 * time.Minute)
		}
		writeVector(writer, observedAt, map[string]string{"node_id": "node-a"}, 16)
	}))
	defer server.Close()
	adapter, err := New(Config{BaseURL: server.URL, Timeout: time.Second, MaxSampleAge: 2 * time.Minute, Now: func() time.Time { return fixed }})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := adapter.Collect(context.Background()); err == nil ||
		!strings.Contains(err.Error(), resource.FactCPUCount) || !strings.Contains(err.Error(), "no fresh data") {
		t.Fatalf("error = %v, want required stale fact rejection", err)
	}
}

func TestDefaultConfigReadsMaximumSampleAge(t *testing.T) {
	t.Setenv("RESOURCE_PROMETHEUS_MAX_SAMPLE_AGE", "45s")
	if got := DefaultConfig().MaxSampleAge; got != 45*time.Second {
		t.Fatalf("maximum sample age = %s, want 45s", got)
	}
}

type vectorSample struct {
	observedAt time.Time
	labels     map[string]string
	value      float64
}

func writeVectorSamples(writer http.ResponseWriter, samples []vectorSample) {
	result := make([]any, 0, len(samples))
	for _, sample := range samples {
		result = append(result, map[string]any{
			"metric": sample.labels,
			"value":  []any{float64(sample.observedAt.UnixNano()) / 1e9, formatValue(sample.value)},
		})
	}
	writer.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"status": "success",
		"data":   map[string]any{"resultType": "vector", "result": result},
	})
}

func sampleValue(kind string) float64 {
	switch kind {
	case resource.FactCPUCount:
		return 16
	case resource.FactCPUUtilizationPercent:
		return 10
	case resource.FactMemoryTotalBytes:
		return 64 * 1024 * 1024 * 1024
	case resource.FactMemoryAvailableBytes:
		return 48 * 1024 * 1024 * 1024
	case resource.FactStorageTotalBytes:
		return 200 * 1024 * 1024 * 1024
	case resource.FactStorageAvailableBytes:
		return 150 * 1024 * 1024 * 1024
	case resource.FactGPUMemoryFreeMiB:
		return 40_000
	case resource.FactGPUMemoryUsedMiB:
		return 7_000
	case resource.FactGPUMemoryReservedMiB:
		return 1_000
	case resource.FactGPUUtilizationPercent:
		return 10
	default:
		return 1
	}
}

func hasWarning(warnings []string, wanted string) bool {
	for _, warning := range warnings {
		if warning == wanted {
			return true
		}
	}
	return false
}

func writeVector(writer http.ResponseWriter, observedAt time.Time, labels map[string]string, value float64) {
	writer.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"status": "success",
		"data": map[string]any{
			"resultType": "vector",
			"result": []any{map[string]any{
				"metric": labels,
				"value":  []any{float64(observedAt.UnixNano()) / 1e9, formatValue(value)},
			}},
		},
	})
}

func formatValue(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func hasFact(facts []resource.RawFact, kind, resourceID, deviceID string) bool {
	for _, fact := range facts {
		if fact.Kind == kind && fact.ResourceID == resourceID && fact.DeviceID == deviceID {
			return true
		}
	}
	return false
}
