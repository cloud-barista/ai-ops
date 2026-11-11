package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"kyunghee-aiops/service-control-api/internal/model"
)

func TestAIAppApplyUsesSavedOperatorApprovalAndReadsCurrentReceipt(t *testing.T) {
	cfg := testAIAppRegistryConfig(t)
	cfg.AIAppEngineApprovalToken = "fixture-apply-token"
	const flow = "approved-fixture"
	targets := []model.AIAppNodeTarget{{Kind: "node", NsID: cfg.AIAppNamespace, InfraID: "travel", NodeID: "vm1"}}
	review := &model.AIAppPlacementReview{
		NsID: cfg.AIAppNamespace, SpecID: "aws+gpu", ImageID: "aws+image", Status: "Ready",
		SpecStatus: "Available", ImageStatus: "Available", EstimatedCost: "$0.50/hour", ReviewedAt: time.Now().UTC(),
	}
	inf := model.AIAppNodeDeployment{Type: "inference", Targets: targets, PlacementReview: review,
		Inference: &model.AIAppInference{ModelID: "ministral-3-8b", ArtifactID: "hf-fp8", RuntimeID: "vllm", GPU: true,
			Args: []string{"--max-num-seqs", "16"}}}
	planned := model.AIAppNodeDeployment{Type: "app", Targets: targets, PlacementReview: review,
		App: &model.AIAppApplication{Name: "langflow", Version: 1}}
	stage := aiAppStageRecord{
		ApprovedAt: time.Now().UTC(), Request: model.AIAppStageRequest{Revision: 1, Deployment: inf}, PlannedApp: &planned,
		Receipt: &model.AIAppStageReceipt{Status: "blocked"},
	}
	if err := saveJournalRecord(cfg.path("runs", "aiapp-stage", flow+".json"), stage, true); err != nil {
		t.Fatal(err)
	}

	var mu sync.Mutex
	var calls []string
	server := mcp.NewServer(&mcp.Implementation{Name: aiAppApprovalServer, Version: "0.1.0"}, nil)
	for _, name := range []string{aiAppApplyTool, aiAppApplyReadTool} {
		grade := "write"
		if name == aiAppApplyReadTool {
			grade = "read"
		}
		server.AddTool(&mcp.Tool{
			Name: name, InputSchema: map[string]any{"type": "object"}, Meta: mcp.Meta{"ai-mcmp/grade": grade},
			Annotations: &mcp.ToolAnnotations{ReadOnlyHint: grade == "read"},
		}, func(_ context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			var args struct {
				CorrelationID string `json:"correlationId"`
				model.AIAppApplyRequest
			}
			if err := json.Unmarshal(req.Params.Arguments, &args); err != nil || args.CorrelationID != flow ||
				req.Extra.Header.Get(aiAppNamespaceHeader) != cfg.AIAppNamespace {
				t.Errorf("unexpected MCP apply input %s", req.Params.Arguments)
			}
			if name == aiAppApplyTool {
				if args.Inference.Inference == nil || !slices.Equal(args.Inference.Inference.Args, inf.Inference.Args) {
					t.Error("the engine call did not preserve the approved vLLM baseline")
				}
				got := args.Inference.PlacementReview
				if got == nil || got.SpecID != review.SpecID || got.EstimatedCost != review.EstimatedCost || !got.ReviewedAt.Equal(review.ReviewedAt) {
					t.Error("the engine call did not preserve the reviewed selection")
				}
				path := cfg.path("runs", "aiapp-apply", flow, "1.json")
				if args.Revision == 2 {
					path = cfg.path("runs", "aiapp-apply", flow, "2.json")
				}
				saved, err := loadJSON[aiAppApplyApproval](path)
				if err != nil || saved.Request.Revision != args.Revision {
					t.Error("operator approval was missing before the apply call")
				}
			}
			mu.Lock()
			calls = append(calls, name)
			mu.Unlock()
			receipt := model.AIAppApplyReceipt{
				CorrelationID: flow, Revision: args.Revision, PreviousRevision: args.Revision - 1,
				Namespace: cfg.AIAppNamespace, Target: cfg.AIAppNamespace + "/travel/vm1",
				Status: "queued", UpdatedAt: time.Now().UTC(),
			}
			if name == aiAppApplyReadTool {
				receipt.Status = "succeeded"
				receipt.Inference = model.AIAppApplyStep{Status: "succeeded", EngineRunID: "eng-inference",
					DeploymentID: "3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"}
				receipt.Application = model.AIAppApplyStep{Status: "succeeded", EngineRunID: "eng-app",
					DeploymentID: "4f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"}
			}
			body, _ := json.Marshal(receipt)
			return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(body)}}}, nil
		})
	}
	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return server }, nil)
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != aiAppApprovalMCPPath || r.Header.Get("Authorization") != "Bearer "+cfg.AIAppEngineApprovalToken {
			t.Error("AI-Ops reached the wrong approval endpoint or bearer")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		stream.ServeHTTP(w, r)
	}))
	t.Cleanup(app.Close)
	cfg.AIAppBaseURL = app.URL
	api := NewFocusedServer(cfg)
	if res := postAIAppEngineCard(t, api, cfg.AIAppEngineRegistryToken, testAIAppEngineCard()); res.Code != http.StatusCreated {
		t.Fatalf("register engine: %d %s", res.Code, res.Body)
	}
	path := pathAgentControl + "/flows/" + flow + "/aiapp/apply"
	check := func(method, path, token string, body any, status int) {
		t.Helper()
		data, _ := json.Marshal(body)
		r := httptest.NewRequest(method, path, bytes.NewReader(data))
		r.Header.Set("Authorization", "Bearer "+token)
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		api.ServeHTTP(w, r)
		if w.Code != status {
			t.Fatalf("%s %s = %d %s, want %d", method, path, w.Code, w.Body, status)
		}
	}
	planned.App.InferenceBinding = &model.AIAppInferenceBinding{BaseURLEnv: "MODEL_BASE_URL", APIKeyEnv: "MODEL_API_KEY"}
	request := model.AIAppApplyRequest{Revision: 1, Inference: inf, Application: planned}
	check(http.MethodPost, path, cfg.AIAppToken, request, http.StatusUnauthorized)
	changed := request
	changedInference := *request.Inference.Inference
	changedInference.Args = []string{"--max-num-seqs", "32"}
	changed.Inference.Inference = &changedInference
	check(http.MethodPost, path, cfg.AutonomyAdminToken, changed, http.StatusConflict)
	request.Application.App.Version = 2
	check(http.MethodPost, path, cfg.AutonomyAdminToken, request, http.StatusConflict)
	request.Application.App.Version = 1
	check(http.MethodPost, path, cfg.AutonomyAdminToken, request, http.StatusAccepted)
	check(http.MethodGet, path+"/1", cfg.AutonomyAdminToken, nil, http.StatusOK)
	request.Revision, request.PreviousRevision = 2, 1
	request.Application.App.Version = 2
	request.Inference.PlacementReview, request.Application.PlacementReview = nil, nil
	check(http.MethodPost, path, cfg.AutonomyAdminToken, request, http.StatusConflict)
	request.Inference.PlacementReview, request.Application.PlacementReview = review, review
	check(http.MethodPost, path, cfg.AutonomyAdminToken, request, http.StatusAccepted)
	request.Application.App.Version = 3
	check(http.MethodPost, path, cfg.AutonomyAdminToken, request, http.StatusConflict)
	mu.Lock()
	defer mu.Unlock()
	want := []string{aiAppApplyTool, aiAppApplyReadTool, aiAppApplyReadTool, aiAppApplyTool}
	if !slices.Equal(calls, want) {
		t.Fatalf("approval tool calls = %v, want %v", calls, want)
	}
}
