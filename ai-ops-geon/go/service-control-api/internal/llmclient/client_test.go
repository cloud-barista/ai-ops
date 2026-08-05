package llmclient

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClientCompleteCallsOpenAICompatibleEndpoint(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			t.Fatalf("expected POST, got %s", request.Method)
		}
		var body map[string]any
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		if body["model"] != "test-model" {
			t.Fatalf("expected test-model, got %#v", body["model"])
		}
		responseFormat, ok := body["response_format"].(map[string]any)
		if !ok || responseFormat["type"] != "json_object" {
			t.Fatalf("expected JSON response format, got %#v", body["response_format"])
		}
		if body["reasoning_effort"] != "none" {
			t.Fatalf("expected reasoning_effort none, got %#v", body["reasoning_effort"])
		}
		writer.Header().Set("content-type", "application/json")
		_, _ = writer.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"observe_status\"}"}}]}`))
	}))
	defer server.Close()

	result, err := NewClient(nil).Complete(context.Background(), Candidate{
		CandidateID:     "test",
		Provider:        "test-provider",
		ActualModel:     "test-model",
		Endpoint:        server.URL,
		Enabled:         true,
		JSONMode:        true,
		ReasoningEffort: "none",
	}, "system", "user")
	if err != nil {
		t.Fatalf("Complete returned error: %v", err)
	}
	if result.Content != `{"action":"observe_status"}` {
		t.Fatalf("unexpected content: %s", result.Content)
	}
	if result.Status != "executed" {
		t.Fatalf("expected executed, got %s", result.Status)
	}
	if result.ActualModel != "test-model" || result.Provider != "test-provider" {
		t.Fatalf("missing provider metadata: %#v", result)
	}
}

func TestClientCompleteDoesNotFallbackWhenEndpointFails(t *testing.T) {
	result, err := NewClient(nil).Complete(context.Background(), Candidate{
		CandidateID:    "test",
		Provider:       "test-provider",
		ActualModel:    "test-model",
		Endpoint:       "http://127.0.0.1:1/v1/chat/completions",
		Enabled:        true,
		TimeoutSeconds: 1,
	}, "system", "user")
	if err == nil {
		t.Fatal("expected provider error")
	}
	if result.Status != "not_executed" {
		t.Fatalf("expected not_executed without fallback, got %s", result.Status)
	}
	if result.Content != "" {
		t.Fatalf("expected no fallback content, got %s", result.Content)
	}
}
