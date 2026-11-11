package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestFrameworkMCPRegistrationRecoveryAndScopedInvocation(t *testing.T) {
	cfg := testAIAppRegistryConfig(t)
	cfg.FrameworkAccessPolicyFile = cfg.path("config", "framework-access.json")
	const (
		name     = "infra-test"
		ns       = "infra-khu"
		peerKey  = "fixture-upstream-key"
		adminKey = "fixture-registry-manager"
		readKey  = "fixture-framework-reader"
		otherKey = "fixture-framework-foreign"
	)
	t.Setenv("AIOPS_MCP_INFRA_TEST_TOKEN", peerKey)
	t.Setenv("AIOPS_PRINCIPAL_FW_MANAGER_TOKEN", adminKey)
	t.Setenv("AIOPS_PRINCIPAL_FW_READER_TOKEN", readKey)
	t.Setenv("AIOPS_PRINCIPAL_FW_FOREIGN_TOKEN", otherKey)
	var requests, calls atomic.Int32
	buildPeer := func(serverName string, changed bool, annotations *mcp.ToolAnnotations) *mcp.Server {
		peer := mcp.NewServer(&mcp.Implementation{Name: serverName, Version: "0.1.0"}, nil)
		for _, toolName := range []string{"get_infra_list", "get_infra"} {
			properties := map[string]any{"ns_id": map[string]string{"type": "string"}}
			required := []string{"ns_id"}
			if toolName == "get_infra" {
				properties["infra_id"] = map[string]string{"type": "string"}
				required = append(required, "infra_id")
			}
			if changed {
				properties["detail"] = map[string]string{"type": "string"}
			}
			peer.AddTool(&mcp.Tool{
				Name: toolName, InputSchema: map[string]any{"type": "object", "properties": properties, "required": required}, Annotations: annotations,
			}, func(_ context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
				calls.Add(1)
				var args map[string]string
				if err := json.Unmarshal(req.Params.Arguments, &args); err != nil {
					return nil, err
				}
				if toolName == "get_infra_list" {
					return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(req.Params.Arguments)}}}, nil
				}
				return &mcp.CallToolResult{StructuredContent: args}, nil
			})
		}
		return peer
	}
	var active atomic.Pointer[mcp.Server]
	active.Store(buildPeer("cb-tumblebug", false, nil))
	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return active.Load() },
		&mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.URL.Path != "/mcp" || r.Header.Get("Authorization") != "Bearer "+peerKey || r.Header.Get(aiAppNamespaceHeader) != ns {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		stream.ServeHTTP(w, r)
	}))
	defer upstream.Close()

	policy := frameworkAccessPolicy{
		Version: 1,
		Principals: []frameworkPrincipal{
			{Name: "manager", TokenEnv: "AIOPS_PRINCIPAL_FW_MANAGER_TOKEN", Grants: []frameworkGrant{
				{Agent: name, Framework: "AI-Infra", Namespace: ns, Actions: []string{"registry.manage", "registry.read"}},
			}},
			{Name: "reader", TokenEnv: "AIOPS_PRINCIPAL_FW_READER_TOKEN", Grants: []frameworkGrant{
				{Agent: name, Framework: "AI-Infra", Namespace: ns, Actions: []string{"read.infras", "read.infra", "read.off_policy"}},
			}},
			{Name: "foreign", TokenEnv: "AIOPS_PRINCIPAL_FW_FOREIGN_TOKEN", Grants: []frameworkGrant{
				{Agent: name, Framework: "AI-HA", Namespace: ns, Actions: []string{"registry.manage", "registry.read", "read.infras"}},
				{Agent: name, Framework: "AI-Infra", Namespace: "foreign-ns", Actions: []string{"registry.manage", "registry.read", "read.infras"}},
			}},
		},
		MCPPeers: map[string]frameworkMCPPeer{name: {
			Framework: "AI-Infra", Namespace: ns, Role: "Infrastructure inventory", Endpoint: upstream.URL + "/mcp",
			ServerName: "cb-tumblebug", TokenEnv: "AIOPS_MCP_INFRA_TEST_TOKEN",
			ReadTools: map[string]frameworkMCPTool{
				"read.infras": {Name: "get_infra_list", NamespaceArgument: "ns_id"},
				"read.infra":  {Name: "get_infra", NamespaceArgument: "ns_id", Arguments: []string{"infra_id"}},
			},
		}},
	}
	if err := saveJournalRecord(cfg.FrameworkAccessPolicyFile, policy, false); err != nil {
		t.Fatal(err)
	}

	api := NewFocusedServer(cfg)
	path := frameworkMCPPath + "/" + name
	list := path + "/actions/read.infras/invoke"
	detail := path + "/actions/read.infra/invoke"
	empty := map[string]string{}
	intentChangeTestRequest(t, api, http.MethodPut, path, "", nil, nil, http.StatusUnauthorized)
	intentChangeTestRequest(t, api, http.MethodPut, path, readKey, nil, nil, http.StatusForbidden)
	intentChangeTestRequest(t, api, http.MethodPut, path, otherKey, nil, nil, http.StatusForbidden)
	intentChangeTestRequest(t, api, http.MethodPost, list, otherKey, empty, nil, http.StatusForbidden)
	intentChangeTestRequest(t, api, http.MethodPost, list, readKey, empty, nil, http.StatusNotFound)
	if requests.Load() != 0 {
		t.Fatal("access or registration failure contacted the peer")
	}

	var card, again frameworkMCPRegistration
	intentChangeTestRequest(t, api, http.MethodPut, path, adminKey, nil, &card, http.StatusOK)
	intentChangeTestRequest(t, api, http.MethodPut, path, adminKey, nil, &again, http.StatusOK)
	if card.Namespace != ns || card.Role != "Infrastructure inventory" || card.Endpoint != upstream.URL+"/mcp" ||
		card.Framework != "AI-Infra" || card.RegisteredBy != "manager" || !card.RegisteredAt.Equal(again.RegisteredAt) ||
		card.ReadTools["read.infra"].Name != "get_infra" || calls.Load() != 0 {
		t.Fatalf("unexpected registration %+v", card)
	}
	record, err := os.ReadFile(cfg.path("runs", "framework-mcp", name+".json"))
	if err != nil || bytes.Contains(record, []byte(peerKey)) || bytes.Contains(record, []byte(adminKey)) {
		t.Fatal("registration record is missing or contains a credential")
	}

	api = NewFocusedServer(cfg)
	before := requests.Load()
	intentChangeTestRequest(t, api, http.MethodGet, path, adminKey, nil, &again, http.StatusOK)
	intentChangeTestRequest(t, api, http.MethodPost, detail, readKey, map[string]string{"infra_id": "../foreign"}, nil, http.StatusBadRequest)
	intentChangeTestRequest(t, api, http.MethodPost, list, readKey, map[string]string{"ns_id": "foreign-ns"}, nil, http.StatusBadRequest)
	intentChangeTestRequest(t, api, http.MethodPost, path+"/actions/read.off_policy/invoke", readKey, empty, nil, http.StatusForbidden)
	if !card.RegisteredAt.Equal(again.RegisteredAt) || requests.Load() != before {
		t.Fatal("recovery changed the registration or a rejected call contacted the peer")
	}
	var output frameworkMCPInvocation
	intentChangeTestRequest(t, api, http.MethodPost, list, readKey, empty, &output, http.StatusOK)
	if string(output.Result) != `{"ns_id":"infra-khu"}` {
		t.Fatalf("namespace not bound: %s", output.Result)
	}
	intentChangeTestRequest(t, api, http.MethodPost, detail, readKey, map[string]string{"infra_id": "gpu-1"}, &output, http.StatusOK)
	var args map[string]string
	if err := json.Unmarshal(output.Result, &args); err != nil || args["infra_id"] != "gpu-1" || args["ns_id"] != ns {
		t.Fatalf("unexpected scoped input: %s", output.Result)
	}

	active.Store(buildPeer("wrong-server", false, nil))
	intentChangeTestRequest(t, api, http.MethodPost, list, readKey, empty, nil, http.StatusConflict)
	active.Store(buildPeer("cb-tumblebug", true, nil))
	intentChangeTestRequest(t, api, http.MethodPost, list, readKey, empty, nil, http.StatusConflict)
	if calls.Load() != 2 {
		t.Fatal("an unregistered tool contract was invoked")
	}
	intentChangeTestRequest(t, api, http.MethodPut, path, adminKey, nil, &again, http.StatusOK)
	if again.ContractDigest == card.ContractDigest {
		t.Fatal("refresh did not pin the new tool declaration")
	}
	peer := policy.MCPPeers[name]
	peer.Framework = "AI-Conn"
	peer.ServerName = "connector-test"
	policy.MCPPeers[name] = peer
	policy.Principals[0].Grants[0].Framework = "AI-Conn"
	if err := saveJournalRecord(cfg.FrameworkAccessPolicyFile, policy, false); err != nil {
		t.Fatal(err)
	}
	api = NewFocusedServer(cfg)
	before = requests.Load()
	intentChangeTestRequest(t, api, http.MethodGet, path, adminKey, nil, nil, http.StatusConflict)
	intentChangeTestRequest(t, api, http.MethodPost, list, readKey, empty, nil, http.StatusForbidden)
	if requests.Load() != before {
		t.Fatal("a stale binding or foreign framework contacted the peer")
	}
	active.Store(buildPeer("connector-test", false, nil))
	intentChangeTestRequest(t, api, http.MethodPut, path, adminKey, nil, nil, http.StatusConflict)
	active.Store(buildPeer("connector-test", false, &mcp.ToolAnnotations{ReadOnlyHint: true}))
	intentChangeTestRequest(t, api, http.MethodPut, path, adminKey, nil, &card, http.StatusOK)
	if card.Framework != "AI-Conn" || card.ServerName != "connector-test" {
		t.Fatal("registration did not retain the configured framework and server identity")
	}
	intentChangeTestRequest(t, api, http.MethodDelete, path, readKey, nil, nil, http.StatusForbidden)
	intentChangeTestRequest(t, api, http.MethodDelete, path, adminKey, nil, nil, http.StatusNoContent)
	intentChangeTestRequest(t, NewFocusedServer(cfg), http.MethodGet, path, adminKey, nil, nil, http.StatusNotFound)
}
