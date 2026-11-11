package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"kyunghee-aiops/service-control-api/internal/model"
)

func TestAIAppStageRequiresOperatorAndExactApprovedRequest(t *testing.T) {
	cfg := testAIAppRegistryConfig(t)
	cfg.AIAppEngineApprovalToken = "stage-test-token"
	decisionRegistry, err := loadJSON[AgentRegistry](NewServerConfig().path("config", "agent_registry.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := saveJournalRecord(cfg.path("config", "agent_registry.json"), decisionRegistry, false); err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	mcpServer := mcp.NewServer(&mcp.Implementation{Name: aiAppApprovalServer, Version: "0.1.0"}, nil)
	mcpServer.AddReceivingMiddleware(func(next mcp.MethodHandler) mcp.MethodHandler {
		return func(ctx context.Context, method string, req mcp.Request) (mcp.Result, error) {
			result, err := next(ctx, method, req)
			if init, ok := result.(*mcp.InitializeResult); ok {
				init.Meta = mcp.Meta{"ai-mcmp/dryRun": true}
			}
			return result, err
		}
	})
	mcpServer.AddTool(&mcp.Tool{
		Name: aiAppStageTool, InputSchema: map[string]any{"type": "object"}, Meta: mcp.Meta{"ai-mcmp/grade": "write"},
	}, func(_ context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		attempt := calls.Add(1)
		var got struct {
			CorrelationID string `json:"correlationId"`
			model.AIAppStageRequest
		}
		if err := json.Unmarshal(req.Params.Arguments, &got); err != nil || got.CorrelationID != "flow-focused-001" ||
			got.Revision != 1 || len(got.Deployment.Targets) != 1 || got.Deployment.Targets[0].NsID != cfg.AIAppNamespace ||
			got.Deployment.Inference == nil || got.Deployment.Inference.ModelID != "ministral-3-8b" ||
			!reflect.DeepEqual(got.Deployment.Inference.Args, []string{"--max-num-seqs", "16"}) {
			t.Errorf("unexpected approval tool arguments %s", req.Params.Arguments)
		}
		receipt := `{"correlationId":"flow-focused-001","revision":1,"nsId":"khu-test","target":"khu-test/travel/vm1",
			"status":"blocked","engineRunId":"eng-stage","deploymentId":"3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48",
			"updatedAt":"2026-11-10T05:00:00Z"}`
		if attempt == 1 {
			receipt = `{"correlationId":"flow-focused-001","revision":1,"nsId":"khu-test","target":"khu-test/travel/vm1",
				"status":"confirmation_pending","updatedAt":"2026-11-10T04:59:00Z"}`
		}
		return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: receipt}}}, nil
	})
	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return mcpServer }, nil)
	aiApp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != aiAppApprovalMCPPath || r.Header.Get("Authorization") != "Bearer "+cfg.AIAppEngineApprovalToken ||
			r.Header.Get(aiAppNamespaceHeader) != cfg.AIAppNamespace {
			t.Errorf("approval used incorrect MCP path, authorization or scope")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		stream.ServeHTTP(w, r)
	}))
	defer aiApp.Close()
	cfg.AIAppBaseURL = aiApp.URL
	server := NewFocusedServer(cfg)
	if res := postAIAppEngineCard(t, server, cfg.AIAppEngineRegistryToken, testAIAppEngineCard()); res.Code != http.StatusCreated {
		t.Fatalf("register engine: %d %s", res.Code, res.Body)
	}
	plan := focusedPlanningRequest()
	plan.ApplicationProfile.AppVersion = "2"
	plan.ResourceRecommendation.Recommendations[0].Resource.ResourceID = "khu-test/travel/vm1"
	body, _ := json.Marshal(plan)
	if res := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body)); res.Code != http.StatusCreated {
		t.Fatalf("create plan: %d %s", res.Code, res.Body)
	}
	path := pathAgentControl + "/flows/" + plan.CorrelationID + "/aiapp"
	res := performJSONRequest(t, server, http.MethodPost, path+"/prepare", `{"model_id":"ministral-3-8b","artifact_id":"hf-fp8","max_num_seqs":16}`)
	var prepared model.AIAppPreparation
	if err := json.Unmarshal(res.Body.Bytes(), &prepared); res.Code != http.StatusOK || err != nil {
		t.Fatalf("prepare: %d %s", res.Code, res.Body)
	}
	request := model.AIAppStageRequest{Revision: prepared.Revision, Deployment: prepared.Deployments[0]}
	postStage := func(token string, body model.AIAppStageRequest) *httptest.ResponseRecorder {
		t.Helper()
		encoded, _ := json.Marshal(body)
		r := httptest.NewRequest(http.MethodPost, path+"/stage", bytes.NewReader(encoded))
		r.RemoteAddr = "127.0.0.1:12345"
		r.Header.Set("Authorization", "Bearer "+token)
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		server.ServeHTTP(w, r)
		return w
	}
	if res := postStage(cfg.AIAppToken, request); res.Code != http.StatusUnauthorized {
		t.Fatalf("gateway token staged an approval: %d %s", res.Code, res.Body)
	}
	wrong := request
	wrong.Revision++
	if res := postStage(cfg.AutonomyAdminToken, wrong); res.Code != http.StatusConflict {
		t.Fatalf("wrong revision: %d %s", res.Code, res.Body)
	}
	wrong = request
	wrongInference := *request.Deployment.Inference
	wrongInference.Args = []string{"--port", "16"}
	wrong.Deployment.Inference = &wrongInference
	if res := postStage(cfg.AutonomyAdminToken, wrong); res.Code != http.StatusConflict || calls.Load() != 0 {
		t.Fatalf("unsupported runtime arguments: %d %s", res.Code, res.Body)
	}
	first := postStage(cfg.AutonomyAdminToken, request)
	var pending model.AIAppStageReceipt
	if first.Code != http.StatusOK || json.Unmarshal(first.Body.Bytes(), &pending) != nil || pending.Status != "confirmation_pending" {
		t.Fatalf("pending stage: %d %s", first.Code, first.Body)
	}
	resumed := postStage(cfg.AutonomyAdminToken, request)
	again := postStage(cfg.AutonomyAdminToken, request)
	if resumed.Code != http.StatusOK || again.Code != http.StatusOK || resumed.Body.String() != again.Body.String() || calls.Load() != 2 {
		t.Fatalf("stage recovery/replay statuses %d, %d and %d MCP calls, resumed body %s", resumed.Code, again.Code, calls.Load(), resumed.Body)
	}
	record, err := loadJSON[aiAppStageRecord](cfg.path("runs", "aiapp-stage", plan.CorrelationID+".json"))
	if err != nil || !reflect.DeepEqual(record.Request, request) || record.Receipt == nil || record.Receipt.Status != "blocked" {
		t.Fatalf("approval journal = %+v, %v", record, err)
	}
	request.Deployment.Inference.Args = []string{"--max-num-seqs", "32"}
	if res := postStage(cfg.AutonomyAdminToken, request); res.Code != http.StatusConflict || calls.Load() != 2 {
		t.Fatalf("changed approval: %d %s with %d calls", res.Code, res.Body, calls.Load())
	}
}
