package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	aiAppEngineMCPPath        = "/aiapp/engine/mcp"
	aiAppNamespaceHeader      = "X-AIMCMP-Namespace"
	aiAppPartnerResponseLimit = 4 << 20
)

var errAIAppToolRejected = errors.New("AI-App MCP tool rejected the request")

type aiAppEngineHeaders struct {
	namespace string
	token     string
}

// Each MCP hop negotiates its own protocol version while keeping the caller's deadline and cancellation.
type aiAppEngineContext struct{ context.Context }

func (aiAppEngineContext) Value(any) any { return nil }

func (transport aiAppEngineHeaders) RoundTrip(request *http.Request) (*http.Response, error) {
	request = request.Clone(request.Context())
	request.Header.Set(aiAppNamespaceHeader, transport.namespace)
	request.Header.Set("Authorization", "Bearer "+transport.token)
	return http.DefaultTransport.RoundTrip(request)
}

func partnerHTTPClient(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout:       timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

func callPartnerJSON(ctx context.Context, client *http.Client, method, endpoint, token string, body []byte, output any) (int, error) {
	req, err := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")
	if len(body) > 0 {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusBadGateway {
		return resp.StatusCode, fmt.Errorf("partner returned status %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, aiAppPartnerResponseLimit+1))
	if err != nil {
		return resp.StatusCode, err
	}
	if len(data) > aiAppPartnerResponseLimit {
		return resp.StatusCode, errors.New("partner response exceeded its size limit")
	}
	return resp.StatusCode, json.Unmarshal(data, output)
}

func (h restHandler) connectAIAppMCP(ctx context.Context, engine *registeredAIAppEngine, path, token string) (*mcp.ClientSession, error) {
	httpClient := partnerHTTPClient(h.config.AgentExecutionTimeout)
	httpClient.Transport = aiAppEngineHeaders{namespace: engine.Namespace, token: token}
	return connectPartnerMCP(ctx, httpClient, engine.Endpoint+path)
}

func connectPartnerMCP(ctx context.Context, httpClient *http.Client, endpoint string) (*mcp.ClientSession, error) {
	client := mcp.NewClient(&mcp.Implementation{Name: aiAppIntentSource, Version: aiAppIntentVersion}, nil)
	return client.Connect(ctx, &mcp.StreamableClientTransport{
		Endpoint:             endpoint,
		HTTPClient:           httpClient,
		DisableStandaloneSSE: true,
		MaxEventSize:         aiAppPartnerResponseLimit,
	}, nil)
}

func aiAppMCPTextResult(ctx context.Context, session *mcp.ClientSession, tool string, input any) (json.RawMessage, error) {
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: input})
	if err != nil {
		return nil, err
	}
	if result.IsError {
		return nil, errAIAppToolRejected
	}
	if len(result.Content) == 1 {
		if text, ok := result.Content[0].(*mcp.TextContent); ok && len(text.Text) <= aiAppPartnerResponseLimit {
			return json.RawMessage(text.Text), nil
		}
	}
	return nil, errors.New("engine tool did not return a readable result")
}

func (h restHandler) callAIAppReadTool(ctx context.Context, engine *registeredAIAppEngine, tool string, input map[string]string) (json.RawMessage, bool, error) {
	ctx = aiAppEngineContext{ctx}
	session, err := h.connectAIAppMCP(ctx, engine, engine.Card.MCPEndpoint, h.config.AIAppToken)
	if err != nil {
		return nil, false, err
	}
	defer session.Close()
	initialized := session.InitializeResult()
	if initialized.ServerInfo == nil || initialized.ServerInfo.Name != engine.Card.Name {
		return nil, false, errors.New("engine MCP server identity does not match the registration")
	}
	dryRun, _ := initialized.Meta["ai-mcmp/dryRun"].(bool)

	for definition, err := range session.Tools(ctx, nil) {
		if err != nil {
			return nil, dryRun, err
		}
		if definition.Name == tool && definition.Annotations != nil && definition.Annotations.ReadOnlyHint &&
			definition.Meta["ai-mcmp/grade"] == "read" {
			result, err := aiAppMCPTextResult(ctx, session, tool, input)
			return result, dryRun, err
		}
	}
	return nil, dryRun, errors.New("engine tool is unavailable as a read-only tool")
}
