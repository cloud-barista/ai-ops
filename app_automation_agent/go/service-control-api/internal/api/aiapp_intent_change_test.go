package api

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"kyunghee-aiops/service-control-api/internal/model"
)

const (
	testIntentOperator = "fixture-scoped-operator"
	testIntentReader   = "fixture-scoped-reader"
	testIntentAppID    = "4f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
	testIntentInferID  = "3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
)

func scopedIntentTestConfig(t *testing.T) ServerConfig {
	t.Helper()
	cfg := testAIAppRegistryConfig(t)
	cfg.AIAppEngineApprovalToken = "fixture-apply-token"
	cfg.FrameworkAccessPolicyFile = cfg.path("config", "framework-access.json")
	t.Setenv("AIOPS_PRINCIPAL_TEST_OPERATOR_TOKEN", testIntentOperator)
	t.Setenv("AIOPS_PRINCIPAL_TEST_READER_TOKEN", testIntentReader)
	t.Setenv("AIOPS_PRINCIPAL_TEST_FOREIGN_TOKEN", "fixture-other-framework")
	policy := frameworkAccessPolicy{Version: 1, Principals: []frameworkPrincipal{
		{Name: "operator", TokenEnv: "AIOPS_PRINCIPAL_TEST_OPERATOR_TOKEN", Grants: []frameworkGrant{
			{Agent: aiAppEngineName, Framework: "AI-App", Namespace: cfg.AIAppNamespace,
				Actions: []string{"interpret", "deploy_application", "update_application", "control_application.suspend",
					"control_application.resume", "control_application.terminate", "remove_application"}},
		}},
		{Name: "reader", TokenEnv: "AIOPS_PRINCIPAL_TEST_READER_TOKEN", Grants: []frameworkGrant{
			{Agent: aiAppEngineName, Framework: "AI-App", Namespace: cfg.AIAppNamespace, Actions: []string{"interpret", "list_applications"}},
		}},
		{Name: "foreign", TokenEnv: "AIOPS_PRINCIPAL_TEST_FOREIGN_TOKEN", Grants: []frameworkGrant{
			{Agent: aiAppEngineName, Framework: "AI-Infra", Namespace: cfg.AIAppNamespace, Actions: []string{"control_application.suspend"}},
		}},
	}}
	if err := saveJournalRecord(cfg.FrameworkAccessPolicyFile, policy, false); err != nil {
		t.Fatal(err)
	}
	return cfg
}

func saveTestIntent(t *testing.T, cfg ServerConfig, action, deploymentID, control string) string {
	t.Helper()
	id := "nl-1-" + strings.ToLower(rand.Text())
	tool := action
	if action == "update_application" {
		tool = "deploy_application"
	}
	run := AIAppIntentRun{
		RunID: id, Source: aiAppIntentSource, Kind: "intent", Caller: "ai-app-gateway", Namespace: cfg.AIAppNamespace, Status: "blocked",
		Steps: []AIAppIntentStep{{Tool: tool, Grade: "write", Input: map[string]string{"deploymentId": deploymentID, "action": control}}},
		OpsLLM: &AIAppOpsLLMRun{RunID: "opsllm-fixture", Status: "completed", Proposal: &AIAppIntentProposal{
			Action: action, DeploymentID: deploymentID, ControlAction: control,
		}},
	}
	h := restHandler{config: cfg}
	if err := saveJournalRecord(h.aiAppIntentPath(id), run, false); err != nil {
		t.Fatal(err)
	}
	return id
}

func intentChangeTestRequest(t *testing.T, api http.Handler, method, path, token string, input, output any, status int) {
	t.Helper()
	body, err := json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(method, path, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	api.ServeHTTP(res, req)
	if res.Code != status {
		t.Fatalf("%s %s = %d %s, want %d", method, path, res.Code, res.Body.String(), status)
	}
	if output != nil && json.Unmarshal(res.Body.Bytes(), output) != nil {
		t.Fatalf("unreadable response %s", res.Body.String())
	}
}

func testIntentMCPServer(name string) *mcp.Server {
	server := mcp.NewServer(&mcp.Implementation{Name: name, Version: "0.1.0"}, nil)
	server.AddReceivingMiddleware(func(next mcp.MethodHandler) mcp.MethodHandler {
		return func(ctx context.Context, method string, req mcp.Request) (mcp.Result, error) {
			result, err := next(ctx, method, req)
			if initialized, ok := result.(*mcp.InitializeResult); ok {
				initialized.Meta = mcp.Meta{"ai-mcmp/dryRun": false}
			}
			return result, err
		}
	})
	return server
}

func testIntentTool(server *mcp.Server, name, grade string, call func(*mcp.CallToolRequest) any) {
	destructive := grade == "destructive"
	server.AddTool(&mcp.Tool{
		Name: name, InputSchema: map[string]any{"type": "object"},
		Meta:        mcp.Meta{"ai-mcmp/grade": grade, "ai-mcmp/guardedByDryRun": grade != "read"},
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: grade == "read", DestructiveHint: &destructive},
	}, func(_ context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		body, err := json.Marshal(call(req))
		if err != nil {
			return nil, err
		}
		return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(body)}}}, nil
	})
}

func TestScopedAIAppIntentControlAndRemovalUseExactTools(t *testing.T) {
	cfg := scopedIntentTestConfig(t)
	router := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/ops-llm/interpret" || r.Header.Get("Authorization") != "Bearer "+cfg.OpsLLMToken {
			t.Error("wrong OpsLLM path or credential")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		_ = json.NewEncoder(w).Encode(AIAppOpsLLMRun{RunID: "opsllm-scoped-read", Status: "completed",
			Proposal: &AIAppIntentProposal{Action: "application_status", DeploymentID: testIntentAppID}})
	}))
	defer router.Close()
	cfg.LLMOpBaseURL = router.URL
	target := model.AIAppNodeTarget{Kind: "node", NsID: cfg.AIAppNamespace, InfraID: "travel", NodeID: "vm1"}
	var mu sync.Mutex
	var writes []string
	deploymentStatus := "running"
	active := true
	progress := func() map[string]any {
		return map[string]any{"id": testIntentAppID, "status": deploymentStatus, "type": "app",
			"nodes": []map[string]any{{"target": target}}}
	}
	control := testIntentMCPServer(aiAppEngineName)
	testIntentTool(control, "control_application", "write", func(req *mcp.CallToolRequest) any {
		mu.Lock()
		defer mu.Unlock()
		var args map[string]string
		if json.Unmarshal(req.Params.Arguments, &args) != nil || args["deploymentId"] != testIntentAppID ||
			req.Extra.Header.Get(aiAppNamespaceHeader) != cfg.AIAppNamespace {
			t.Errorf("unexpected VM control arguments %s", req.Params.Arguments)
		}
		writes = append(writes, args["action"])
		if args["action"] == "resume" {
			// The tool received the write before the response became unreadable.
			return nil
		}
		outcome := map[string]any{"id": testIntentAppID, "action": args["action"], "nodes": []map[string]any{{"target": target, "status": "done"}}}
		return map[string]any{"runId": "eng-fixture-control", "nsId": cfg.AIAppNamespace,
			"workflow": "control", "status": "succeeded", "input": args,
			"steps": []map[string]any{{"tool": "control_node", "output": outcome}}}
	})
	native := testIntentMCPServer(aiAppNativeServer)
	testIntentTool(native, aiAppRemoveTool, "destructive", func(req *mcp.CallToolRequest) any {
		mu.Lock()
		defer mu.Unlock()
		var args map[string]string
		if json.Unmarshal(req.Params.Arguments, &args) != nil || len(args) != 1 || args["id"] != testIntentAppID {
			t.Errorf("unexpected removal arguments %s", req.Params.Arguments)
		}
		writes = append(writes, aiAppRemoveTool)
		deploymentStatus = "removing"
		return progress()
	})
	appHandler := mcp.NewStreamableHTTPHandler(func(r *http.Request) *mcp.Server {
		if r.URL.Path == aiAppNativeMCPPath {
			return native
		}
		return control
	}, nil)
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+cfg.AIAppToken {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case aiAppNativeMCPPath, aiAppEngineMCPPath:
			appHandler.ServeHTTP(w, r)
		case "/aiapp/deployments/" + testIntentAppID + "/progress":
			mu.Lock()
			defer mu.Unlock()
			_ = json.NewEncoder(w).Encode(progress())
		case "/aiapp/endpoints":
			mu.Lock()
			defer mu.Unlock()
			endpoints := []map[string]string{}
			if active {
				endpoints = append(endpoints, map[string]string{"nsId": target.NsID, "infraId": target.InfraID,
					"nodeId": target.NodeID, "deploymentId": testIntentAppID})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"endpoints": endpoints})
		default:
			t.Errorf("unexpected AI-App path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer app.Close()
	cfg.AIAppBaseURL = app.URL
	api := NewFocusedServer(cfg)
	card := testAIAppEngineCard()
	card.Tools.Exposed = append(card.Tools.Exposed, AIAppEngineCardTool{Name: "control_application", Grade: "write", GuardedByDryRun: true})
	if res := postAIAppEngineCard(t, api, cfg.AIAppEngineRegistryToken, card); res.Code != http.StatusCreated {
		t.Fatalf("register AI-App: %d %s", res.Code, res.Body.String())
	}
	id := saveTestIntent(t, cfg, "control_application", testIntentAppID, "suspend")
	path := aiAppIntentChangesPath + "/" + id
	call := func(method, path, bearer string, input, output any, status int) {
		t.Helper()
		intentChangeTestRequest(t, api, method, path, bearer, input, output, status)
	}
	empty := AIAppIntentChangeRequest{}
	for _, token := range []string{testIntentReader, "fixture-other-framework"} {
		call(http.MethodPost, path+"/prepare", token, empty, nil, http.StatusForbidden)
	}
	call(http.MethodPost, path+"/prepare", cfg.AIAppToken, empty, nil, http.StatusUnauthorized)
	call(http.MethodGet, aiAppIntentPrefix+"/runs/"+id, testIntentReader, nil, nil, http.StatusNotFound)
	var readerRun AIAppIntentRun
	call(http.MethodPost, aiAppIntentPrefix+"/ns/"+cfg.AIAppNamespace+"/intents", testIntentReader,
		AIAppIntentRequest{Intent: testIntentAppID + " 상태"}, &readerRun, http.StatusOK)
	if readerRun.Status != "blocked" || readerRun.Caller != "reader" ||
		len(readerRun.Steps) != 1 || readerRun.Steps[0].Executed {
		t.Fatalf("status read escaped principal's grant: %+v", readerRun)
	}
	call(http.MethodGet, aiAppIntentPrefix+"/runs/"+readerRun.RunID, testIntentReader, nil, &readerRun, http.StatusOK)
	var plan AIAppIntentChangePlan
	call(http.MethodPost, path+"/prepare", testIntentOperator, empty, &plan, http.StatusOK)
	if plan.Target != target || plan.Tool != "control_application" || plan.ControlAction != "suspend" || plan.PreparedBy != "operator" {
		t.Fatalf("reviewed plan = %+v", plan)
	}
	call(http.MethodPost, path+"/approve", testIntentOperator, AIAppIntentApprovalRequest{Digest: "changed"}, nil, http.StatusConflict)
	mu.Lock()
	active = false
	mu.Unlock()
	confirm := AIAppIntentApprovalRequest{Digest: plan.Digest}
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, nil, http.StatusConflict)
	mu.Lock()
	active = true
	mu.Unlock()

	// The approval tool and REST route share the same authenticated journal.
	gateway := httptest.NewServer(api)
	defer gateway.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "intent-approval-fixture", Version: "1"}, nil)
	httpClient := partnerHTTPClient(time.Second)
	httpClient.Transport = aiAppEngineHeaders{namespace: cfg.AIAppNamespace, token: testIntentOperator}
	session, err := client.Connect(context.Background(), &mcp.StreamableClientTransport{
		Endpoint: gateway.URL + aiAppIntentMCPPath, HTTPClient: httpClient, DisableStandaloneSSE: true,
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{
		Name: "approve_application_intent_change", Arguments: aiAppIntentMCPApprove{RunID: id, Request: confirm},
	})
	if err != nil || result.IsError {
		t.Fatalf("MCP approval = %+v, %v", result, err)
	}
	api = NewFocusedServer(cfg)
	var approved AIAppIntentChangeApproval
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, &approved, http.StatusAccepted)
	if approved.Status != "succeeded" || approved.EngineRunID != "eng-fixture-control" || approved.ApprovedBy != "operator" {
		t.Fatalf("approval receipt = %+v", approved)
	}
	terminateID := saveTestIntent(t, cfg, "control_application", testIntentAppID, "terminate")
	terminatePath := aiAppIntentChangesPath + "/" + terminateID
	call(http.MethodPost, terminatePath+"/prepare", testIntentOperator, empty, &plan, http.StatusOK)
	call(http.MethodPost, terminatePath+"/approve", testIntentOperator, AIAppIntentApprovalRequest{Digest: plan.Digest}, nil, http.StatusConflict)

	resumeID := saveTestIntent(t, cfg, "control_application", testIntentAppID, "resume")
	resumePath := aiAppIntentChangesPath + "/" + resumeID
	call(http.MethodPost, resumePath+"/prepare", testIntentOperator, empty, &plan, http.StatusOK)
	for range 2 {
		call(http.MethodPost, resumePath+"/approve", testIntentOperator, AIAppIntentApprovalRequest{Digest: plan.Digest}, &approved, http.StatusAccepted)
		if approved.Status != "confirmation_pending" {
			t.Fatalf("unconfirmed resume = %+v", approved)
		}
	}
	removeID := saveTestIntent(t, cfg, "remove_application", testIntentAppID, "")
	removePath := aiAppIntentChangesPath + "/" + removeID
	call(http.MethodPost, removePath+"/prepare", testIntentOperator, AIAppIntentChangeRequest{DeploymentType: "inference"}, nil, http.StatusConflict)
	call(http.MethodPost, removePath+"/prepare", testIntentOperator, AIAppIntentChangeRequest{DeploymentType: "app"}, &plan, http.StatusOK)
	call(http.MethodPost, removePath+"/approve", testIntentOperator, AIAppIntentApprovalRequest{Digest: plan.Digest}, &approved, http.StatusAccepted)
	if approved.Status != "queued" || approved.DeploymentStatus != "removing" {
		t.Fatalf("removal = %+v", approved)
	}
	mu.Lock()
	deploymentStatus = "removed"
	mu.Unlock()
	var change AIAppIntentChange
	call(http.MethodGet, removePath, testIntentOperator, nil, &change, http.StatusOK)
	if change.Approval == nil || change.Approval.Status != "succeeded" {
		t.Fatalf("removal status = %+v", change.Approval)
	}
	mu.Lock()
	defer mu.Unlock()
	if !slices.Equal(writes, []string{"suspend", "resume", aiAppRemoveTool}) {
		t.Fatalf("write calls = %v", writes)
	}
	audit, err := os.ReadFile(cfg.path("runs", "framework-access", "current.json"))
	if err != nil || bytes.Contains(audit, []byte(testIntentOperator)) || bytes.Contains(audit, []byte(testIntentReader)) {
		t.Fatalf("unexpected policy audit: %v", err)
	}
}

func TestFrameworkAccessPolicyAuditsChangesAndRejectsLegacyTokenReuse(t *testing.T) {
	cfg := scopedIntentTestConfig(t)
	policy, err := loadFrameworkAccessPolicy(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loadFrameworkAccessPolicy(cfg); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(cfg.path("runs", "framework-access", "history"))
	if err != nil || len(entries) != 1 {
		t.Fatalf("activation history = %v, %v", entries, err)
	}
	policy.Principals[0].Grants[0].Actions = []string{"interpret"}
	if err := saveJournalRecord(cfg.FrameworkAccessPolicyFile, policy, false); err != nil {
		t.Fatal(err)
	}
	active, err := loadFrameworkAccessPolicy(cfg)
	if err != nil || active.digest == policy.digest ||
		active.Principals[0].allows(aiAppEngineName, "AI-App", cfg.AIAppNamespace, "deploy_application") {
		t.Fatalf("removed deployment grant survived policy reload: %v", err)
	}
	entries, err = os.ReadDir(cfg.path("runs", "framework-access", "history"))
	if err != nil || len(entries) != 2 {
		t.Fatalf("updated activation history = %v, %v", entries, err)
	}
	t.Setenv("AIOPS_PRINCIPAL_TEST_READER_TOKEN", cfg.AIAppToken)
	if active, err := loadFrameworkAccessPolicy(cfg); err == nil || active != nil {
		t.Fatal("a principal reused the AI-App gateway credential")
	}
}

func TestScopedAIAppIntentDeploymentAndUpdateBindApprovedRevision(t *testing.T) {
	cfg := scopedIntentTestConfig(t)
	const otherOperator = "fixture-other-operator"
	t.Setenv("AIOPS_PRINCIPAL_OTHER_OPERATOR_TOKEN", otherOperator)
	policy, err := loadJSON[frameworkAccessPolicy](cfg.FrameworkAccessPolicyFile)
	if err != nil {
		t.Fatal(err)
	}
	other := policy.Principals[0]
	other.Name, other.TokenEnv = "other-operator", "AIOPS_PRINCIPAL_OTHER_OPERATOR_TOKEN"
	policy.Principals = append(policy.Principals, other)
	if err := saveJournalRecord(cfg.FrameworkAccessPolicyFile, policy, false); err != nil {
		t.Fatal(err)
	}
	const flow = "intent-revision-fixture"
	target := model.AIAppNodeTarget{Kind: "node", NsID: cfg.AIAppNamespace, InfraID: "travel", NodeID: "vm1"}
	inf := model.AIAppNodeDeployment{Type: "inference", Targets: []model.AIAppNodeTarget{target},
		Inference: &model.AIAppInference{ModelID: "model", ArtifactID: "artifact", RuntimeID: "vllm", GPU: true}}
	planned := model.AIAppNodeDeployment{Type: "app", Targets: []model.AIAppNodeTarget{target},
		App: &model.AIAppApplication{Name: "langflow", Version: 1}}
	if err := saveJournalRecord(cfg.path("runs", "aiapp-stage", flow+".json"), aiAppStageRecord{
		ApprovedAt: time.Now(), Request: model.AIAppStageRequest{Revision: 1, Deployment: inf}, PlannedApp: &planned,
		Receipt: &model.AIAppStageReceipt{Status: "blocked"},
	}, true); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var submitted []int
	revisionTwoSubmissions := 0
	engine := testIntentMCPServer(aiAppApprovalServer)
	for _, tool := range []string{aiAppApplyTool, aiAppApplyReadTool} {
		grade := "read"
		if tool == aiAppApplyTool {
			grade = "write"
		}
		testIntentTool(engine, tool, grade, func(req *mcp.CallToolRequest) any {
			var args struct {
				CorrelationID string `json:"correlationId"`
				model.AIAppApplyRequest
			}
			if json.Unmarshal(req.Params.Arguments, &args) != nil || args.CorrelationID != flow {
				t.Errorf("invalid apply call %s", req.Params.Arguments)
			}
			r := model.AIAppApplyReceipt{
				CorrelationID: flow, Revision: args.Revision, PreviousRevision: args.Revision - 1,
				Namespace: target.NsID, Target: target.NsID + "/travel/vm1", UpdatedAt: time.Now(), Status: "queued",
			}
			if tool == aiAppApplyTool {
				saved, err := loadJSON[aiAppApplyApproval](cfg.path("runs", "aiapp-apply", flow, strconv.Itoa(args.Revision)+".json"))
				if err != nil || saved.Request.Application.App == nil || saved.Request.Application.App.Version != args.Revision ||
					frameworkDigest(saved.Request) != frameworkDigest(args.AIAppApplyRequest) {
					t.Errorf("apply called without matching saved approval: %v", err)
				}
				mu.Lock()
				submitted = append(submitted, args.Revision)
				if args.Revision == 2 {
					revisionTwoSubmissions++
				}
				mu.Unlock()
			} else {
				mu.Lock()
				pending := args.Revision == 2 && revisionTwoSubmissions == 1
				mu.Unlock()
				if pending {
					r.Status = "confirmation_pending"
					return r
				}
				r.Status = "succeeded"
				r.Inference = model.AIAppApplyStep{Status: "succeeded", DeploymentID: testIntentInferID, EngineRunID: "eng-runtime"}
				r.Application = model.AIAppApplyStep{Status: "succeeded", DeploymentID: testIntentAppID, EngineRunID: "eng-app"}
			}
			return r
		})
	}
	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return engine }, nil)
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == aiAppApprovalMCPPath && r.Header.Get("Authorization") == "Bearer "+cfg.AIAppEngineApprovalToken:
			stream.ServeHTTP(w, r)
		case r.URL.Path == "/aiapp/endpoints" && r.Header.Get("Authorization") == "Bearer "+cfg.AIAppToken:
			rows := []map[string]string{}
			for _, id := range []string{testIntentInferID, testIntentAppID} {
				rows = append(rows, map[string]string{"nsId": target.NsID, "infraId": target.InfraID, "nodeId": target.NodeID, "deploymentId": id})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"endpoints": rows})
		default:
			t.Errorf("unexpected partner call %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer app.Close()
	cfg.AIAppBaseURL = app.URL
	api := NewFocusedServer(cfg)
	if res := postAIAppEngineCard(t, api, cfg.AIAppEngineRegistryToken, testAIAppEngineCard()); res.Code != http.StatusCreated {
		t.Fatalf("register AI-App: %d %s", res.Code, res.Body.String())
	}
	planned.App.InferenceBinding = &model.AIAppInferenceBinding{BaseURLEnv: "MODEL_BASE_URL"}
	request := model.AIAppApplyRequest{Revision: 1, Inference: inf, Application: planned}
	bind := AIAppIntentChangeRequest{CorrelationID: flow, Apply: &request}
	for revision := 1; revision <= 2; revision++ {
		action, prior := "deploy_application", ""
		if revision == 2 {
			action, prior = "update_application", testIntentAppID
		}
		id := saveTestIntent(t, cfg, action, prior, "")
		path := aiAppIntentChangesPath + "/" + id
		var plan AIAppIntentChangePlan
		if revision == 2 {
			request.Inference.Inference.Args = []string{"--max-num-seqs", "32"}
			intentChangeTestRequest(t, api, http.MethodPost, path+"/prepare", testIntentOperator, bind, nil, http.StatusConflict)
			request.Inference.Inference.Args = nil
		}
		intentChangeTestRequest(t, api, http.MethodPost, path+"/prepare", testIntentOperator, bind, &plan, http.StatusOK)
		var approval AIAppIntentChangeApproval
		intentChangeTestRequest(t, api, http.MethodPost, path+"/approve", testIntentOperator,
			AIAppIntentApprovalRequest{Digest: plan.Digest}, &approval, http.StatusAccepted)
		if approval.Status != "queued" || approval.Apply == nil || approval.Apply.Revision != revision {
			t.Fatalf("approval = %+v", approval)
		}
		api = NewFocusedServer(cfg)
		var change AIAppIntentChange
		if revision == 2 {
			confirm := AIAppIntentApprovalRequest{Digest: plan.Digest}
			intentChangeTestRequest(t, api, http.MethodGet, path, testIntentOperator, nil, &change, http.StatusOK)
			if change.Approval == nil || change.Approval.Status != "confirmation_pending" {
				t.Fatalf("interrupted approval = %+v", change.Approval)
			}
			intentChangeTestRequest(t, api, http.MethodPost, path+"/approve", testIntentReader, confirm, nil, http.StatusForbidden)
			intentChangeTestRequest(t, api, http.MethodPost, path+"/approve", testIntentOperator,
				AIAppIntentApprovalRequest{Digest: strings.Repeat("b", 64)}, nil, http.StatusConflict)
			intentChangeTestRequest(t, api, http.MethodPost, path+"/approve", otherOperator, confirm, nil, http.StatusAccepted)
			var resumed AIAppIntentChangeApproval
			intentChangeTestRequest(t, api, http.MethodPost, path+"/approve", testIntentOperator, confirm, &resumed, http.StatusAccepted)
			if resumed.Status != "queued" || resumed.Apply == nil || resumed.Apply.Revision != 2 ||
				resumed.ApprovedBy != approval.ApprovedBy || !resumed.ApprovedAt.Equal(approval.ApprovedAt) {
				t.Fatalf("resumed approval = %+v", resumed)
			}
		}
		intentChangeTestRequest(t, api, http.MethodGet, path, testIntentOperator, nil, &change, http.StatusOK)
		if change.Approval == nil || change.Approval.Apply == nil || change.Approval.Apply.Status != "succeeded" ||
			change.Approval.Apply.Application.DeploymentID != testIntentAppID {
			t.Fatalf("recovered approval = %+v", change.Approval)
		}
		request.Revision, request.PreviousRevision = 2, 1
		request.Application.App.Version = 2
	}
	mu.Lock()
	defer mu.Unlock()
	if !slices.Equal(submitted, []int{1, 2, 2}) {
		t.Fatalf("submitted revisions = %v", submitted)
	}
}
