package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestAIAppIntentRunsReadToolsAndRecordsWriteProposals(t *testing.T) {
	const id = "3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
	const foreignID = "4f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
	const progress = `{"id":"` + id + `","status":"running","nodes":[{"target":{"nsId":"khu-test"}}],"request":{"env":{"TOKEN":"sensitive-value"}}}`

	var opsCalls, recoveryReads atomic.Int32
	var lostRunID atomic.Value
	router := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer ops-token" {
			t.Error("missing OpsLLM credential")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if r.Method == http.MethodGet {
			id, _ := lostRunID.Load().(string)
			if id == "" || r.URL.Path != "/api/v1/ops-llm/runs/"+id {
				t.Errorf("unexpected OpsLLM run lookup: %s", r.URL.Path)
				w.WriteHeader(http.StatusNotFound)
				return
			}
			recoveryReads.Add(1)
			_ = json.NewEncoder(w).Encode(AIAppOpsLLMRun{RunID: id, Status: "completed", LLMType: "open_weights",
				ActualModel: "model-test", Proposal: &AIAppIntentProposal{Action: "deploy_application"}})
			return
		}
		opsCalls.Add(1)
		if r.URL.Path != "/api/v1/ops-llm/interpret" {
			t.Errorf("unexpected OpsLLM request: %s", r.URL.Path)
			w.WriteHeader(http.StatusForbidden)
			return
		}
		var request struct {
			Intent  string `json:"intent"`
			LLMType string `json:"llm_type"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || request.LLMType != "open_weights" {
			t.Errorf("bad OpsLLM request: %#v, err=%v", request, err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if strings.Contains(request.Intent, "응답 유실") {
			lostRunID.Store(r.Header.Get("X-AIMCMP-OpsLLM-Run-ID"))
			conn, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Errorf("close interpretation response: %v", err)
				return
			}
			_ = conn.Close()
			return
		}
		proposal := AIAppIntentProposal{Action: "list_applications"}
		switch {
		case strings.Contains(request.Intent, foreignID):
			proposal = AIAppIntentProposal{Action: "application_status", DeploymentID: foreignID}
		case strings.Contains(request.Intent, id):
			proposal = AIAppIntentProposal{Action: "application_status", DeploymentID: id}
		case strings.Contains(request.Intent, "배포해"):
			proposal.Action = "deploy_application"
		}
		_ = json.NewEncoder(w).Encode(AIAppOpsLLMRun{
			RunID: "opsllm-test", Status: "completed", LLMType: request.LLMType, CandidateID: "public",
			ActualModel: "model-test", LatencyMS: 8, Proposal: &proposal,
		})
	}))
	defer router.Close()

	var toolCalls atomic.Int32
	engine := mcp.NewServer(&mcp.Implementation{Name: aiAppEngineName, Version: "0.1.0"}, nil)
	addReadTool := func(name, output string) {
		engine.AddTool(&mcp.Tool{
			Name:        name,
			InputSchema: map[string]any{"type": "object"},
			Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
			Meta:        mcp.Meta{"ai-mcmp/grade": "read"},
		}, func(_ context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			toolCalls.Add(1)
			if req.Extra.Header.Get(aiAppNamespaceHeader) != "khu-test" {
				t.Error("engine request lacked the scoped namespace")
			}
			if name == "application_status" {
				var input map[string]string
				if err := json.Unmarshal(req.Params.Arguments, &input); err != nil || input["deploymentId"] != id {
					t.Errorf("unexpected status arguments: %s", req.Params.Arguments)
				}
			}
			return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: output}}}, nil
		})
	}
	addReadTool("list_applications", `{"models":[{"id":"qwen","name":"Qwen"}],"apps":[{"name":"chat","versions":[1,2]}]}`)
	addReadTool("application_status", `{"runId":"eng-test","nsId":"khu-test","status":"succeeded","dryRun":true,"steps":[{"tool":"get_deployment","output":`+progress+`}]}`)

	mux := http.NewServeMux()
	mux.Handle(aiAppEngineMCPPath, mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return engine }, nil))
	mux.HandleFunc("GET /aiapp/deployments/"+id+"/progress", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(progress))
	})
	mux.HandleFunc("GET /aiapp/deployments/"+foreignID+"/progress", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintf(w, `{"id":"%s","status":"running","nodes":[{"target":{"nsId":"other"}}]}`, foreignID)
	})
	aiApp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer app-token" {
			t.Error("AI-App request lacked the configured bearer token")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		mux.ServeHTTP(w, r)
	}))
	defer aiApp.Close()

	config := NewServerConfig()
	config.RepoRoot = t.TempDir()
	config.AIAppBaseURL, config.AIAppNamespace, config.AIAppToken = aiApp.URL, "khu-test", "app-token"
	config.AIAppEngineRegistryToken = "registry-token"
	config.LLMOpBaseURL, config.OpsLLMToken = router.URL, "ops-token"
	config.AgentExecutionTimeout = 5 * time.Second
	server := NewFocusedServer(config)
	intent := func(server http.Handler, text string) *httptest.ResponseRecorder {
		body, _ := json.Marshal(AIAppIntentRequest{Intent: text, LLMType: "open_weights"})
		req := httptest.NewRequest(http.MethodPost, aiAppIntentPrefix+"/ns/khu-test/intents", strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer app-token")
		res := httptest.NewRecorder()
		server.ServeHTTP(res, req)
		return res
	}
	if res := intent(server, id+" 상태"); res.Code != http.StatusServiceUnavailable || opsCalls.Load() != 0 {
		t.Fatalf("intent before registration: status=%d body=%s", res.Code, res.Body.String())
	}
	card := testAIAppEngineCard()
	if res := postAIAppEngineCard(t, server, config.AIAppEngineRegistryToken, card); res.Code != http.StatusCreated {
		t.Fatalf("register engine: status=%d body=%s", res.Code, res.Body.String())
	}
	server = NewFocusedServer(config)

	var statusRunID string
	for _, tc := range []struct {
		intent, status, tool string
		executed             bool
	}{
		{"응용 목록", "succeeded", "list_applications", true},
		{id + " 상태", "succeeded", "application_status", true},
		{foreignID + " 상태", "blocked", "application_status", false},
		{"새 응용을 배포해", "blocked", "deploy_application", false},
	} {
		res := intent(server, tc.intent)
		var run AIAppIntentRun
		if res.Code != http.StatusOK || json.Unmarshal(res.Body.Bytes(), &run) != nil || run.Status != tc.status ||
			run.Caller != "ai-app-gateway" || run.OpsLLM == nil || run.OpsLLM.RunID != "opsllm-test" || len(run.Steps) != 1 ||
			run.Steps[0].Tool != tc.tool || run.Steps[0].Executed != tc.executed ||
			strings.Contains(res.Body.String(), "sensitive-value") {
			t.Fatalf("%s: status=%d body=%s", tc.intent, res.Code, res.Body.String())
		}
		if tc.tool == "application_status" && tc.executed {
			if run.Steps[0].EngineRunID != "eng-test" || run.Steps[0].Agent != aiAppEngineName || !run.DryRun {
				t.Fatalf("missing engine evidence: %s", res.Body.String())
			}
			statusRunID = run.RunID
		}
	}
	if calls := toolCalls.Load(); calls != 2 {
		t.Fatalf("engine called %d times, expected only the two authorized reads", calls)
	}
	t.Run("recovers a lost interpretation response as a proposal", func(t *testing.T) {
		beforeOps := opsCalls.Load()
		res := intent(server, "응답 유실 시험으로 새 응용을 배포해")
		var run AIAppIntentRun
		if res.Code != http.StatusOK || json.Unmarshal(res.Body.Bytes(), &run) != nil ||
			run.Status != "blocked" || run.OpsLLM == nil || run.OpsLLM.Status != "completed" ||
			run.OpsLLM.RunID != "opsllm-"+run.RunID[strings.LastIndex(run.RunID, "-")+1:] ||
			len(run.Steps) != 1 || run.Steps[0].Executed || run.Steps[0].Tool != "deploy_application" {
			t.Fatalf("recovered proposal: %d %s", res.Code, res.Body)
		}
		if opsCalls.Load() != beforeOps+1 || recoveryReads.Load() != 1 || toolCalls.Load() != 2 {
			t.Fatal("recovery must read the saved result once and leave the proposal unexecuted")
		}
	})

	var mcpRunID string
	t.Run("MCP agent uses the same intent and run store", func(t *testing.T) {
		beforeOps := opsCalls.Load()
		for _, tc := range []struct {
			token, namespace, origin string
			status                   int
		}{
			{"", "khu-test", "", http.StatusUnauthorized},
			{"app-token", "", "", http.StatusForbidden},
			{"app-token", "other", "", http.StatusForbidden},
			{"app-token", "khu-test", "https://untrusted.example", http.StatusForbidden},
		} {
			body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_application_intent","arguments":{"intent":"응용 목록","llmType":"open_weights"}}}`
			req := httptest.NewRequest(http.MethodPost, aiAppIntentMCPPath, strings.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Accept", "application/json, text/event-stream")
			if tc.token != "" {
				req.Header.Set("Authorization", "Bearer "+tc.token)
			}
			req.Header.Set(aiAppNamespaceHeader, tc.namespace)
			req.Header.Set("Origin", tc.origin)
			res := httptest.NewRecorder()
			server.ServeHTTP(res, req)
			if res.Code != tc.status || opsCalls.Load() != beforeOps {
				t.Fatalf("unauthorized MCP tool call: status=%d body=%s", res.Code, res.Body.String())
			}
		}

		endpoint := httptest.NewServer(server)
		defer endpoint.Close()
		httpClient := partnerHTTPClient(config.AgentExecutionTimeout)
		httpClient.Transport = aiAppEngineHeaders{namespace: config.AIAppNamespace, token: config.AIAppToken}
		ctx, cancel := context.WithTimeout(context.Background(), config.AgentExecutionTimeout)
		defer cancel()
		client := mcp.NewClient(&mcp.Implementation{Name: "aiops-mcp-test", Version: "0.1.0"}, nil)
		session, err := client.Connect(ctx, &mcp.StreamableClientTransport{
			Endpoint: endpoint.URL + aiAppIntentMCPPath, HTTPClient: httpClient, DisableStandaloneSSE: true,
		}, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer session.Close()
		if serverInfo := session.InitializeResult().ServerInfo; serverInfo == nil || serverInfo.Name != aiAppIntentSource {
			t.Fatalf("unexpected MCP server identity: %+v", serverInfo)
		}
		tools, err := session.ListTools(ctx, nil)
		if err != nil || len(tools.Tools) != 2 {
			t.Fatalf("unexpected tool discovery: %+v, err=%v", tools, err)
		}
		for _, tool := range tools.Tools {
			switch tool.Name {
			case "get_application_intent_run":
				if tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
					t.Fatalf("run lookup must be marked read-only: %+v", tool)
				}
			case "run_application_intent":
				if tool.Annotations == nil || tool.Annotations.ReadOnlyHint {
					t.Fatalf("intent execution writes a run record: %+v", tool)
				}
			default:
				t.Fatalf("unexpected MCP tool: %s", tool.Name)
			}
		}

		callRun := func(name string, arguments any) AIAppIntentRun {
			t.Helper()
			result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: arguments})
			if err != nil || result.IsError {
				t.Fatalf("%s: result=%+v err=%v", name, result, err)
			}
			body, err := json.Marshal(result.StructuredContent)
			var run AIAppIntentRun
			if err != nil || json.Unmarshal(body, &run) != nil || run.RunID == "" || strings.Contains(string(body), "sensitive-value") {
				t.Fatalf("%s: invalid or unsafe run %s", name, body)
			}
			return run
		}
		restRun := callRun("get_application_intent_run", map[string]string{"runId": statusRunID})
		if restRun.RunID != statusRunID || restRun.Caller != "ai-app-gateway" {
			t.Fatalf("MCP failed to find the REST run: %+v", restRun)
		}
		read := callRun("run_application_intent", AIAppIntentRequest{Intent: id + " 상태", LLMType: "open_weights"})
		if read.Status != "succeeded" || read.Caller != "ai-app-mcp" || len(read.Steps) != 1 ||
			!read.Steps[0].Executed || read.Steps[0].EngineRunID != "eng-test" {
			t.Fatalf("MCP status lookup failed: %+v", read)
		}
		mcpRunID = read.RunID
		proposal := callRun("run_application_intent", AIAppIntentRequest{Intent: "새 응용을 배포해", LLMType: "open_weights"})
		if proposal.Status != "blocked" || len(proposal.Steps) != 1 || proposal.Steps[0].Executed || toolCalls.Load() != 3 {
			t.Fatalf("MCP write intent escaped the proposal boundary: %+v", proposal)
		}
	})

	engineReads := toolCalls.Load()
	card.Workflows = []string{"deploy", "heal", "control"}
	if res := postAIAppEngineCard(t, server, config.AIAppEngineRegistryToken, card); res.Code != http.StatusOK {
		t.Fatalf("update engine card: status=%d body=%s", res.Code, res.Body.String())
	}
	res := intent(server, id+" 상태")
	var blocked AIAppIntentRun
	if res.Code != http.StatusOK || json.Unmarshal(res.Body.Bytes(), &blocked) != nil ||
		blocked.Status != "blocked" || len(blocked.Steps) != 1 || blocked.Steps[0].Error == "" || toolCalls.Load() != engineReads {
		t.Fatalf("unadvertised status workflow: status=%d body=%s", res.Code, res.Body.String())
	}

	restarted := NewFocusedServer(config)
	for _, tc := range []struct {
		path, bearer, runID string
		status              int
	}{
		{"/runs/" + statusRunID, "Bearer app-token", statusRunID, http.StatusOK},
		{"/runs/" + mcpRunID, "Bearer app-token", mcpRunID, http.StatusOK},
		{"/runs", "Bearer app-token", statusRunID, http.StatusOK},
		{"/runs/" + statusRunID, "", statusRunID, http.StatusUnauthorized},
	} {
		req := httptest.NewRequest(http.MethodGet, aiAppIntentPrefix+tc.path, nil)
		req.Header.Set("Authorization", tc.bearer)
		res := httptest.NewRecorder()
		restarted.ServeHTTP(res, req)
		if res.Code != tc.status || tc.status == http.StatusOK && !strings.Contains(res.Body.String(), tc.runID) {
			t.Fatalf("%s: status=%d body=%s", tc.path, res.Code, res.Body.String())
		}
	}
	req := httptest.NewRequest(http.MethodPost, aiAppIntentPrefix+"/ns/other/intents", strings.NewReader(`{"intent":"응용 목록"}`))
	req.Header.Set("Authorization", "Bearer app-token")
	res = httptest.NewRecorder()
	server.ServeHTTP(res, req)
	if res.Code != http.StatusForbidden {
		t.Fatalf("foreign namespace accepted: status=%d body=%s", res.Code, res.Body.String())
	}
}
