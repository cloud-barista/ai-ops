package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"ai-mcmp/internal/resource"
)

type stubSource struct {
	raw resource.RawSnapshot
	err error
}

func (source stubSource) Name() string { return "stub" }
func (source stubSource) Collect(context.Context) (resource.RawSnapshot, error) {
	return source.raw, source.err
}

func TestHealthDescribesFocusedResourceOpsRole(t *testing.T) {
	router := testRouter(t, stubSource{raw: apiSnapshot()})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/health", nil)
	router.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", recorder.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["resource_source"] != "stub" || body["role"] != "resource_discovery_normalization_filtering_ranking_recommendation" {
		t.Fatalf("unexpected health body: %#v", body)
	}
}

func TestRecommendReturnsResourceRecommendation(t *testing.T) {
	router := testRouter(t, stubSource{raw: apiSnapshot()})
	body := `{
		"schema_version":"1.0",
		"request_id":"flow-1",
		"application_profile":{
			"profile_id":"profile-1",
			"app_id":"chat-service",
			"app_version":"1.0.0",
			"workload":{"task_type":"LLM_INFERENCE","request_pattern":"ONLINE"},
			"requirements":{
				"compute":{"cpu_cores_min":4,"memory_mib_min":8192,"storage_gib_min":20},
				"accelerator":{"required":false,"count_min":0,"memory_mib_min_per_device":0},
				"deployment":{"replicas_min":1,"replicas_max":2,"isolation":"ONE_MAJOR_APP_PER_VM"},
				"slo":{},"cost":{}
			},
			"analysis":{"confidence":0.9}
		}
	}`
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/resource-recommendations", bytes.NewBufferString(body))
	request.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", recorder.Code, recorder.Body.String())
	}
	var response resource.ResourceRecommendation
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.RequestID != "flow-1" || response.ProfileID != "profile-1" || response.Source != "stub" || len(response.Recommendations) != 1 {
		t.Fatalf("unexpected response: %#v", response)
	}
	if response.Recommendations[0].Resource.ResourceID != "node-a" || len(response.Recommendations[0].Reasons) == 0 {
		t.Fatalf("unexpected recommendation: %#v", response.Recommendations[0])
	}
}

func TestRecommendRejectsUnknownJSONField(t *testing.T) {
	router := testRouter(t, stubSource{raw: apiSnapshot()})
	body := `{"request_id":"r","application_profile":{},"surprise":true}`
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/resource-recommendations", bytes.NewBufferString(body))
	router.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", recorder.Code)
	}
}

func TestRecommendReturnsValidationAndSourceErrors(t *testing.T) {
	tests := []struct {
		name   string
		source stubSource
		body   string
		status int
		code   string
	}{
		{
			name: "invalid query", source: stubSource{raw: apiSnapshot()},
			body:   `{"request_id":"r","constraints":{"cpu_cores_min":0,"memory_mib_min":1,"accelerator":{}}}`,
			status: http.StatusUnprocessableEntity, code: "INVALID_RESOURCE_QUERY",
		},
		{
			name: "source unavailable", source: stubSource{err: errors.New("offline")},
			body:   `{"request_id":"r","constraints":{"cpu_cores_min":1,"memory_mib_min":1,"accelerator":{}}}`,
			status: http.StatusBadGateway, code: "RESOURCE_SOURCE_UNAVAILABLE",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			router := testRouter(t, test.source)
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/legacy/api/v1/resource-recommendations", bytes.NewBufferString(test.body))
			router.ServeHTTP(recorder, request)
			if recorder.Code != test.status {
				t.Fatalf("status = %d, want %d: %s", recorder.Code, test.status, recorder.Body.String())
			}
			var response struct {
				Error errorDetail `json:"error"`
			}
			if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
				t.Fatal(err)
			}
			if response.Error.Code != test.code {
				t.Fatalf("code = %q, want %q", response.Error.Code, test.code)
			}
		})
	}
}

func TestLegacyQueryIsIsolatedAndOldAliasesAreNotExposed(t *testing.T) {
	router := testRouter(t, stubSource{raw: apiSnapshot()})
	for _, path := range []string{"/recommend", "/select", "/optimize"} {
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(`{}`))
		router.ServeHTTP(recorder, request)
		if recorder.Code != http.StatusNotFound {
			t.Fatalf("%s status = %d, want 404", path, recorder.Code)
		}
	}
}

func TestRecommendRejectsInvalidApplicationProfile(t *testing.T) {
	router := testRouter(t, stubSource{raw: apiSnapshot()})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/resource-recommendations",
		bytes.NewBufferString(`{"request_id":"flow-1","application_profile":{"profile_id":"profile-1"}}`),
	)
	router.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want 422: %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Error errorDetail `json:"error"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.Error.Code != "INVALID_APPLICATION_PROFILE" {
		t.Fatalf("code = %q", response.Error.Code)
	}
}

func testRouter(t *testing.T, source resource.Source) http.Handler {
	t.Helper()
	router, err := NewRouterWithSource(source)
	if err != nil {
		t.Fatal(err)
	}
	return router
}

func apiSnapshot() resource.RawSnapshot {
	observedAt := time.Date(2026, 8, 27, 6, 0, 0, 0, time.UTC)
	labels := map[string]string{"hostname": "node-a", "node_ip": "10.0.0.1"}
	const bytesPerMiB = 1024 * 1024
	const bytesPerGiB = 1024 * 1024 * 1024
	return resource.RawSnapshot{
		Source: "stub", ObservedAt: observedAt,
		Facts: []resource.RawFact{
			{ResourceID: "node-a", Kind: resource.FactNodeUp, Value: 1, Labels: labels, ObservedAt: observedAt},
			{ResourceID: "node-a", Kind: resource.FactCPUCount, Value: 16, Labels: labels, ObservedAt: observedAt},
			{ResourceID: "node-a", Kind: resource.FactCPUUtilizationPercent, Value: 10, Labels: labels, ObservedAt: observedAt},
			{ResourceID: "node-a", Kind: resource.FactMemoryTotalBytes, Value: 64 * 1024 * bytesPerMiB, Labels: labels, ObservedAt: observedAt},
			{ResourceID: "node-a", Kind: resource.FactMemoryAvailableBytes, Value: 48 * 1024 * bytesPerMiB, Labels: labels, ObservedAt: observedAt},
			{ResourceID: "node-a", Kind: resource.FactStorageTotalBytes, Value: 100 * bytesPerGiB, Labels: labels, ObservedAt: observedAt},
			{ResourceID: "node-a", Kind: resource.FactStorageAvailableBytes, Value: 80 * bytesPerGiB, Labels: labels, ObservedAt: observedAt},
		},
		Warnings: []string{},
	}
}
