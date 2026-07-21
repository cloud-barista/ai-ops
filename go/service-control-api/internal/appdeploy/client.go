package appdeploy

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const maxResponseBytes = 4 << 20

type Client struct {
	baseURL    *url.URL
	httpClient *http.Client
}

func NewClient(baseURL string, httpClient *http.Client) (Client, error) {
	parsed, err := url.Parse(strings.TrimSpace(baseURL))
	if err != nil {
		return Client{}, fmt.Errorf("parse AppDeploy base URL: %w", err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return Client{}, fmt.Errorf("AppDeploy base URL must use http or https")
	}
	if parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return Client{}, fmt.Errorf("AppDeploy base URL must not contain credentials, query parameters, or fragments")
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	} else if httpClient.Timeout == 0 {
		copy := *httpClient
		copy.Timeout = 30 * time.Second
		httpClient = &copy
	}
	return Client{baseURL: parsed, httpClient: httpClient}, nil
}

func (client Client) CreateDeployment(ctx context.Context, manifest DeploymentManifest) (DeploymentResponse, error) {
	var response DeploymentResponse
	err := client.doJSON(ctx, http.MethodPost, "/deployments", DeploymentCreateRequest{Manifest: manifest}, &response)
	if err != nil {
		return response, err
	}
	if strings.TrimSpace(response.DeploymentID) == "" {
		return response, fmt.Errorf("AppDeploy response did not contain deployment_id")
	}
	return response, nil
}

func (client Client) GetDeployment(ctx context.Context, deploymentID string) (DeploymentResponse, error) {
	var response DeploymentResponse
	if strings.TrimSpace(deploymentID) == "" {
		return response, fmt.Errorf("deployment_id is required")
	}
	err := client.doJSON(ctx, http.MethodGet, "/deployments/"+url.PathEscape(deploymentID), nil, &response)
	return response, err
}

func (client Client) GetDeploymentLogs(ctx context.Context, deploymentID string) (DeploymentLogsResponse, error) {
	var response DeploymentLogsResponse
	if strings.TrimSpace(deploymentID) == "" {
		return response, fmt.Errorf("deployment_id is required")
	}
	err := client.doJSON(ctx, http.MethodGet, "/deployments/"+url.PathEscape(deploymentID)+"/logs", nil, &response)
	if len(response.Items) == 0 && len(response.Logs) > 0 {
		response.Items = append([]DeploymentLog(nil), response.Logs...)
	}
	return response, err
}

func (client Client) ListDeployments(ctx context.Context) (DeploymentListResponse, error) {
	var response DeploymentListResponse
	err := client.doJSON(ctx, http.MethodGet, "/deployments", nil, &response)
	if len(response.Items) == 0 && len(response.Deployments) > 0 {
		response.Items = append([]DeploymentResponse(nil), response.Deployments...)
	}
	return response, err
}

func (client Client) ListDeploymentMetrics(ctx context.Context, deploymentID string) (DeploymentMetricsResponse, error) {
	var response DeploymentMetricsResponse
	if strings.TrimSpace(deploymentID) == "" {
		return response, fmt.Errorf("deployment_id is required")
	}
	err := client.doJSON(ctx, http.MethodGet, "/deployments/"+url.PathEscape(deploymentID)+"/metrics", nil, &response)
	if len(response.Items) == 0 && len(response.Metrics) > 0 {
		response.Items = append([]InferenceMetricRecord(nil), response.Metrics...)
	}
	return response, err
}

func (client Client) GetMonitoringSummary(ctx context.Context) (MonitoringSummaryResponse, error) {
	var response MonitoringSummaryResponse
	err := client.doJSON(ctx, http.MethodGet, "/monitoring/summary", nil, &response)
	return response, err
}

func (client Client) StopDeployment(ctx context.Context, deploymentID string) (DeploymentResponse, error) {
	var response DeploymentResponse
	if strings.TrimSpace(deploymentID) == "" {
		return response, fmt.Errorf("deployment_id is required")
	}
	err := client.doJSON(ctx, http.MethodPost, "/deployments/"+url.PathEscape(deploymentID)+"/stop", nil, &response)
	return response, err
}

func (client Client) doJSON(ctx context.Context, method string, path string, input any, output any) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var body io.Reader
	if input != nil {
		content, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(content)
	}
	endpoint := *client.baseURL
	endpoint.Path = strings.TrimRight(client.baseURL.Path, "/") + path
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), body)
	if err != nil {
		return err
	}
	request.Header.Set("accept", "application/json")
	if input != nil {
		request.Header.Set("content-type", "application/json")
	}
	response, err := client.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer func() {
		_ = response.Body.Close()
	}()
	content, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
	if err != nil {
		return err
	}
	if len(content) > maxResponseBytes {
		return fmt.Errorf("AppDeploy response exceeds %d bytes", maxResponseBytes)
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return decodeAPIError(response.StatusCode, content)
	}
	if err := json.Unmarshal(content, output); err != nil {
		return fmt.Errorf("decode AppDeploy response: %w", err)
	}
	return nil
}

func decodeAPIError(statusCode int, content []byte) error {
	var response ErrorResponse
	if err := json.Unmarshal(content, &response); err != nil {
		return &APIError{StatusCode: statusCode}
	}
	return &APIError{
		StatusCode: statusCode,
		RequestID:  response.RequestID,
		Code:       response.ErrorBody.Code,
		Message:    response.ErrorBody.Message,
		Retryable:  response.ErrorBody.Retryable,
	}
}
