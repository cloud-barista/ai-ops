// Package prometheus implements the temporary Prometheus ResourceSource.
// Metric names, PromQL, labels, and the Prometheus HTTP envelope are confined
// here so another delivery format can replace this adapter independently.
package prometheus

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"ai-mcmp/internal/resource"
)

const (
	defaultBaseURL      = "http://127.0.0.1:9090"
	defaultTimeout      = 10 * time.Second
	defaultMaxSampleAge = 2 * time.Minute
	maxResponseSize     = 8 << 20
)

type Config struct {
	BaseURL      string
	Timeout      time.Duration
	MaxSampleAge time.Duration
	BearerToken  string
	HTTPClient   *http.Client
	Now          func() time.Time
}

func DefaultConfig() Config {
	baseURL := firstNonEmpty(os.Getenv("RESOURCE_PROMETHEUS_URL"), os.Getenv("PROMETHEUS_URL"), defaultBaseURL)
	timeout := defaultTimeout
	if raw := strings.TrimSpace(os.Getenv("RESOURCE_PROMETHEUS_TIMEOUT")); raw != "" {
		if parsed, err := time.ParseDuration(raw); err == nil && parsed > 0 {
			timeout = parsed
		}
	}
	maxSampleAge := defaultMaxSampleAge
	if raw := strings.TrimSpace(os.Getenv("RESOURCE_PROMETHEUS_MAX_SAMPLE_AGE")); raw != "" {
		if parsed, err := time.ParseDuration(raw); err == nil && parsed > 0 {
			maxSampleAge = parsed
		}
	}
	return Config{
		BaseURL:      baseURL,
		Timeout:      timeout,
		MaxSampleAge: maxSampleAge,
		BearerToken:  strings.TrimSpace(os.Getenv("RESOURCE_PROMETHEUS_BEARER_TOKEN")),
	}
}

type Adapter struct {
	baseURL      string
	timeout      time.Duration
	maxSampleAge time.Duration
	bearerToken  string
	client       *http.Client
	now          func() time.Time
}

func New(config Config) (*Adapter, error) {
	baseURL := strings.TrimRight(strings.TrimSpace(config.BaseURL), "/")
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, fmt.Errorf("prometheus base URL must be an absolute http(s) URL")
	}
	if parsed.Path != "" && parsed.Path != "/" {
		return nil, fmt.Errorf("prometheus base URL must not include a path")
	}
	timeout := config.Timeout
	if timeout <= 0 {
		timeout = defaultTimeout
	}
	maxSampleAge := config.MaxSampleAge
	if maxSampleAge <= 0 {
		maxSampleAge = defaultMaxSampleAge
	}
	client := config.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: timeout}
	}
	now := config.Now
	if now == nil {
		now = time.Now
	}
	return &Adapter{
		baseURL:      baseURL,
		timeout:      timeout,
		maxSampleAge: maxSampleAge,
		bearerToken:  strings.TrimSpace(config.BearerToken),
		client:       client,
		now:          now,
	}, nil
}

func (adapter *Adapter) Name() string { return "prometheus" }

type queryDefinition struct {
	kind     string
	unit     string
	promQL   string
	required bool
	device   bool
}

var queries = []queryDefinition{
	{resource.FactNodeUp, "boolean", `up{job="node"}`, true, false},
	{resource.FactCPUCount, "cores", `count(count(node_cpu_seconds_total{job="node"}) by (node_id,hostname,node_ip,cpu)) by (node_id,hostname,node_ip)`, true, false},
	{resource.FactCPUUtilizationPercent, "percent", `100 * (1 - avg by (node_id,hostname,node_ip) (rate(node_cpu_seconds_total{job="node",mode="idle"}[5m])))`, true, false},
	{resource.FactMemoryTotalBytes, "bytes", `node_memory_MemTotal_bytes{job="node"}`, true, false},
	{resource.FactMemoryAvailableBytes, "bytes", `node_memory_MemAvailable_bytes{job="node"}`, true, false},
	{resource.FactStorageTotalBytes, "bytes", `node_filesystem_size_bytes{job="node",mountpoint="/",fstype!~"tmpfs|overlay"}`, false, false},
	{resource.FactStorageAvailableBytes, "bytes", `node_filesystem_avail_bytes{job="node",mountpoint="/",fstype!~"tmpfs|overlay"}`, false, false},
	{resource.FactNodeInfo, "info", `node_uname_info{job="node"}`, false, false},
	{resource.FactOSInfo, "info", `node_os_info{job="node"}`, false, false},
	{resource.FactAcceleratorExporterUp, "boolean", `up{job="dcgm"}`, false, false},
	{resource.FactGPUUtilizationPercent, "percent", `DCGM_FI_DEV_GPU_UTIL{job="dcgm"}`, false, true},
	{resource.FactGPUMemoryFreeMiB, "MiB", `DCGM_FI_DEV_FB_FREE{job="dcgm"}`, false, true},
	{resource.FactGPUMemoryUsedMiB, "MiB", `DCGM_FI_DEV_FB_USED{job="dcgm"}`, false, true},
	{resource.FactGPUMemoryReservedMiB, "MiB", `DCGM_FI_DEV_FB_RESERVED{job="dcgm"}`, false, true},
}

type collectedQuery struct {
	index    int
	facts    []resource.RawFact
	warnings []string
	err      error
}

func (adapter *Adapter) Collect(ctx context.Context) (resource.RawSnapshot, error) {
	if adapter == nil {
		return resource.RawSnapshot{}, errors.New("prometheus adapter is nil")
	}
	queryAt := adapter.now().UTC()
	requestContext, cancel := context.WithTimeout(ctx, adapter.timeout)
	defer cancel()

	results := make(chan collectedQuery, len(queries))
	var group sync.WaitGroup
	for index, definition := range queries {
		group.Add(1)
		go func() {
			defer group.Done()
			facts, warnings, err := adapter.execute(requestContext, definition, queryAt)
			results <- collectedQuery{index: index, facts: facts, warnings: warnings, err: err}
		}()
	}
	group.Wait()
	close(results)

	ordered := make([]collectedQuery, len(queries))
	for result := range results {
		ordered[result.index] = result
	}
	raw := resource.RawSnapshot{
		Source:   adapter.Name(),
		Facts:    []resource.RawFact{},
		Warnings: []string{},
	}
	for index, result := range ordered {
		definition := queries[index]
		if result.err != nil {
			if definition.required {
				return resource.RawSnapshot{}, fmt.Errorf("collect required Prometheus fact %s: %w", definition.kind, result.err)
			}
			raw.Warnings = append(raw.Warnings, "OPTIONAL_PROMETHEUS_QUERY_FAILED:"+definition.kind)
			continue
		}
		if definition.required && len(result.facts) == 0 {
			return resource.RawSnapshot{}, fmt.Errorf("required Prometheus fact %s returned no fresh data within %s", definition.kind, adapter.maxSampleAge)
		}
		raw.Facts = append(raw.Facts, result.facts...)
		raw.Warnings = append(raw.Warnings, result.warnings...)
	}
	// The query evaluation time is not a measurement timestamp. The oldest
	// accepted sample is the conservative observation time of this snapshot.
	for _, fact := range raw.Facts {
		if raw.ObservedAt.IsZero() || fact.ObservedAt.Before(raw.ObservedAt) {
			raw.ObservedAt = fact.ObservedAt
		}
	}
	return raw, nil
}

func (adapter *Adapter) execute(ctx context.Context, definition queryDefinition, queryAt time.Time) ([]resource.RawFact, []string, error) {
	endpoint, err := url.Parse(adapter.baseURL + "/api/v1/query")
	if err != nil {
		return nil, nil, err
	}
	parameters := endpoint.Query()
	parameters.Set("query", definition.promQL)
	parameters.Set("time", strconv.FormatFloat(float64(queryAt.UnixNano())/1e9, 'f', 9, 64))
	endpoint.RawQuery = parameters.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, nil, err
	}
	request.Header.Set("Accept", "application/json")
	if adapter.bearerToken != "" {
		request.Header.Set("Authorization", "Bearer "+adapter.bearerToken)
	}
	response, err := adapter.client.Do(request)
	if err != nil {
		return nil, nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 1024))
		return nil, nil, fmt.Errorf("Prometheus returned HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}

	var payload apiResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	if err := decoder.Decode(&payload); err != nil {
		return nil, nil, fmt.Errorf("decode Prometheus response: %w", err)
	}
	if payload.Status != "success" {
		return nil, nil, fmt.Errorf("Prometheus query failed: %s", firstNonEmpty(payload.Error, payload.ErrorType, "unknown error"))
	}
	if payload.Data.ResultType != "vector" {
		return nil, nil, fmt.Errorf("Prometheus returned result type %q; want vector", payload.Data.ResultType)
	}

	facts := make([]resource.RawFact, 0, len(payload.Data.Result))
	staleCount := 0
	futureCount := 0
	for _, sample := range payload.Data.Result {
		fact, err := factFromSample(definition, sample)
		if err != nil {
			return nil, nil, err
		}
		if fact.ObservedAt.IsZero() || queryAt.Sub(fact.ObservedAt) > adapter.maxSampleAge {
			staleCount++
			continue
		}
		if fact.ObservedAt.After(queryAt) {
			futureCount++
			continue
		}
		facts = append(facts, fact)
	}
	warnings := make([]string, 0, len(payload.Warnings))
	for _, warning := range payload.Warnings {
		warnings = append(warnings, "PROMETHEUS_WARNING:"+definition.kind+":"+warning)
	}
	if staleCount > 0 {
		warnings = append(warnings, fmt.Sprintf("STALE_PROMETHEUS_SAMPLES_DROPPED:%s:%d", definition.kind, staleCount))
	}
	if futureCount > 0 {
		warnings = append(warnings, fmt.Sprintf("FUTURE_PROMETHEUS_SAMPLES_DROPPED:%s:%d", definition.kind, futureCount))
	}
	return facts, warnings, nil
}

type apiResponse struct {
	Status    string   `json:"status"`
	ErrorType string   `json:"errorType"`
	Error     string   `json:"error"`
	Warnings  []string `json:"warnings"`
	Data      struct {
		ResultType string      `json:"resultType"`
		Result     []apiSample `json:"result"`
	} `json:"data"`
}

type apiSample struct {
	Metric map[string]string `json:"metric"`
	Value  []json.RawMessage `json:"value"`
}

func factFromSample(definition queryDefinition, sample apiSample) (resource.RawFact, error) {
	if len(sample.Value) != 2 {
		return resource.RawFact{}, fmt.Errorf("Prometheus %s sample has invalid value tuple", definition.kind)
	}
	var timestamp float64
	if err := json.Unmarshal(sample.Value[0], &timestamp); err != nil || math.IsNaN(timestamp) || math.IsInf(timestamp, 0) {
		return resource.RawFact{}, fmt.Errorf("Prometheus %s sample has invalid timestamp", definition.kind)
	}
	var valueText string
	if err := json.Unmarshal(sample.Value[1], &valueText); err != nil {
		return resource.RawFact{}, fmt.Errorf("Prometheus %s sample has non-string value", definition.kind)
	}
	value, err := strconv.ParseFloat(valueText, 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return resource.RawFact{}, fmt.Errorf("Prometheus %s sample has invalid numeric value %q", definition.kind, valueText)
	}
	resourceID := resourceID(sample.Metric)
	if resourceID == "" {
		return resource.RawFact{}, fmt.Errorf("Prometheus %s sample has no node identity", definition.kind)
	}
	deviceID := ""
	if definition.device {
		deviceID = firstNonEmpty(sample.Metric["UUID"], sample.Metric["gpu"], sample.Metric["device"])
	}
	seconds, fraction := math.Modf(timestamp)
	observedAt := time.Unix(int64(seconds), int64(fraction*1e9)).UTC()
	return resource.RawFact{
		ResourceID: resourceID,
		DeviceID:   deviceID,
		Kind:       definition.kind,
		Value:      value,
		Unit:       definition.unit,
		Labels:     cloneLabels(sample.Metric),
		ObservedAt: observedAt,
	}, nil
}

func resourceID(labels map[string]string) string {
	if value := strings.TrimSpace(labels["node_id"]); value != "" {
		return value
	}
	hostname := strings.TrimSpace(firstNonEmpty(labels["hostname"], labels["exported_hostname"]))
	address := strings.TrimSpace(labels["node_ip"])
	if hostname != "" && address != "" {
		return hostname + "-" + strings.ReplaceAll(address, ".", "-")
	}
	if hostname != "" {
		return hostname
	}
	instance := strings.TrimSpace(labels["instance"])
	if host, _, err := net.SplitHostPort(instance); err == nil {
		return host
	}
	return instance
}

func cloneLabels(labels map[string]string) map[string]string {
	copy := make(map[string]string, len(labels))
	for key, value := range labels {
		copy[key] = value
	}
	return copy
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
