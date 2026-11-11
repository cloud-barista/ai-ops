package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"

	"kyunghee-aiops/service-control-api/internal/model"
)

func TestFocusedAIAppPreparation(t *testing.T) {
	const selection = `{"model_id":"ministral-3-8b","artifact_id":"hf-fp8"}`
	for _, test := range []struct {
		name       string
		resourceID string
		version    string
		wantStatus int
	}{
		{"AI-App catalog and CB-Tumblebug node", "etri/travel/vm1", "2", http.StatusOK},
		{"unqualified node", "vm1", "2", http.StatusConflict},
		{"legacy application version", "etri/travel/vm1", "1.0.0", http.StatusConflict},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := NewFocusedServer(NewServerConfig())
			request := focusedPlanningRequest()
			request.ApplicationProfile.AppVersion = test.version
			request.ResourceRecommendation.Recommendations[0].Resource.ResourceID = test.resourceID
			path := pathAgentControl + "/flows/" + request.CorrelationID + "/aiapp/prepare"
			if response := performJSONRequest(t, server, http.MethodPost, path, selection); response.Code != http.StatusConflict {
				t.Fatalf("unapproved request: %d %s", response.Code, response.Body)
			}
			body, err := json.Marshal(request)
			if err != nil {
				t.Fatal(err)
			}
			if response := performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body)); response.Code != http.StatusCreated {
				t.Fatalf("create plan: %d %s", response.Code, response.Body)
			}
			response := performJSONRequest(t, server, http.MethodPost, path, selection)
			if response.Code != test.wantStatus {
				t.Fatalf("prepare: %d, want %d: %s", response.Code, test.wantStatus, response.Body)
			}
			if test.wantStatus != http.StatusOK {
				return
			}
			const want = `{
				"correlation_id":"flow-focused-001","revision":1,"deployments":[
					{"type":"inference","targets":[{"kind":"node","nsId":"etri","infraId":"travel","nodeId":"vm1"}],
					 "inference":{"modelId":"ministral-3-8b","artifactId":"hf-fp8","runtimeId":"vllm","gpu":true}},
					{"type":"app","targets":[{"kind":"node","nsId":"etri","infraId":"travel","nodeId":"vm1"}],
					 "app":{"name":"chat-service","version":2}}
				]
			}`
			var got, expected any
			if err := json.Unmarshal(response.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal([]byte(want), &expected); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, expected) {
				t.Fatalf("AI-App request contract: %s", response.Body)
			}
			const reviewed = `{"model_id":"ministral-3-8b","artifact_id":"hf-fp8","max_num_seqs":16,"placement_review":{
				"nsId":"etri","specId":"aws+gpu","imageId":"aws+image","status":"Warning",
				"specStatus":"Available","imageStatus":"Available (Auto-registered)","estimatedCost":"$0.50/hour",
				"reviewedAt":"2026-11-10T00:00:00Z"}}`
			reviewedResponse := performJSONRequest(t, server, http.MethodPost, path, reviewed)
			var projection model.AIAppPreparation
			if reviewedResponse.Code != http.StatusOK || json.Unmarshal(reviewedResponse.Body.Bytes(), &projection) != nil ||
				len(projection.Deployments) != 2 {
				t.Fatalf("reviewed prepare: %d %s", reviewedResponse.Code, reviewedResponse.Body)
			}
			inf, app := projection.Deployments[0].PlacementReview, projection.Deployments[1].PlacementReview
			if inf == nil || inf.SpecID != "aws+gpu" || inf.EstimatedCost != "$0.50/hour" || !reflect.DeepEqual(inf, app) {
				t.Fatal("the reviewed selection was not copied into both deployments")
			}
			if inference := projection.Deployments[0].Inference; inference == nil ||
				!reflect.DeepEqual(inference.Args, []string{"--max-num-seqs", "16"}) {
				t.Fatalf("the selected baseline was not projected: %s", reviewedResponse.Body)
			}
			const invalidBaseline = `{"model_id":"ministral-3-8b","artifact_id":"hf-fp8","max_num_seqs":0}`
			if response := performJSONRequest(t, server, http.MethodPost, path, invalidBaseline); response.Code != http.StatusConflict {
				t.Fatalf("zero baseline: %d %s", response.Code, response.Body)
			}
		})
	}
}

func TestFocusedAIAppPlanPinsOllamaForOpenWebUI(t *testing.T) {
	config := NewServerConfig()
	registry, err := loadJSON[AgentRegistry](config.path("config", "agent_registry.json"))
	if err != nil {
		t.Fatal(err)
	}
	config.RepoRoot = t.TempDir()
	if err := saveJournalRecord(config.path("config", "agent_registry.json"), registry, false); err != nil {
		t.Fatal(err)
	}
	server := NewFocusedServer(config)
	request := focusedPlanningRequest()
	request.ApplicationProfile.AppID, request.ApplicationProfile.AppVersion = "open-webui", "1"
	request.ResourceRecommendation.Recommendations[0].Resource.ResourceID = "etri/travel/vm2"
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	var wire map[string]json.RawMessage
	if err := json.Unmarshal(body, &wire); err != nil {
		t.Fatal(err)
	}
	plan := func(runtime string) *httptest.ResponseRecorder {
		t.Helper()
		wire["runtime_id"] = json.RawMessage(strconv.Quote(runtime))
		body, err := json.Marshal(wire)
		if err != nil {
			t.Fatal(err)
		}
		return performJSONRequest(t, server, http.MethodPost, pathDeploymentPlans, string(body))
	}
	if response := plan("unsupported"); response.Code != http.StatusUnprocessableEntity {
		t.Errorf("unsupported runtime: %d %s", response.Code, response.Body)
	}
	if response := plan("ollama"); response.Code != http.StatusCreated {
		t.Fatalf("create Ollama plan: %d %s", response.Code, response.Body)
	}
	if response := plan("vllm"); response.Code != http.StatusUnprocessableEntity {
		t.Errorf("changed the runtime on the same plan: %d %s", response.Code, response.Body)
	}
	path := pathAgentControl + "/flows/" + request.CorrelationID + "/aiapp/prepare"
	response := performJSONRequest(t, server, http.MethodPost, path, `{"model_id":"qwen3-vl-8b","artifact_id":"ollama-gguf"}`)
	var prepared model.AIAppPreparation
	if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &prepared) != nil || len(prepared.Deployments) != 2 ||
		prepared.Deployments[0].Inference == nil || prepared.Deployments[0].Inference.RuntimeID != "ollama" ||
		prepared.Deployments[1].App == nil || prepared.Deployments[1].App.Name != "open-webui" {
		t.Fatalf("prepare Ollama deployment: %d %s", response.Code, response.Body)
	}
	if response := performJSONRequest(t, server, http.MethodPost, path, `{"model_id":"qwen3-vl-8b","artifact_id":"ollama-gguf","max_num_seqs":16}`); response.Code != http.StatusConflict {
		t.Errorf("vLLM setting on an Ollama plan: %d %s", response.Code, response.Body)
	}
}

func TestFocusedServerIsolatesPoCAppDeploy(t *testing.T) {
	config := NewServerConfig()
	config.AppDeployPoCEnabled = false
	config.LegacyAPIEnabled = false
	config.AppDeployBaseURL = "http://localhost:18150/api/v1"
	config.ResourceCatalogPath = filepath.Join(t.TempDir(), "missing-poc-catalog.json")
	pocRoutes := []struct{ method, path string }{
		{http.MethodGet, pathAgentControl + "/integration"},
		{http.MethodGet, pathAgentControl + "/flows/example/appdeploy"},
		{http.MethodPost, pathAgentControl + "/flows/example/appdeploy/prepare"},
		{http.MethodPost, pathAgentControl + "/flows/example/appdeploy/submit"},
	}
	for _, aiAppURL := range []string{"", "http://127.0.0.1:8090"} {
		config.AIAppBaseURL = aiAppURL
		server := NewFocusedServer(config)
		if response := performJSONRequest(t, server, http.MethodGet, pathReadyz, ""); response.Code != http.StatusOK {
			t.Fatalf("focused readiness depends on the PoC catalog: %d %s", response.Code, response.Body)
		}
		for _, route := range pocRoutes {
			if response := performJSONRequest(t, server, route.method, route.path, ""); response.Code != http.StatusNotFound {
				t.Fatalf("focused server exposes PoC %s %s: %d %s", route.method, route.path, response.Code, response.Body)
			}
		}
	}
	config.AIAppBaseURL = ""
	config.AppDeployPoCEnabled = true
	pocServer := NewAppDeployPoCServer(config)
	if response := performJSONRequest(t, pocServer, http.MethodGet, pathAgentControl+"/integration", ""); response.Code != http.StatusOK {
		t.Fatalf("explicit PoC integration: %d %s", response.Code, response.Body)
	}
	if response := performJSONRequest(t, pocServer, http.MethodPost, pathPlannerDeploy, "{}"); response.Code != http.StatusNotFound {
		t.Fatalf("AppDeploy PoC exposes the all-in-one planner: %d %s", response.Code, response.Body)
	}
}
