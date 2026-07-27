package api

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/spf13/viper"
)

func TestHTTPAgentExecutorPostsContractAndReturnsEnvelope(t *testing.T) {
	t.Setenv("EXTERNAL_RESEARCH_AGENT_TOKEN", "test-bearer-token")
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			t.Errorf("method=%s want=%s", request.Method, http.MethodPost)
		}
		if request.Header.Get("Content-Type") != "application/json" {
			t.Errorf("content-type=%q", request.Header.Get("Content-Type"))
		}
		if request.Header.Get("Authorization") != "Bearer test-bearer-token" {
			t.Errorf("authorization header was not populated from the configured environment reference")
		}
		body, err := io.ReadAll(request.Body)
		if err != nil {
			t.Errorf("read request body: %v", err)
		}
		for _, expected := range []string{
			`"run_id":"run-001"`,
			`"agent":"ExternalResearchAgent"`,
			`"capability":"deployment_review"`,
			`"action":"review_deployment_plan"`,
			`"workload":"llm-chat-inference"`,
		} {
			if !strings.Contains(string(body), expected) {
				t.Errorf("request body is missing %s: %s", expected, body)
			}
		}
		response.Header().Set("Content-Type", "application/json")
		fmt.Fprint(response, `{
			"run_id":"run-001",
			"agent":"ExternalResearchAgent",
			"status":"completed",
			"proposal":{"action":"review_deployment_plan","parameters":{"decision":"approved"}},
			"result":{"review":"approved"},
			"evidence":{"source":"external-test"},
			"message":"review completed"
		}`)
	}))
	defer server.Close()

	executor := newHTTPAgentExecutor(server.Client(), time.Second)
	result, err := executor.Execute(
		context.Background(),
		runtimeAgentProfile(server.URL),
		runtimeAgentDispatchRequest(),
	)
	if err != nil {
		t.Fatalf("execute runtime Agent: %v", err)
	}
	if result.Status != "completed" || result.Proposal.Action != "review_deployment_plan" {
		t.Fatalf("unexpected runtime result: %#v", result)
	}
	if result.DomainValidation != "not_registered" || result.LatencyMS < 0 {
		t.Fatalf("runtime execution evidence is incomplete: %#v", result)
	}
}

func TestHTTPAgentExecutorRejectsRedirectAndNon2xx(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/invoke":
			http.Redirect(response, request, "/redirected", http.StatusFound)
		case "/failed":
			http.Error(response, "failed", http.StatusBadGateway)
		default:
			fmt.Fprint(response, `{"status":"unexpected"}`)
		}
	}))
	defer server.Close()

	executor := newHTTPAgentExecutor(newAgentHTTPClient(time.Second), time.Second)
	profile := runtimeAgentProfile(server.URL)
	if _, err := executor.Execute(context.Background(), profile, runtimeAgentDispatchRequest()); err == nil {
		t.Fatal("expected redirect to be rejected")
	}

	profile.InvocationPath = "/failed"
	if _, err := executor.Execute(context.Background(), profile, runtimeAgentDispatchRequest()); err == nil {
		t.Fatal("expected non-2xx response to be rejected")
	}
}

func TestHTTPAgentExecutorRejectsOversizedAndInvalidJSONResponses(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/oversized" {
			response.Header().Set("Content-Type", "application/json")
			fmt.Fprint(response, `{"payload":"`+strings.Repeat("a", maxAgentResponseBytes)+`"}`)
			return
		}
		response.Header().Set("Content-Type", "application/json")
		fmt.Fprint(response, `{invalid-json`)
	}))
	defer server.Close()

	executor := newHTTPAgentExecutor(server.Client(), time.Second)
	profile := runtimeAgentProfile(server.URL)
	profile.InvocationPath = "/oversized"
	if _, err := executor.Execute(context.Background(), profile, runtimeAgentDispatchRequest()); err == nil {
		t.Fatal("expected oversized response to be rejected")
	}

	profile.InvocationPath = "/invalid"
	if _, err := executor.Execute(context.Background(), profile, runtimeAgentDispatchRequest()); err == nil {
		t.Fatal("expected invalid JSON response to be rejected")
	}
}

func TestHTTPAgentExecutorTimesOut(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		time.Sleep(100 * time.Millisecond)
		fmt.Fprint(response, `{}`)
	}))
	defer server.Close()

	executor := newHTTPAgentExecutor(server.Client(), 10*time.Millisecond)
	if _, err := executor.Execute(
		context.Background(),
		runtimeAgentProfile(server.URL),
		runtimeAgentDispatchRequest(),
	); err == nil {
		t.Fatal("expected runtime Agent timeout")
	}
}

func TestNewServerConfigCapsAgentExecutionTimeout(t *testing.T) {
	viper.Reset()
	t.Cleanup(viper.Reset)
	t.Setenv("AIOPS_AGENT_EXECUTION_TIMEOUT_SECONDS", "999")

	config := NewServerConfig()
	if config.AgentExecutionTimeout != 120*time.Second {
		t.Fatalf("timeout=%s want=2m0s", config.AgentExecutionTimeout)
	}
}

func runtimeAgentProfile(endpoint string) AgentProfile {
	return AgentProfile{
		Name:           "ExternalResearchAgent",
		Enabled:        true,
		Capabilities:   []string{"deployment_review"},
		BoundedActions: []string{"review_deployment_plan"},
		Endpoint:       endpoint,
		InvocationPath: "/invoke",
		AuthTokenEnv:   "EXTERNAL_RESEARCH_AGENT_TOKEN",
		Source:         agentSourceRuntime,
	}
}

func runtimeAgentDispatchRequest() AgentDispatchRequest {
	return AgentDispatchRequest{
		RunID:      "run-001",
		Agent:      "ExternalResearchAgent",
		Capability: "deployment_review",
		Action:     "review_deployment_plan",
		Input:      map[string]any{"workload": "llm-chat-inference"},
		Context:    map[string]any{"requested_by": "geon-control"},
	}
}
