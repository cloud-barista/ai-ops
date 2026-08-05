package llmop

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type countingRoundTripper struct {
	calls int
}

func (transport *countingRoundTripper) RoundTrip(
	_ *http.Request,
) (*http.Response, error) {
	transport.calls++
	return nil, errors.New("network transport must not be called")
}

func TestPrepareWithConfigDoesNotFallbackForDisabledQwen(t *testing.T) {
	directory := t.TempDir()
	candidatePath := filepath.Join(directory, "candidates.json")
	policyPath := filepath.Join(directory, "policy.json")
	writeJSON(t, candidatePath, map[string]any{
		"version": "1",
		"candidates": []map[string]any{{
			"candidate_id":    "qwen3.5-ops-planner",
			"role_label":      "primary-ops-llm",
			"provider":        "openai-compatible",
			"actual_model":    "qwen3.5:4b",
			"endpoint":        "http://127.0.0.1:8000/v1/chat/completions",
			"enabled":         false,
			"json_mode":       true,
			"timeout_seconds": 60,
		}},
	})
	writeJSON(t, policyPath, testGuardPolicy())

	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	transport := &countingRoundTripper{}
	result, err := PrepareWithConfig(
		context.Background(),
		testRequest(t, now),
		candidatePath,
		policyPath,
		normalizer,
		ProviderOptions{},
		&http.Client{Transport: transport},
	)
	if err == nil {
		t.Fatal("expected disabled Qwen candidate to be unavailable")
	}
	if result.Status != StatusModelUnavailable {
		t.Fatalf("expected %s, got %s", StatusModelUnavailable, result.Status)
	}
	if result.Manifest != nil {
		t.Fatal("disabled Qwen candidate must not produce a fallback manifest")
	}
	if transport.calls != 0 {
		t.Fatalf("disabled candidate path attempted %d HTTP calls", transport.calls)
	}
	if result.Decision.ObservationStatus != result.Evidence.Input.ObservationStatus {
		t.Fatal("candidate failure must preserve normalized observation status")
	}
	assertNoPreparedHandoff(t, result)
}

func TestPrepareWithConfigRejectsUnsafeRequestBeforeCandidateLookup(t *testing.T) {
	directory := t.TempDir()
	candidatePath := filepath.Join(directory, "candidates.json")
	policyPath := filepath.Join(directory, "policy.json")
	writeJSON(t, candidatePath, map[string]any{
		"version": "1",
		"candidates": []map[string]any{{
			"candidate_id":    "qwen3.5-ops-planner",
			"provider":        "openai-compatible",
			"actual_model":    "qwen3.5:4b",
			"endpoint":        "http://127.0.0.1:8000/v1/chat/completions",
			"enabled":         false,
			"json_mode":       true,
			"timeout_seconds": 60,
		}},
	})
	writeJSON(t, policyPath, testGuardPolicy())

	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	request := testRequest(t, now)
	request.Policy.Mode = "approved_submit"
	request.Policy.ApprovalReference = "unverified-approval"

	result, err := PrepareWithConfig(
		context.Background(),
		request,
		candidatePath,
		policyPath,
		normalizer,
		ProviderOptions{},
		nil,
	)
	if err == nil {
		t.Fatal("expected unsafe submission mode to be rejected")
	}
	if result.Status != StatusRequestRejected {
		t.Fatalf("expected %s before candidate selection, got %s", StatusRequestRejected, result.Status)
	}
}

func TestPrepareWithConfigRequiresExplicitLiveCompletion(t *testing.T) {
	directory := t.TempDir()
	candidatePath := filepath.Join(directory, "candidates.json")
	policyPath := filepath.Join(directory, "policy.json")
	writeJSON(t, candidatePath, map[string]any{
		"version": "1",
		"candidates": []map[string]any{{
			"candidate_id":    "qwen3.5-ops-planner",
			"role_label":      "fixed-ops-llm",
			"provider":        "openai-compatible",
			"actual_model":    "qwen3.5:4b",
			"endpoint":        "https://paid-api.invalid/v1/chat/completions",
			"enabled":         true,
			"json_mode":       true,
			"timeout_seconds": 60,
		}},
	})
	writeJSON(t, policyPath, testGuardPolicy())

	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	transport := &countingRoundTripper{}
	result, err := PrepareWithConfig(
		context.Background(),
		testRequest(t, now),
		candidatePath,
		policyPath,
		normalizer,
		ProviderOptions{},
		&http.Client{Transport: transport},
	)
	if err == nil {
		t.Fatal("expected live completion to require explicit authorization")
	}
	if result.Status != StatusConfigurationError {
		t.Fatalf("expected %s, got %s", StatusConfigurationError, result.Status)
	}
	if result.Manifest != nil {
		t.Fatal("disabled live completion must not produce a manifest")
	}
	if transport.calls != 0 {
		t.Fatalf("live-disabled path attempted %d HTTP calls", transport.calls)
	}
	if result.Decision.ObservationStatus != result.Evidence.Input.ObservationStatus {
		t.Fatal("live-disabled result must preserve normalized observation status")
	}
	assertNoPreparedHandoff(t, result)
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	content, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal test JSON: %v", err)
	}
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatalf("write test JSON: %v", err)
	}
}
