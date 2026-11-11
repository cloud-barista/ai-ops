package profileapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"kyunghee-aiops/llmop-api/internal/llmclient"
)

func TestOpsLLMRunCanBeRecoveredWithoutCallingProviderAgain(t *testing.T) {
	const (
		runID  = "opsllm-abcdefghijklmnopqrstuvwxyz"
		body   = `{"intent":"응용 목록을 보여줘"}`
		bearer = "Bearer test-ops-token"
	)
	entered, release := make(chan struct{}, 1), make(chan struct{})
	var calls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		select {
		case entered <- struct{}{}:
		default:
		}
		select {
		case <-release:
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"list_applications\"}"}}]}`))
		case <-r.Context().Done():
		}
	}))
	defer provider.Close()
	var once sync.Once
	finish := func() { once.Do(func() { close(release) }) }
	defer finish()

	settings := testConfig()
	settings.RepoRoot = t.TempDir()
	settings.OpsLLMToken, settings.CandidateID = "test-ops-token", "local"
	settings.LLMCandidatesPath = filepath.Join(settings.RepoRoot, "candidates.json")
	config := llmclient.CandidateConfig{Candidates: []llmclient.Candidate{{
		CandidateID: "local", LLMType: "self_hosted", Endpoint: provider.URL, ActualModel: "test-model", Enabled: true,
	}}}
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(settings.LLMCandidatesPath, data, 0600); err != nil {
		t.Fatal(err)
	}

	request := func(server http.Handler, method, path, token, id, content string) *httptest.ResponseRecorder {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		req := httptest.NewRequestWithContext(ctx, method, path, strings.NewReader(content))
		req.Header.Set("Authorization", token)
		req.Header.Set("X-AIMCMP-OpsLLM-Run-ID", id)
		resp := httptest.NewRecorder()
		server.ServeHTTP(resp, req)
		return resp
	}
	server := newServer(settings, &generatorStub{})
	lookup := "/api/v1/ops-llm/runs/" + runID
	if got := request(server, http.MethodGet, lookup, "", "", ""); got.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized lookup: %d %s", got.Code, got.Body)
	}
	if got := request(server, http.MethodGet, lookup, bearer, "", ""); got.Code != http.StatusNotFound {
		t.Fatalf("unknown run: %d %s", got.Code, got.Body)
	}
	if got := request(server, http.MethodPost, pathOpsLLMInterpret, bearer, "../escape", body); got.Code != http.StatusBadRequest {
		t.Fatalf("invalid run ID: %d %s", got.Code, got.Body)
	}
	first := make(chan *httptest.ResponseRecorder, 1)
	go func() { first <- request(server, http.MethodPost, pathOpsLLMInterpret, bearer, runID, body) }()
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("provider did not receive the reserved run")
	}
	otherInstance := newServer(settings, &generatorStub{})
	for _, tc := range []struct {
		method, path, token, id, content string
		status                           int
	}{
		{http.MethodGet, lookup, bearer, "", "", http.StatusOK},
		{http.MethodPost, pathOpsLLMInterpret, bearer, runID, body, http.StatusAccepted},
		{http.MethodPost, pathOpsLLMInterpret, bearer, runID, `{"intent":"응용을 배포해"}`, http.StatusConflict},
	} {
		got := request(otherInstance, tc.method, tc.path, tc.token, tc.id, tc.content)
		if got.Code != tc.status || (got.Code == http.StatusOK || got.Code == http.StatusAccepted) &&
			!strings.Contains(got.Body.String(), `"status":"completion_unconfirmed"`) {
			t.Fatalf("%s %s: %d %s, want %d", tc.method, tc.path, got.Code, got.Body, tc.status)
		}
	}
	finish()
	var completed *httptest.ResponseRecorder
	select {
	case completed = <-first:
	case <-time.After(3 * time.Second):
		t.Fatal("original run did not complete")
	}
	if completed.Code != http.StatusOK || !strings.Contains(completed.Body.String(), `"status":"completed"`) {
		t.Fatalf("completed run: %d %s", completed.Code, completed.Body)
	}
	unwritable := settings
	unwritable.RepoRoot = settings.LLMCandidatesPath
	if got := request(newServer(unwritable, &generatorStub{}), http.MethodPost, pathOpsLLMInterpret, bearer, runID, body); got.Code != http.StatusServiceUnavailable ||
		!strings.Contains(got.Body.String(), "OPS_LLM_RUN_STORE_UNAVAILABLE") || calls.Load() != 1 {
		t.Fatalf("unavailable run store: %d %s, provider calls=%d", got.Code, got.Body, calls.Load())
	}
	if err := os.Remove(settings.LLMCandidatesPath); err != nil {
		t.Fatal(err)
	}
	restarted := newServer(settings, &generatorStub{})
	for _, got := range []*httptest.ResponseRecorder{
		request(restarted, http.MethodGet, lookup, bearer, "", ""),
		request(restarted, http.MethodPost, pathOpsLLMInterpret, bearer, runID, body),
	} {
		if got.Code != http.StatusOK || got.Body.String() != completed.Body.String() {
			t.Fatalf("recovered run: %d %s", got.Code, got.Body)
		}
	}
	stored, err := os.ReadFile(filepath.Join(settings.RepoRoot, "runs", "ops-llm", runID+".json"))
	if err != nil || strings.Contains(string(stored), "응용 목록을 보여줘") || calls.Load() != 1 {
		t.Fatalf("provider calls=%d, run metadata=%s, err=%v", calls.Load(), stored, err)
	}
}
