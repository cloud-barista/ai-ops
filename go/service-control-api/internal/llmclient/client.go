package llmclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const maxResponseBytes = 4 << 20

type Candidate struct {
	CandidateID    string `json:"candidate_id"`
	RoleLabel      string `json:"role_label"`
	Provider       string `json:"provider"`
	ActualModel    string `json:"actual_model"`
	APIKeyEnv      string `json:"api_key_env"`
	Endpoint       string `json:"endpoint"`
	Enabled        bool   `json:"enabled"`
	JSONMode       bool   `json:"json_mode,omitempty"`
	TimeoutSeconds int    `json:"timeout_seconds"`
}

type CandidateConfig struct {
	Version         string      `json:"version"`
	BenchmarkMode   string      `json:"benchmark_mode"`
	BenchmarkStatus string      `json:"benchmark_status"`
	Description     string      `json:"description"`
	Candidates      []Candidate `json:"candidates"`
}

type Completion struct {
	Status      string
	Content     string
	LatencyMS   int64
	Provider    string
	ActualModel string
	CandidateID string
}

type Client struct {
	HTTPClient *http.Client
}

type chatResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

func NewClient(httpClient *http.Client) Client {
	return Client{HTTPClient: httpClient}
}

func (client Client) Complete(ctx context.Context, candidate Candidate, systemPrompt string, userPrompt string) (Completion, error) {
	result := Completion{
		Status:      "not_executed",
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if !candidate.Enabled {
		return result, fmt.Errorf("candidate is disabled")
	}
	if strings.TrimSpace(candidate.Endpoint) == "" {
		return result, fmt.Errorf("candidate endpoint is required")
	}
	if strings.TrimSpace(candidate.ActualModel) == "" {
		return result, fmt.Errorf("candidate actual model is required")
	}

	apiKey := ""
	if candidate.APIKeyEnv != "" {
		apiKey = os.Getenv(candidate.APIKeyEnv)
		if apiKey == "" {
			return result, fmt.Errorf("provider API key environment variable is not set")
		}
	}
	requestBody := map[string]any{
		"model":       candidate.ActualModel,
		"temperature": 0,
		"messages": []map[string]string{
			{"role": "system", "content": systemPrompt},
			{"role": "user", "content": userPrompt},
		},
	}
	if candidate.JSONMode {
		requestBody["response_format"] = map[string]string{"type": "json_object"}
	}
	body, err := json.Marshal(requestBody)
	if err != nil {
		return result, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, candidate.Endpoint, bytes.NewReader(body))
	if err != nil {
		return result, err
	}
	request.Header.Set("content-type", "application/json")
	if apiKey != "" {
		request.Header.Set("authorization", "Bearer "+apiKey)
	}

	timeout := time.Duration(candidate.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	httpClient := client.HTTPClient
	if httpClient == nil {
		httpClient = &http.Client{Timeout: timeout}
	} else if httpClient.Timeout == 0 {
		copy := *httpClient
		copy.Timeout = timeout
		httpClient = &copy
	}

	startedAt := time.Now()
	response, err := httpClient.Do(request)
	result.LatencyMS = time.Since(startedAt).Milliseconds()
	if err != nil {
		return result, err
	}
	defer func() {
		_ = response.Body.Close()
	}()
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes))
	if err != nil {
		return result, err
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return result, fmt.Errorf("provider returned status %d", response.StatusCode)
	}
	var parsed chatResponse
	if err := json.Unmarshal(responseBody, &parsed); err != nil {
		return result, fmt.Errorf("decode provider response: %w", err)
	}
	if len(parsed.Choices) == 0 || strings.TrimSpace(parsed.Choices[0].Message.Content) == "" {
		return result, fmt.Errorf("provider response did not contain assistant content")
	}
	result.Status = "executed"
	result.Content = strings.TrimSpace(parsed.Choices[0].Message.Content)
	return result, nil
}

func LoadCandidateConfig(path string) (CandidateConfig, error) {
	var config CandidateConfig
	content, err := os.ReadFile(path)
	if err != nil {
		return config, fmt.Errorf("read candidate config: %w", err)
	}
	if err := json.Unmarshal(content, &config); err != nil {
		return config, fmt.Errorf("parse candidate config: %w", err)
	}
	if len(config.Candidates) == 0 {
		return config, fmt.Errorf("candidate config must contain at least one candidate")
	}
	return config, nil
}

func FindEnabledCandidate(config CandidateConfig, candidateID string) (Candidate, error) {
	if strings.TrimSpace(candidateID) == "" {
		return Candidate{}, fmt.Errorf("candidate id is required")
	}
	for _, candidate := range config.Candidates {
		if candidate.CandidateID != candidateID {
			continue
		}
		if !candidate.Enabled {
			return Candidate{}, fmt.Errorf("candidate is disabled")
		}
		return candidate, nil
	}
	return Candidate{}, fmt.Errorf("unknown candidate id")
}
