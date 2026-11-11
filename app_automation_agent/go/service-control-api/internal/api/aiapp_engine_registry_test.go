package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"testing"
)

func testAIAppEngineCard() AIAppEngineCard {
	card := AIAppEngineCard{
		Name: aiAppEngineName, DisplayName: "AI 응용 관리 자동화 에이전트",
		Version: "0.1.0", Owner: "innogrid", Framework: "AI-Ops", ServesFramework: "AI-App",
		Workflows: []string{"deploy", "heal", "control", "status"}, WorkflowEndpoint: aiAppEngineWorkflowPath,
		MCPEndpoint: aiAppEngineMCPPath, DryRun: true,
		Tools: AIAppEngineCardTools{
			Exposed: []AIAppEngineCardTool{
				{Name: "list_applications", Grade: "read"},
				{Name: "application_status", Grade: "read"},
				{Name: "deploy_application", Grade: "write", GuardedByDryRun: true},
			},
			Uses: []AIAppEngineCardTool{{Name: "get_deployment"}},
		},
	}
	card.MCP.AppServer.URL = "http://ai-app:8090/aiapp/mcp"
	return card
}

func postAIAppEngineCard(t *testing.T, server http.Handler, token string, card AIAppEngineCard) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(card)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, aiAppEngineRegistryPath, bytes.NewReader(body))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	server.ServeHTTP(res, req)
	return res
}

func testAIAppRegistryConfig(t *testing.T) ServerConfig {
	t.Helper()
	config := NewServerConfig()
	config.RepoRoot = t.TempDir()
	config.AIAppBaseURL, config.AIAppNamespace, config.AIAppToken = "http://127.0.0.1:8090/", "khu-test", "app-token"
	config.AIAppEngineRegistryToken = "registry-token"
	config.LLMOpBaseURL, config.OpsLLMToken = "http://127.0.0.1:18110", "ops-token"
	config.AutonomyAdminToken = "admin-token"
	if err := saveJournalRecord(config.path("config", "agent_registry.json"), AgentRegistry{
		Agents: []AgentProfile{{Name: "LocalDecisionAgent", BoundedActions: []string{"plan_deployment"}}},
	}, false); err != nil {
		t.Fatal(err)
	}
	return config
}

func TestAIAppEngineCardRegistrationPersistsAndScopesReads(t *testing.T) {
	config := testAIAppRegistryConfig(t)
	server := NewFocusedServer(config)
	card := testAIAppEngineCard()
	for _, token := range []string{"", config.AIAppToken, config.AutonomyAdminToken} {
		if res := postAIAppEngineCard(t, server, token, card); res.Code != http.StatusUnauthorized {
			t.Fatalf("unexpected registration response: status=%d body=%s", res.Code, res.Body.String())
		}
	}
	for _, mutate := range []func(*AIAppEngineCard){
		func(card *AIAppEngineCard) { card.ServesFramework = "AI-Infra" },
		func(card *AIAppEngineCard) { card.MCPEndpoint = "//foreign.example/mcp" },
		func(card *AIAppEngineCard) { card.MCP.AppServer.URL = "http://user:secret@ai-app:8090/aiapp/mcp" },
	} {
		invalid := card
		mutate(&invalid)
		if res := postAIAppEngineCard(t, server, config.AIAppEngineRegistryToken, invalid); res.Code != http.StatusBadRequest ||
			strings.Contains(res.Body.String(), "secret") {
			t.Fatalf("invalid engine card: status=%d body=%s", res.Code, res.Body.String())
		}
	}
	card.Tools.Exposed = append(card.Tools.Exposed, AIAppEngineCardTool{Name: "control_application", Grade: "read"})
	created := postAIAppEngineCard(t, server, config.AIAppEngineRegistryToken, card)
	var profile AgentProfile
	if created.Code != http.StatusCreated || json.Unmarshal(created.Body.Bytes(), &profile) != nil ||
		profile.Owner != "innogrid" || profile.Framework != "AI-Ops" || profile.ServesFramework != "AI-App" ||
		profile.Namespace != config.AIAppNamespace || profile.Endpoint != "http://127.0.0.1:8090" ||
		profile.InvocationPath != aiAppEngineMCPPath || !slices.Equal(profile.BoundedActions, []string{"application_status", "list_applications"}) {
		t.Fatalf("registered profile: status=%d body=%s", created.Code, created.Body.String())
	}
	replayed := postAIAppEngineCard(t, server, config.AIAppEngineRegistryToken, card)
	if replayed.Code != http.StatusOK || replayed.Body.String() != created.Body.String() {
		t.Fatalf("registration replay changed the record: status=%d body=%s", replayed.Code, replayed.Body.String())
	}
	record, err := os.ReadFile(config.aiAppEngineRegistryFile())
	if err != nil || bytes.Contains(record, []byte(config.AIAppEngineRegistryToken)) || bytes.Contains(record, []byte(config.AIAppToken)) {
		t.Fatalf("stored engine record was unreadable or contained a token: %v", err)
	}

	restarted := NewFocusedServer(config)
	res := performJSONRequest(t, restarted, http.MethodGet, pathAgents+"/"+aiAppEngineName, "")
	if res.Code != http.StatusOK || res.Body.String() != created.Body.String() {
		t.Fatalf("restore engine: status=%d body=%s", res.Code, res.Body.String())
	}
	res = performJSONRequest(t, restarted, http.MethodGet, pathAgents, "")
	var listing struct {
		Agents                 []AgentProfile `json:"agents"`
		EligibleDecisionAgents []AgentProfile `json:"eligible_decision_agents"`
	}
	if res.Code != http.StatusOK || json.Unmarshal(res.Body.Bytes(), &listing) != nil ||
		!slices.ContainsFunc(listing.Agents, func(agent AgentProfile) bool { return agent.Name == aiAppEngineName }) ||
		slices.ContainsFunc(listing.EligibleDecisionAgents, func(agent AgentProfile) bool { return agent.Name == aiAppEngineName }) {
		t.Fatalf("engine registry list: status=%d body=%s", res.Code, res.Body.String())
	}
	res = performJSONRequest(t, restarted, http.MethodGet, aiAppIntentPrefix+"/readyz", "")
	if res.Code != http.StatusOK {
		t.Fatalf("restored registry readiness: status=%d body=%s", res.Code, res.Body.String())
	}
	config.AIAppNamespace = "different"
	changed := NewFocusedServer(config)
	res = performJSONRequest(t, changed, http.MethodGet, aiAppIntentPrefix+"/readyz", "")
	if res.Code != http.StatusServiceUnavailable || !strings.Contains(res.Body.String(), "AIAPP_ENGINE_NOT_REGISTERED") {
		t.Fatalf("restored registration widened its namespace: status=%d body=%s", res.Code, res.Body.String())
	}

	for _, token := range []string{config.AIAppEngineRegistryToken, config.AutonomyAdminToken} {
		body := `{"name":"` + aiAppEngineName + `","version":"1","role":"engine","capabilities":["deployment"],"bounded_actions":["deploy_application"],"endpoint":"https://other.example","invocation_path":"/deploy"}`
		req := httptest.NewRequest(http.MethodPost, pathAgents, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		res = httptest.NewRecorder()
		restarted.ServeHTTP(res, req)
		want := http.StatusUnauthorized
		if token == config.AutonomyAdminToken {
			want = http.StatusBadRequest
		}
		if res.Code != want {
			t.Fatalf("generic registration of reserved name: status=%d body=%s", res.Code, res.Body.String())
		}
	}
}

func TestAIAppEngineRegistryFailsClosedOnStorageErrors(t *testing.T) {
	config := testAIAppRegistryConfig(t)
	server := NewFocusedServer(config)
	card := testAIAppEngineCard()
	if err := os.WriteFile(config.path("runs"), []byte("not a directory"), 0600); err != nil {
		t.Fatal(err)
	}
	res := postAIAppEngineCard(t, server, config.AIAppEngineRegistryToken, card)
	if res.Code != http.StatusInternalServerError || !strings.Contains(res.Body.String(), "ENGINE_REGISTRATION_SAVE_FAILED") {
		t.Fatalf("registration with unwritable destination: status=%d body=%s", res.Code, res.Body.String())
	}
	if res := performJSONRequest(t, server, http.MethodGet, aiAppIntentPrefix+"/readyz", ""); res.Code != http.StatusServiceUnavailable {
		t.Fatalf("unsaved registration became ready: status=%d body=%s", res.Code, res.Body.String())
	}
	if err := os.Remove(config.path("runs")); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(config.path("runs"), 0700); err != nil {
		t.Fatal(err)
	}
	const damaged = `{"schema_version":`
	if err := os.WriteFile(config.aiAppEngineRegistryFile(), []byte(damaged), 0600); err != nil {
		t.Fatal(err)
	}
	restarted := NewFocusedServer(config)
	res = postAIAppEngineCard(t, restarted, config.AIAppEngineRegistryToken, card)
	if res.Code != http.StatusServiceUnavailable || !strings.Contains(res.Body.String(), "AIAPP_ENGINE_REGISTRY_INVALID") {
		t.Fatalf("registration over damaged data: status=%d body=%s", res.Code, res.Body.String())
	}
	res = performJSONRequest(t, restarted, http.MethodGet, aiAppIntentPrefix+"/readyz", "")
	if res.Code != http.StatusServiceUnavailable || !strings.Contains(res.Body.String(), "AIAPP_ENGINE_REGISTRY_INVALID") {
		t.Fatalf("damaged registry became ready: status=%d body=%s", res.Code, res.Body.String())
	}
	record, err := os.ReadFile(config.aiAppEngineRegistryFile())
	if err != nil || string(record) != damaged {
		t.Fatalf("damaged record was changed: %v", err)
	}
}
