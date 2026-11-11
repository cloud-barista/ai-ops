package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"kyunghee-aiops/service-control-api/internal/model"
)

func TestOptimizationApprovalUsesPublishedEvidenceAndRecordsComparison(t *testing.T) {
	cfg := scopedIntentTestConfig(t)
	policy, err := loadJSON[frameworkAccessPolicy](cfg.FrameworkAccessPolicyFile)
	if err != nil {
		t.Fatal(err)
	}
	policy.Principals[0].Grants[0].Actions = append(policy.Principals[0].Grants[0].Actions,
		"optimize_inference.read", "optimize_inference.approve", "optimize_inference.compare")
	policy.Principals[1].Grants[0].Actions = append(policy.Principals[1].Grants[0].Actions, "optimize_inference.read")
	if err := saveJournalRecord(cfg.FrameworkAccessPolicyFile, policy, false); err != nil {
		t.Fatal(err)
	}
	const flow = "model-optimization-fixture"
	proposalID := strings.Repeat("c", 64)
	beforeObservationID := strings.Repeat("a", 64)
	afterObservationID := strings.Repeat("b", 64)
	transitionObservationID := strings.Repeat("d", 64)
	const afterInf = "e82cb140-c4d1-468b-a819-16e262c00bc8"
	const afterApp = "cc8d14b1-cd6e-47dc-b807-39396014ae6b"
	target := model.AIAppNodeTarget{Kind: "node", NsID: cfg.AIAppNamespace, InfraID: "travel", NodeID: "vm1"}
	previous := model.AIAppApplyRequest{
		Revision: 2, PreviousRevision: 1,
		Inference: model.AIAppNodeDeployment{
			Type: "inference", Targets: []model.AIAppNodeTarget{target},
			Inference: &model.AIAppInference{ModelID: "fixture-model", ArtifactID: "fp8", RuntimeID: "vllm",
				GPU: true, Args: []string{"--max-num-seqs", "16"}},
		},
		Application: model.AIAppNodeDeployment{
			Type: "app", Targets: []model.AIAppNodeTarget{target},
			App: &model.AIAppApplication{Name: "langflow", Version: 1, InferenceBinding: &model.AIAppInferenceBinding{BaseURLEnv: "MODEL_BASE_URL"}},
		},
	}
	h := restHandler{config: cfg}
	if err := saveJournalRecord(h.applyApprovalPath(flow, 2), aiAppApplyApproval{ApprovedAt: time.Now().UTC(), Request: previous}, true); err != nil {
		t.Fatal(err)
	}
	receipt := func(revision int) model.AIAppApplyReceipt {
		r := model.AIAppApplyReceipt{CorrelationID: flow, Revision: revision, PreviousRevision: revision - 1,
			Namespace: target.NsID, Target: target.NsID + "/travel/vm1", UpdatedAt: time.Now().UTC(), Status: "succeeded"}
		r.Inference = model.AIAppApplyStep{Status: "succeeded", EngineRunID: "eng-inf-before", DeploymentID: testIntentInferID}
		r.Application = model.AIAppApplyStep{Status: "succeeded", EngineRunID: "eng-app-before", DeploymentID: testIntentAppID}
		if revision == 3 {
			r.Inference = model.AIAppApplyStep{Status: "succeeded", EngineRunID: "eng-inf-after", DeploymentID: afterInf}
			r.Application = model.AIAppApplyStep{Status: "succeeded", EngineRunID: "eng-app-after", DeploymentID: afterApp}
		}
		return r
	}
	observation := func(r model.AIAppApplyReceipt, id string, maxNumSeqs int, window model.AIAppOptimizationWindow) map[string]any {
		return map[string]any{"observationId": id, "correlationId": flow, "revision": r.Revision,
			"placement":      map[string]string{"nsId": r.Namespace, "infraId": "travel", "nodeId": "vm1", "deploymentId": r.Inference.DeploymentID},
			"inferenceRunId": r.Inference.EngineRunID, "applicationDeploymentId": r.Application.DeploymentID,
			"applicationRunId": r.Application.EngineRunID, "applicationName": "langflow", "applicationVersion": 1,
			"runtimeId": "vllm", "modelId": "fixture-model", "artifactId": "fp8", "maxNumSeqs": maxNumSeqs, "window": window}
	}
	end := time.Now().UTC().Truncate(time.Second).Add(-time.Minute)
	beforeWindow := model.AIAppOptimizationWindow{Start: end.Add(-7 * time.Minute), End: end.Add(-5 * time.Minute)}
	afterWindow := model.AIAppOptimizationWindow{Start: end.Add(-2 * time.Minute), End: end}
	firstCandidateResponse := afterWindow.Start.Add(-time.Second)
	transition := model.AIAppOptimizationTransitionEvidence{
		CorrelationID: flow, PreviousRevision: 2, Revision: 3,
		BaselineApplicationDeploymentID: testIntentAppID, CandidateApplicationDeploymentID: afterApp,
		RuntimeImage: "vllm-fixture-image", GPUUUIDs: []string{"GPU-fixture"},
		LastBaselineResponseAt: firstCandidateResponse.Add(-153*time.Second - 400*time.Millisecond), FirstCandidateResponseAt: firstCandidateResponse,
	}
	var eligible, fixtureData atomic.Bool
	var applyCalls, compareCalls atomic.Int32
	eligible.Store(true)
	appMCP := testIntentMCPServer(aiAppApprovalServer)
	testIntentTool(appMCP, aiAppOptimizationReadTool, "read", func(req *mcp.CallToolRequest) any {
		var args struct {
			ProposalID   string `json:"proposalId"`
			CheckCurrent bool   `json:"checkCurrent"`
		}
		if json.Unmarshal(req.Params.Arguments, &args) != nil || args.ProposalID != proposalID || !args.CheckCurrent {
			t.Errorf("review missed its current evidence check: %s", req.Params.Arguments)
		}
		kind := "measurement"
		if fixtureData.Load() {
			kind = "fixture"
		}
		return map[string]any{"proposalId": proposalID, "eligible": eligible.Load(), "proposal": map[string]any{
			"schemaVersion": "khu-inference-optimization/v1", "observation": observation(receipt(2), beforeObservationID, 16, beforeWindow),
			"decision": map[string]any{"action": "UPDATE", "dataKind": kind, "holdoutPassed": true,
				"currentMaxNumSeqs": 16, "candidateMaxNumSeqs": 32},
		}}
	})
	testIntentTool(appMCP, aiAppApplyReadTool, "read", func(req *mcp.CallToolRequest) any {
		var args struct {
			Revision int `json:"revision"`
		}
		_ = json.Unmarshal(req.Params.Arguments, &args)
		r := receipt(args.Revision)
		if args.Revision == 3 && applyCalls.Load() == 1 {
			r.Status = "confirmation_pending"
			r.Application = model.AIAppApplyStep{Status: "waiting"}
		}
		return r
	})
	testIntentTool(appMCP, aiAppApplyTool, "write", func(req *mcp.CallToolRequest) any {
		applyCalls.Add(1)
		var got struct {
			CorrelationID string `json:"correlationId"`
			model.AIAppApplyRequest
		}
		_ = json.Unmarshal(req.Params.Arguments, &got)
		want := previous
		want.Revision, want.PreviousRevision = 3, 2
		inf := *want.Inference.Inference
		inf.Args = []string{"--max-num-seqs", "32"}
		want.Inference.Inference = &inf
		if got.CorrelationID != flow || !reflect.DeepEqual(got.AIAppApplyRequest, want) {
			t.Errorf("unexpected optimizer changes: %s", req.Params.Arguments)
		}
		path := h.optimizationPath(optimizationAccess{flow: flow, id: proposalID}, "approval.json")
		saved, err := loadJSON[optimizationApprovalRecord](path)
		if err != nil || saved.ApprovedBy != "operator" || !reflect.DeepEqual(saved.Request, want) {
			t.Errorf("optimization was submitted before exact approval was saved: %v", err)
		}
		r := receipt(3)
		r.Status = "queued"
		return r
	})
	testIntentTool(appMCP, aiAppOptimizationCompareTool, "read", func(req *mcp.CallToolRequest) any {
		compareCalls.Add(1)
		var got struct {
			ProposalID string `json:"proposalId"`
			model.AIAppOptimizationCompareRequest
		}
		if err := json.Unmarshal(req.Params.Arguments, &got); err != nil || got.ProposalID != proposalID {
			t.Errorf("unexpected comparison request: %s", req.Params.Arguments)
		}
		if got.Load.ObservationID == transitionObservationID {
			if got.DowntimeSeconds != nil || !reflect.DeepEqual(got.TransitionEvidence, &transition) {
				t.Errorf("unexpected transition measurement: %s", req.Params.Arguments)
			}
			return map[string]any{"proposalId": proposalID, "status": "ROLLBACK_REVIEW", "downtimeSeconds": 153.4,
				"transitionEvidence": transition, "observation": observation(receipt(3), transitionObservationID, 32, afterWindow)}
		}
		if got.Load.ObservationID != afterObservationID ||
			got.DowntimeSeconds == nil || *got.DowntimeSeconds != 35 {
			t.Errorf("unexpected post-apply measurement: %s", req.Params.Arguments)
		}
		return map[string]any{"proposalId": proposalID, "status": "ROLLBACK_REVIEW", "downtimeSeconds": 35,
			"observation": observation(receipt(3), afterObservationID, 32, afterWindow)}
	})
	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return appMCP }, nil)
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != aiAppApprovalMCPPath || r.Header.Get("Authorization") != "Bearer "+cfg.AIAppEngineApprovalToken ||
			r.Header.Get(aiAppNamespaceHeader) != cfg.AIAppNamespace {
			t.Error("optimization tool escaped the registered approval path or scope")
			w.WriteHeader(http.StatusForbidden)
			return
		}
		stream.ServeHTTP(w, r)
	}))
	defer app.Close()
	cfg.AIAppBaseURL = app.URL
	server := NewFocusedServer(cfg)
	if res := postAIAppEngineCard(t, server, cfg.AIAppEngineRegistryToken, testAIAppEngineCard()); res.Code != http.StatusCreated {
		t.Fatalf("register AI-App: %d %s", res.Code, res.Body.String())
	}
	path := pathAgentControl + "/flows/" + flow + "/aiapp/optimizations/" + proposalID
	confirm := model.AIAppOptimizationApprovalRequest{Digest: proposalID}
	call := func(method, path, token string, input, output any, status int) {
		t.Helper()
		intentChangeTestRequest(t, server, method, path, token, input, output, status)
	}
	var review model.AIAppOptimizationReview
	call(http.MethodGet, path, testIntentReader, nil, &review, http.StatusOK)
	if !review.Eligible || review.Next == nil || review.Next.Revision != 3 ||
		!slices.Equal(review.Next.Inference.Inference.Args, []string{"--max-num-seqs", "32"}) {
		t.Fatalf("review = %+v", review)
	}
	call(http.MethodPost, path+"/approve", testIntentReader, confirm, nil, http.StatusForbidden)
	call(http.MethodPost, path+"/approve", testIntentOperator, map[string]any{"digest": proposalID, "setting": 64}, nil, http.StatusBadRequest)
	fixtureData.Store(true)
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, nil, http.StatusBadGateway)
	fixtureData.Store(false)
	eligible.Store(false)
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, nil, http.StatusConflict)
	if applyCalls.Load() != 0 {
		t.Fatal("an unqualified or stale proposal was submitted")
	}
	eligible.Store(true)
	var approved model.AIAppOptimizationApproval
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, &approved, http.StatusAccepted)
	if approved.Status != "queued" || approved.ApprovedBy != "operator" {
		t.Fatalf("approval = %+v", approved)
	}
	server = NewFocusedServer(cfg)
	eligible.Store(false)
	var interrupted model.AIAppOptimizationReview
	call(http.MethodGet, path, testIntentReader, nil, &interrupted, http.StatusOK)
	if applyCalls.Load() != 1 || interrupted.Approval == nil || interrupted.Approval.Status != "confirmation_pending" {
		t.Fatalf("read retried an interrupted optimization or lost its state: %+v", interrupted)
	}
	var resumed model.AIAppOptimizationApproval
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, &resumed, http.StatusAccepted)
	if resumed.Status != "queued" || resumed.Receipt == nil || resumed.Receipt.Revision != 3 ||
		resumed.ApprovedBy != approved.ApprovedBy || !resumed.ApprovedAt.Equal(approved.ApprovedAt) {
		t.Fatalf("resumed optimization = %+v", resumed)
	}
	call(http.MethodPost, path+"/approve", testIntentOperator, confirm, &approved, http.StatusAccepted)
	if approved.Receipt == nil || approved.Receipt.Status != "succeeded" || approved.Receipt.Inference.DeploymentID != afterInf {
		t.Fatalf("recovered apply = %+v", approved)
	}
	downtime := 35.0
	request := model.AIAppOptimizationCompareRequest{
		Load:            model.AIAppOptimizationLoad{ObservationID: afterObservationID, Window: afterWindow, WorkloadID: "repeat-v1"},
		DowntimeSeconds: &downtime,
	}
	var compared model.AIAppOptimizationAssessment
	call(http.MethodPost, path+"/compare", testIntentOperator, request, &compared, http.StatusOK)
	rollback := compared.RollbackDraft
	if rollback == nil || rollback.Revision != 4 || rollback.PreviousRevision != 3 ||
		!reflect.DeepEqual(rollback.Inference, previous.Inference) || !reflect.DeepEqual(rollback.Application, previous.Application) {
		t.Fatalf("rollback draft = %+v", rollback)
	}
	call(http.MethodPost, path+"/compare", testIntentOperator, request, nil, http.StatusOK)
	downtime = 36
	call(http.MethodPost, path+"/compare", testIntentOperator, request, nil, http.StatusConflict)
	request.Load.ObservationID, request.Load.RuntimeImage = transitionObservationID, transition.RuntimeImage
	request.TransitionEvidence = &transition
	call(http.MethodPost, path+"/compare", testIntentOperator, request, nil, http.StatusBadRequest)
	request.DowntimeSeconds = nil
	wrong := transition
	wrong.CandidateApplicationDeploymentID = testIntentAppID
	request.TransitionEvidence = &wrong
	call(http.MethodPost, path+"/compare", testIntentOperator, request, nil, http.StatusConflict)
	request.TransitionEvidence = &transition
	call(http.MethodPost, path+"/compare", testIntentOperator, request, &compared, http.StatusOK)
	if compared.ObservationID != transitionObservationID || compared.RollbackDraft == nil ||
		!strings.Contains(string(compared.Comparison), `"downtimeSeconds":153.4`) {
		t.Fatalf("transition assessment = %+v", compared)
	}
	call(http.MethodPost, path+"/compare", testIntentOperator, request, nil, http.StatusOK)
	if applyCalls.Load() != 2 || compareCalls.Load() != 2 {
		t.Fatalf("apply=%d compare=%d after replays", applyCalls.Load(), compareCalls.Load())
	}
}
