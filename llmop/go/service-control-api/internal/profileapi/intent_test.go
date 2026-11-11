package profileapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"kyunghee-aiops/llmop-api/internal/llmclient"
)

func TestOpsLLMInterpretRoutesToEnabledType(t *testing.T) {
	const deploymentID = "3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
	called := make(chan string, 10)
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			Model    string `json:"model"`
			Messages []struct {
				Content string `json:"content"`
			} `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode provider request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		called <- request.Model
		if request.Model == "bad-output" {
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"execute_shell\"}"}}]}`))
			return
		}
		if len(request.Messages) == 2 && strings.HasSuffix(request.Messages[1].Content, "의 상태를 알려줘.") {
			if request.Messages[1].Content != "배포 [deployment]의 상태를 알려줘." {
				t.Error("provider received the deployment ID")
			}
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"application_status\"}"}}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"list_applications\"}"}}]}`))
	}))
	defer provider.Close()
	path := filepath.Join(t.TempDir(), "candidates.json")
	config := llmclient.CandidateConfig{Candidates: []llmclient.Candidate{
		{CandidateID: "disabled", LLMType: "subscription", Endpoint: provider.URL, ActualModel: "disabled", Enabled: false},
		{CandidateID: "paid", LLMType: "subscription", Endpoint: provider.URL, ActualModel: "paid", Enabled: true},
		{CandidateID: "public", LLMType: "open_weights", Endpoint: provider.URL, ActualModel: "public", Enabled: true},
		{CandidateID: "local", LLMType: "self_hosted", Endpoint: provider.URL, ActualModel: "local", Enabled: true},
		{CandidateID: "bad", LLMType: "self_hosted", Endpoint: provider.URL, ActualModel: "bad-output", Enabled: true},
	}}
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	settings := testConfig()
	settings.RepoRoot = t.TempDir()
	settings.LLMCandidatesPath, settings.OpsLLMToken, settings.CandidateID = path, "test-ops-token", "local"
	server := newServer(settings, &generatorStub{})
	for _, tc := range []struct{ body, llmType, model string }{
		{`{"intent":"구독형 LLM으로 배포 가능한 응용을 보여줘"}`, "subscription", "paid"},
		{`{"intent":"배포 가능한 응용을 보여줘","llm_type":"open_weights"}`, "open_weights", "public"},
		{`{"intent":"배포 가능한 응용을 보여줘"}`, "self_hosted", "local"},
	} {
		request := httptest.NewRequest(http.MethodPost, pathOpsLLMInterpret, strings.NewReader(tc.body))
		request.Header.Set("Authorization", "Bearer test-ops-token")
		response := httptest.NewRecorder()
		server.ServeHTTP(response, request)
		var result OpsLLMIntentResult
		if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &result) != nil ||
			result.Status != "completed" || result.RunID == "" || result.LLMType != tc.llmType || result.ActualModel != tc.model ||
			result.Proposal == nil || result.Proposal.Action != "list_applications" {
			t.Fatalf("type %s: status=%d body=%s", tc.llmType, response.Code, response.Body.String())
		}
		if model := <-called; model != tc.model {
			t.Fatalf("called %q, want %q", model, tc.model)
		}
	}
	request := httptest.NewRequest(http.MethodPost, pathOpsLLMInterpret,
		strings.NewReader(`{"intent":"배포 `+deploymentID+`의 상태를 알려줘.","llm_type":"subscription"}`))
	request.Header.Set("Authorization", "Bearer test-ops-token")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	var result OpsLLMIntentResult
	if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &result) != nil ||
		result.Proposal == nil || result.Proposal.Action != "application_status" || result.Proposal.DeploymentID != deploymentID {
		t.Fatalf("deployment ID was not resolved from the original input: %s", response.Body.String())
	}
	if model := <-called; model != "paid" {
		t.Fatalf("called %q, want paid", model)
	}
	for _, tc := range []struct {
		body, bearer string
		status       int
	}{
		{`{"intent":"목록 보여줘"}`, "", http.StatusUnauthorized},
		{`{"intent":"api_key=inline-value"}`, "Bearer test-ops-token", http.StatusBadRequest},
		{`{"intent":"구독형 LLM으로 목록 보여줘","llm_type":"self_hosted"}`, "Bearer test-ops-token", http.StatusBadRequest},
		{`{"intent":"목록 보여줘","llm_type":"unknown"}`, "Bearer test-ops-token", http.StatusBadRequest},
	} {
		request := httptest.NewRequest(http.MethodPost, pathOpsLLMInterpret, strings.NewReader(tc.body))
		request.Header.Set("Authorization", tc.bearer)
		response := httptest.NewRecorder()
		server.ServeHTTP(response, request)
		if response.Code != tc.status || len(called) != 0 {
			t.Fatalf("status=%d want=%d unexpected model calls=%d", response.Code, tc.status, len(called))
		}
	}
	settings.CandidateID = "bad"
	request = httptest.NewRequest(http.MethodPost, pathOpsLLMInterpret, strings.NewReader(`{"intent":"목록 보여줘"}`))
	request.Header.Set("Authorization", "Bearer test-ops-token")
	response = httptest.NewRecorder()
	newServer(settings, &generatorStub{}).ServeHTTP(response, request)
	if response.Code != http.StatusBadGateway || !strings.Contains(response.Body.String(), `"status":"invalid_model_output"`) ||
		strings.Contains(response.Body.String(), "execute_shell") {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
}

func TestOpsLLMProposalBindsOnlyOneUserDeploymentID(t *testing.T) {
	const id = "3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
	const choice = `{"action":"application_status"}`
	proposal, ok := parseOpsLLMProposal(choice, id+" 상태 보여줘")
	if !ok || proposal.DeploymentID != id || proposal.Action != "application_status" {
		t.Fatalf("proposal=%#v, ok=%v", proposal, ok)
	}
	for _, intent := range []string{"그 배포 상태 보여줘", id + " 또는 4f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"} {
		proposal, ok := parseOpsLLMProposal(choice, intent)
		if !ok || proposal.Action != "clarify" || proposal.DeploymentID != "" {
			t.Fatalf("ambiguous proposal=%#v, ok=%v", proposal, ok)
		}
	}
	if _, ok := parseOpsLLMProposal(`{"action":"application_status","deployment_id":"`+id+`"}`, "그 배포 상태 보여줘"); ok {
		t.Fatal("accepted a model-supplied deployment ID")
	}
	if _, ok := parseOpsLLMProposal(`{"action":"update_application","control_action":"suspend"}`, id+" 업데이트해줘"); ok {
		t.Fatal("accepted control_action on an application update")
	}
}

func TestOpsLLMControlLabelsPreserveProposalContract(t *testing.T) {
	const id = "3f9b2c4e-8a51-4d27-9e0c-5b1f6a7d2e48"
	for _, action := range []string{"suspend", "resume", "reboot", "terminate"} {
		for _, output := range []string{
			`{"action":"vm_` + action + `"}`,
			`{"action":"control_application","control_action":"` + action + `"}`,
		} {
			proposal, ok := parseOpsLLMProposal(output, id+"의 VM을 제어해줘.")
			if !ok || proposal.Action != "control_application" || proposal.ControlAction != action || proposal.DeploymentID != id {
				t.Fatalf("control proposal from %s = %#v, ok=%v", output, proposal, ok)
			}
		}
	}
	for _, output := range []string{`{"action":"vm_create"}`, `{"action":"vm_resume","control_action":"terminate"}`} {
		if _, ok := parseOpsLLMProposal(output, id); ok {
			t.Fatalf("accepted unsupported control output %s", output)
		}
	}
}
