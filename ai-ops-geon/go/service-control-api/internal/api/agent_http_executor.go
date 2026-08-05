package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os"
	"strings"
	"time"
)

const maxAgentResponseBytes = 1 << 20

type agentExecutor interface {
	Execute(context.Context, AgentProfile, AgentDispatchRequest) (AgentExecutionResult, error)
}

type httpDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type httpAgentExecutor struct {
	client  httpDoer
	timeout time.Duration
}

func newHTTPAgentExecutor(client httpDoer, timeout time.Duration) agentExecutor {
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	return &httpAgentExecutor{client: client, timeout: timeout}
}

func newAgentHTTPClient(timeout time.Duration) *http.Client {
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	return &http.Client{
		Timeout: timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func (executor *httpAgentExecutor) Execute(
	ctx context.Context,
	agent AgentProfile,
	request AgentDispatchRequest,
) (AgentExecutionResult, error) {
	if executor == nil || executor.client == nil {
		return AgentExecutionResult{}, fmt.Errorf("runtime Agent HTTP client is required")
	}
	targetURL := strings.TrimRight(agent.Endpoint, "/") + agent.InvocationPath
	body, err := json.Marshal(request)
	if err != nil {
		return AgentExecutionResult{}, fmt.Errorf("encode runtime Agent request: %w", err)
	}

	requestContext, cancel := context.WithTimeout(ctx, executor.timeout)
	defer cancel()
	httpRequest, err := http.NewRequestWithContext(requestContext, http.MethodPost, targetURL, bytes.NewReader(body))
	if err != nil {
		return AgentExecutionResult{}, fmt.Errorf("build runtime Agent request: %w", err)
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("Accept", "application/json")
	if agent.AuthTokenEnv != "" {
		token := strings.TrimSpace(os.Getenv(agent.AuthTokenEnv))
		if token == "" {
			return AgentExecutionResult{}, fmt.Errorf(
				"runtime Agent authentication environment variable is not set: %s",
				agent.AuthTokenEnv,
			)
		}
		httpRequest.Header.Set("Authorization", "Bearer "+token)
	}

	startedAt := time.Now()
	response, err := executor.client.Do(httpRequest)
	latency := time.Since(startedAt)
	if err != nil {
		return AgentExecutionResult{}, fmt.Errorf("invoke runtime Agent: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return AgentExecutionResult{}, fmt.Errorf(
			"runtime Agent returned HTTP status %d",
			response.StatusCode,
		)
	}
	if contentType := strings.TrimSpace(response.Header.Get("Content-Type")); contentType != "" {
		mediaType, _, parseErr := mime.ParseMediaType(contentType)
		if parseErr != nil || mediaType != "application/json" {
			return AgentExecutionResult{}, fmt.Errorf("runtime Agent response must be application/json")
		}
	}

	content, err := io.ReadAll(io.LimitReader(response.Body, maxAgentResponseBytes+1))
	if err != nil {
		return AgentExecutionResult{}, fmt.Errorf("read runtime Agent response: %w", err)
	}
	if len(content) > maxAgentResponseBytes {
		return AgentExecutionResult{}, fmt.Errorf(
			"runtime Agent response exceeds %d bytes",
			maxAgentResponseBytes,
		)
	}
	var result AgentExecutionResult
	if err := json.Unmarshal(content, &result); err != nil {
		return AgentExecutionResult{}, fmt.Errorf("decode runtime Agent response: %w", err)
	}
	result.LatencyMS = latency.Milliseconds()
	if result.DomainValidation == "" {
		result.DomainValidation = "not_registered"
	}
	return result, nil
}
