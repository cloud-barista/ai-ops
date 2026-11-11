package api

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"github.com/labstack/echo/v4"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type frameworkMCPInvocation struct {
	Agent  string          `json:"agent"`
	Action string          `json:"action"`
	Result json.RawMessage `json:"result" swaggertype:"object"`
}

// RestPostFrameworkMCPInvocation invokes a registered, scoped read tool.
// @ID PostFrameworkMCPInvocation
// @Summary Invoke an allowed framework MCP read action
// @Tags Agent Registry
// @Accept json
// @Produce json
// @Security FrameworkPrincipalBearer
// @Param agent path string true "Configured agent name"
// @Param action path string true "Configured read action"
// @Param arguments body map[string]string true "Configured identifier arguments. Use {} for a tool with no caller arguments."
// @Success 200 {object} frameworkMCPInvocation
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/framework-agents/{agent}/actions/{action}/invoke [post]
func (h restHandler) RestPostFrameworkMCPInvocation(c echo.Context) error {
	action := c.Param("action")
	access, failure := h.frameworkMCPAccess(c, action)
	if failure != nil {
		return failure.respond(c)
	}
	binding, ok := access.peer.ReadTools[action]
	if !ok {
		return deniedFrameworkAction().respond(c)
	}
	record, failure := access.load()
	if failure != nil {
		return failure.respond(c)
	}
	arguments, ok := binding.scopedArguments(c.Request().Body, access.peer.Namespace)
	if !ok {
		return focusedError(c, http.StatusBadRequest, "INVALID_FRAMEWORK_MCP_ARGUMENTS",
			"Supply exactly the configured identifier arguments. The namespace is assigned by the registry.", nil)
	}
	ctx, cancel := h.frameworkMCPContext(c)
	defer cancel()
	session, contract, failure := h.openFrameworkMCP(ctx, access.peer)
	if failure != nil {
		access.audit(action, failure.code)
		return failure.respond(c)
	}
	defer session.Close()
	if record.ContractDigest != frameworkDigest(contract) {
		access.audit(action, "contract_changed")
		return changedFrameworkMCPContract().respond(c)
	}
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: binding.Name, Arguments: arguments})
	if err == nil {
		if payload, valid := frameworkMCPResult(result); valid {
			access.audit(action, "ok")
			return c.JSON(http.StatusOK, frameworkMCPInvocation{Agent: access.name, Action: action, Result: payload})
		}
	}
	access.audit(action, "upstream_error")
	return frameworkMCPUpstreamFailure().respond(c)
}

func (tool frameworkMCPTool) scopedArguments(body io.ReadCloser, namespace string) (map[string]string, bool) {
	dec := json.NewDecoder(http.MaxBytesReader(nil, body, 8<<10))
	var args map[string]string
	if dec.Decode(&args) != nil || dec.Decode(new(any)) != io.EOF || args == nil || len(args) != len(tool.Arguments) {
		return nil, false
	}
	for _, name := range tool.Arguments {
		if !aiAppNodeID.MatchString(args[name]) {
			return nil, false
		}
	}
	args[tool.NamespaceArgument] = namespace
	return args, true
}

func (h restHandler) frameworkMCPContext(c echo.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(aiAppEngineContext{c.Request().Context()}, h.config.AgentExecutionTimeout)
}

type boundedFrameworkMCPTransport struct{ aiAppEngineHeaders }

func (t boundedFrameworkMCPTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := t.aiAppEngineHeaders.RoundTrip(req)
	if err == nil {
		resp.Body = http.MaxBytesReader(nil, resp.Body, aiAppPartnerResponseLimit)
	}
	return resp, err
}

type frameworkMCPContract struct {
	Version string
	Tools   map[string]string
}

func (h restHandler) openFrameworkMCP(ctx context.Context, peer frameworkMCPPeer) (*mcp.ClientSession, frameworkMCPContract, *aiAppIntegrationFailure) {
	httpClient := partnerHTTPClient(h.config.AgentExecutionTimeout)
	httpClient.Transport = boundedFrameworkMCPTransport{aiAppEngineHeaders{namespace: peer.Namespace, token: peer.token}}
	session, err := connectPartnerMCP(ctx, httpClient, peer.Endpoint)
	if err != nil {
		return nil, frameworkMCPContract{}, frameworkMCPUpstreamFailure()
	}
	contract, failure := inspectFrameworkMCP(ctx, session, peer)
	if failure != nil {
		_ = session.Close()
		return nil, contract, failure
	}
	return session, contract, nil
}

func inspectFrameworkMCP(ctx context.Context, session *mcp.ClientSession, peer frameworkMCPPeer) (frameworkMCPContract, *aiAppIntegrationFailure) {
	server := session.InitializeResult().ServerInfo
	if server == nil || server.Name != peer.ServerName || strings.TrimSpace(server.Version) == "" || len(server.Version) > 128 {
		return frameworkMCPContract{}, changedFrameworkMCPContract()
	}
	contract := frameworkMCPContract{Version: server.Version, Tools: make(map[string]string, len(peer.ReadTools))}
	wanted := make(map[string]frameworkMCPTool, len(peer.ReadTools))
	for _, tool := range peer.ReadTools {
		wanted[tool.Name] = tool
	}
	for definition, err := range session.Tools(ctx, nil) {
		if err != nil || definition == nil {
			return contract, frameworkMCPUpstreamFailure()
		}
		binding, ok := wanted[definition.Name]
		if !ok {
			continue
		}
		if contract.Tools[definition.Name] != "" || !binding.matchesDefinition(definition, peer) {
			return contract, changedFrameworkMCPContract()
		}
		contract.Tools[definition.Name] = frameworkDigest([]any{definition.InputSchema, definition.OutputSchema, definition.Annotations})
	}
	if len(contract.Tools) != len(wanted) {
		return contract, changedFrameworkMCPContract()
	}
	return contract, nil
}

func (tool frameworkMCPTool) matchesDefinition(definition *mcp.Tool, peer frameworkMCPPeer) bool {
	hints := definition.Annotations
	if hints == nil {
		// The original CB-Tumblebug server omits hints for these two GET wrappers.
		list := tool.Name == "get_infra_list" && len(tool.Arguments) == 0
		detail := tool.Name == "get_infra" && len(tool.Arguments) == 1 && tool.Arguments[0] == "infra_id"
		if peer.Framework != "AI-Infra" || peer.ServerName != "cb-tumblebug" ||
			tool.NamespaceArgument != "ns_id" || (!list && !detail) {
			return false
		}
	} else if !hints.ReadOnlyHint || (hints.DestructiveHint != nil && *hints.DestructiveHint) {
		return false
	}
	if grade, ok := definition.Meta["ai-mcmp/grade"]; ok && grade != "read" {
		return false
	}
	raw, err := json.Marshal(definition.InputSchema)
	var schema struct {
		Type       string
		Required   []string
		Properties map[string]struct{ Type any }
	}
	if err != nil || json.Unmarshal(raw, &schema) != nil || schema.Type != "object" {
		return false
	}
	arguments := append([]string{tool.NamespaceArgument}, tool.Arguments...)
	for _, name := range arguments {
		if schema.Properties[name].Type != "string" {
			return false
		}
	}
	for _, name := range schema.Required {
		if !contains(arguments, name) {
			return false
		}
	}
	return true
}

func frameworkMCPResult(result *mcp.CallToolResult) (json.RawMessage, bool) {
	if result == nil || result.IsError {
		return nil, false
	}
	var raw []byte
	if result.StructuredContent != nil {
		var err error
		raw, err = json.Marshal(result.StructuredContent)
		if err != nil {
			return nil, false
		}
	} else if len(result.Content) == 1 {
		if content, ok := result.Content[0].(*mcp.TextContent); ok {
			raw = []byte(content.Text)
		}
	}
	raw = bytes.TrimSpace(raw)
	return raw, len(raw) > 0 && len(raw) <= aiAppPartnerResponseLimit && raw[0] == '{' && json.Valid(raw)
}

func frameworkMCPUpstreamFailure() *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusBadGateway, code: "FRAMEWORK_MCP_UPSTREAM_ERROR", message: "The framework MCP peer did not provide a usable response."}
}
