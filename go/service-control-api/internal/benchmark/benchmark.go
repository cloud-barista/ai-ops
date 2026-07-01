package benchmark

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type RunOptions struct {
	ScenariosPath  string
	CandidatesPath string
	OutputDir      string
	DryRun         bool
}

type RunResult struct {
	Command         string   `json:"command"`
	Valid           bool     `json:"valid"`
	BenchmarkStatus string   `json:"benchmark_status"`
	ScenariosPath   string   `json:"scenarios_path"`
	CandidatesPath  string   `json:"candidates_path"`
	OutputDir       string   `json:"output_dir"`
	OutputsPath     string   `json:"outputs_path"`
	DryRun          bool     `json:"dry_run"`
	ScenarioCount   int      `json:"scenario_count"`
	CandidateCount  int      `json:"candidate_count"`
	OutputCount     int      `json:"output_count"`
	GeneratedAt     string   `json:"generated_at"`
	Notes           []string `json:"notes"`
}

type EvaluateOptions struct {
	ScenariosPath string
	OutputsPath   string
	SummaryPath   string
}

type EvaluationSummary struct {
	Command             string                `json:"command"`
	Valid               bool                  `json:"valid"`
	BenchmarkStatus     string                `json:"benchmark_status"`
	ScenariosPath       string                `json:"scenarios_path"`
	OutputsPath         string                `json:"outputs_path"`
	SummaryPath         string                `json:"summary_path"`
	ScenarioCount       int                   `json:"scenario_count"`
	OutputCount         int                   `json:"output_count"`
	SelectedCandidateID string                `json:"selected_candidate_id,omitempty"`
	SelectedRoleLabel   string                `json:"selected_role_label,omitempty"`
	SelectedActualModel string                `json:"selected_actual_model,omitempty"`
	SelectedProvider    string                `json:"selected_provider,omitempty"`
	Candidates          []CandidateEvaluation `json:"candidates"`
	Notes               []string              `json:"notes"`
}

type CandidateEvaluation struct {
	CandidateID  string  `json:"candidate_id"`
	RoleLabel    string  `json:"role_label"`
	ActualModel  string  `json:"actual_model"`
	Provider     string  `json:"provider"`
	OutputCount  int     `json:"output_count"`
	Executed     int     `json:"executed"`
	DryRun       int     `json:"dry_run"`
	Skipped      int     `json:"skipped"`
	AverageScore float64 `json:"average_score"`
}

type scenario struct {
	ID                   string         `json:"id"`
	Title                string         `json:"title"`
	Scenario             string         `json:"scenario"`
	Context              map[string]any `json:"context"`
	AllowedActions       []string       `json:"allowed_actions"`
	ExpectedAction       string         `json:"expected_action"`
	RequiredOutputFields []string       `json:"required_output_fields"`
	EvaluationFocus      []string       `json:"evaluation_focus"`
}

type candidateConfig struct {
	Version         string      `json:"version"`
	BenchmarkStatus string      `json:"benchmark_status"`
	Description     string      `json:"description"`
	Candidates      []candidate `json:"candidates"`
}

type candidate struct {
	CandidateID string `json:"candidate_id"`
	RoleLabel   string `json:"role_label"`
	Provider    string `json:"provider"`
	ActualModel string `json:"actual_model"`
	APIKeyEnv   string `json:"api_key_env"`
	Endpoint    string `json:"endpoint"`
	Enabled     bool   `json:"enabled"`
}

type modelOutput struct {
	ScenarioID      string         `json:"scenario_id"`
	CandidateID     string         `json:"candidate_id"`
	RoleLabel       string         `json:"role_label"`
	Provider        string         `json:"provider"`
	ActualModel     string         `json:"actual_model"`
	BenchmarkStatus string         `json:"benchmark_status"`
	DryRun          bool           `json:"dry_run"`
	Skipped         bool           `json:"skipped"`
	Prompt          string         `json:"prompt,omitempty"`
	LatencyMS       int64          `json:"latency_ms,omitempty"`
	RawResponse     string         `json:"raw_response,omitempty"`
	ParsedResponse  map[string]any `json:"parsed_response,omitempty"`
	Error           string         `json:"error,omitempty"`
	CreatedAt       string         `json:"created_at"`
}

type openAIChatResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

func RunOpsLLMBenchmark(options RunOptions) (RunResult, error) {
	if options.ScenariosPath == "" {
		return RunResult{}, fmt.Errorf("scenarios path is required")
	}
	if options.CandidatesPath == "" {
		return RunResult{}, fmt.Errorf("candidates path is required")
	}
	if options.OutputDir == "" {
		options.OutputDir = filepath.Join("runs", "ops-llm-evaluation-"+time.Now().Format("20060102-150405"))
	}

	scenarios, err := loadScenarios(options.ScenariosPath)
	if err != nil {
		return RunResult{}, err
	}
	config, err := loadCandidateConfig(options.CandidatesPath)
	if err != nil {
		return RunResult{}, err
	}
	outputDir, err := filepath.Abs(options.OutputDir)
	if err != nil {
		return RunResult{}, err
	}
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return RunResult{}, err
	}

	outputsPath := filepath.Join(outputDir, "model_outputs.jsonl")
	file, err := os.Create(outputsPath)
	if err != nil {
		return RunResult{}, err
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	status := "not_executed"
	if options.DryRun {
		status = "dry_run"
	}
	outputCount := 0
	for _, candidate := range config.Candidates {
		for _, scenario := range scenarios {
			prompt := buildPrompt(scenario)
			output := modelOutput{
				ScenarioID:      scenario.ID,
				CandidateID:     candidate.CandidateID,
				RoleLabel:       candidate.RoleLabel,
				Provider:        candidate.Provider,
				ActualModel:     candidate.ActualModel,
				BenchmarkStatus: status,
				DryRun:          options.DryRun,
				Skipped:         options.DryRun || !candidate.Enabled,
				Prompt:          prompt,
				CreatedAt:       time.Now().UTC().Format(time.RFC3339),
			}
			if !options.DryRun && !candidate.Enabled {
				output.BenchmarkStatus = "not_executed"
				output.Error = "candidate disabled; no provider API call executed"
			}
			if !options.DryRun && candidate.Enabled {
				executed, callErr := callOpenAICompatible(candidate, prompt)
				output.BenchmarkStatus = executed.BenchmarkStatus
				output.Skipped = executed.Skipped
				output.LatencyMS = executed.LatencyMS
				output.RawResponse = executed.RawResponse
				output.ParsedResponse = executed.ParsedResponse
				if callErr != nil {
					output.Error = callErr.Error()
				}
				if output.BenchmarkStatus == "executed" {
					status = "executed"
				}
			}
			if err := encoder.Encode(output); err != nil {
				return RunResult{}, err
			}
			outputCount++
		}
	}

	return RunResult{
		Command:         "run-ops-llm-benchmark",
		Valid:           true,
		BenchmarkStatus: status,
		ScenariosPath:   options.ScenariosPath,
		CandidatesPath:  options.CandidatesPath,
		OutputDir:       outputDir,
		OutputsPath:     outputsPath,
		DryRun:          options.DryRun,
		ScenarioCount:   len(scenarios),
		CandidateCount:  len(config.Candidates),
		OutputCount:     outputCount,
		GeneratedAt:     time.Now().UTC().Format(time.RFC3339),
		Notes: []string{
			"Dry-run output is not an executed LLM benchmark.",
			"No external Python runner is used.",
		},
	}, nil
}

func EvaluateOpsLLMOutputs(options EvaluateOptions) (EvaluationSummary, error) {
	if options.ScenariosPath == "" {
		return EvaluationSummary{}, fmt.Errorf("scenarios path is required")
	}
	if options.OutputsPath == "" {
		return EvaluationSummary{}, fmt.Errorf("outputs path is required")
	}
	if options.SummaryPath == "" {
		options.SummaryPath = filepath.Join(filepath.Dir(options.OutputsPath), "evaluation_summary.json")
	}

	scenarios, err := loadScenarios(options.ScenariosPath)
	if err != nil {
		return EvaluationSummary{}, err
	}
	outputs, err := loadOutputs(options.OutputsPath)
	if err != nil {
		return EvaluationSummary{}, err
	}

	scenarioByID := map[string]scenario{}
	for _, scenario := range scenarios {
		scenarioByID[scenario.ID] = scenario
	}

	type aggregate struct {
		CandidateEvaluation
		scoreTotal float64
	}
	aggregates := map[string]*aggregate{}
	anyExecuted := false
	anyDryRun := false
	for _, output := range outputs {
		key := output.CandidateID
		if key == "" {
			key = output.RoleLabel + "/" + output.ActualModel
		}
		if _, ok := aggregates[key]; !ok {
			aggregates[key] = &aggregate{
				CandidateEvaluation: CandidateEvaluation{
					CandidateID: output.CandidateID,
					RoleLabel:   output.RoleLabel,
					ActualModel: output.ActualModel,
					Provider:    output.Provider,
				},
			}
		}
		agg := aggregates[key]
		agg.OutputCount++

		if output.DryRun || output.BenchmarkStatus == "dry_run" {
			anyDryRun = true
			agg.DryRun++
			continue
		}
		if output.Skipped || output.BenchmarkStatus != "executed" {
			agg.Skipped++
			continue
		}
		anyExecuted = true
		agg.Executed++
		score := scoreOutput(scenarioByID[output.ScenarioID], output)
		agg.scoreTotal += score
	}

	candidates := make([]CandidateEvaluation, 0, len(aggregates))
	for _, aggregate := range aggregates {
		if aggregate.Executed > 0 {
			aggregate.AverageScore = round4(aggregate.scoreTotal / float64(aggregate.Executed))
		}
		candidates = append(candidates, aggregate.CandidateEvaluation)
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].AverageScore == candidates[j].AverageScore {
			return candidates[i].CandidateID < candidates[j].CandidateID
		}
		return candidates[i].AverageScore > candidates[j].AverageScore
	})

	status := "not_executed"
	if anyDryRun {
		status = "dry_run"
	}
	if anyExecuted {
		status = "executed"
	}

	summary := EvaluationSummary{
		Command:         "evaluate-ops-llm-outputs",
		Valid:           true,
		BenchmarkStatus: status,
		ScenariosPath:   options.ScenariosPath,
		OutputsPath:     options.OutputsPath,
		SummaryPath:     options.SummaryPath,
		ScenarioCount:   len(scenarios),
		OutputCount:     len(outputs),
		Candidates:      candidates,
		Notes: []string{
			"selected_actual_model is populated only when benchmark_status is executed.",
			"Dry-run summaries verify evaluation wiring, not provider model quality.",
		},
	}
	if status == "executed" && len(candidates) > 0 {
		summary.SelectedCandidateID = candidates[0].CandidateID
		summary.SelectedRoleLabel = candidates[0].RoleLabel
		summary.SelectedActualModel = candidates[0].ActualModel
		summary.SelectedProvider = candidates[0].Provider
	}

	if err := os.MkdirAll(filepath.Dir(options.SummaryPath), 0o755); err != nil {
		return EvaluationSummary{}, err
	}
	bytes, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		return EvaluationSummary{}, err
	}
	if err := os.WriteFile(options.SummaryPath, append(bytes, '\n'), 0o644); err != nil {
		return EvaluationSummary{}, err
	}
	return summary, nil
}

func loadScenarios(path string) ([]scenario, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	var scenarios []scenario
	scanner := bufio.NewScanner(file)
	line := 0
	for scanner.Scan() {
		line++
		text := strings.TrimSpace(scanner.Text())
		if text == "" {
			continue
		}
		var scenario scenario
		if err := json.Unmarshal([]byte(text), &scenario); err != nil {
			return nil, fmt.Errorf("%s:%d: %w", path, line, err)
		}
		if scenario.ID == "" {
			return nil, fmt.Errorf("%s:%d: scenario id is required", path, line)
		}
		scenarios = append(scenarios, scenario)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(scenarios) == 0 {
		return nil, fmt.Errorf("%s has no scenarios", path)
	}
	return scenarios, nil
}

func loadCandidateConfig(path string) (candidateConfig, error) {
	bytes, err := os.ReadFile(path)
	if err != nil {
		return candidateConfig{}, err
	}
	var config candidateConfig
	if err := json.Unmarshal(bytes, &config); err != nil {
		return candidateConfig{}, err
	}
	if len(config.Candidates) == 0 {
		return candidateConfig{}, fmt.Errorf("%s has no candidates", path)
	}
	for index, candidate := range config.Candidates {
		if candidate.CandidateID == "" {
			return candidateConfig{}, fmt.Errorf("%s candidate[%d] candidate_id is required", path, index)
		}
		if candidate.ActualModel == "" {
			return candidateConfig{}, fmt.Errorf("%s candidate[%d] actual_model is required", path, index)
		}
	}
	return config, nil
}

func loadOutputs(path string) ([]modelOutput, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	var outputs []modelOutput
	scanner := bufio.NewScanner(file)
	line := 0
	for scanner.Scan() {
		line++
		text := strings.TrimSpace(scanner.Text())
		if text == "" {
			continue
		}
		var output modelOutput
		if err := json.Unmarshal([]byte(text), &output); err != nil {
			return nil, fmt.Errorf("%s:%d: %w", path, line, err)
		}
		outputs = append(outputs, output)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(outputs) == 0 {
		return nil, fmt.Errorf("%s has no model outputs", path)
	}
	return outputs, nil
}

func buildPrompt(scenario scenario) string {
	contextBytes, _ := json.Marshal(scenario.Context)
	return fmt.Sprintf(
		"Return JSON only. Required fields: %s. Allowed actions: %s. Scenario ID: %s. Scenario: %s. Context: %s.",
		strings.Join(scenario.RequiredOutputFields, ", "),
		strings.Join(scenario.AllowedActions, ", "),
		scenario.ID,
		scenario.Scenario,
		string(contextBytes),
	)
}

func callOpenAICompatible(candidate candidate, prompt string) (modelOutput, error) {
	if candidate.Endpoint == "" {
		return modelOutput{BenchmarkStatus: "not_executed", Skipped: true}, fmt.Errorf("candidate endpoint is required")
	}
	apiKey := ""
	if candidate.APIKeyEnv != "" {
		apiKey = os.Getenv(candidate.APIKeyEnv)
		if apiKey == "" {
			return modelOutput{BenchmarkStatus: "not_executed", Skipped: true}, fmt.Errorf("%s is not set; no provider API call executed", candidate.APIKeyEnv)
		}
	}

	requestBody := map[string]any{
		"model":       candidate.ActualModel,
		"temperature": 0,
		"messages": []map[string]string{
			{"role": "system", "content": "You are an AI service-control evaluator. Return only compact JSON."},
			{"role": "user", "content": prompt},
		},
	}
	bodyBytes, err := json.Marshal(requestBody)
	if err != nil {
		return modelOutput{BenchmarkStatus: "not_executed", Skipped: true}, err
	}
	request, err := http.NewRequest(http.MethodPost, candidate.Endpoint, bytes.NewReader(bodyBytes))
	if err != nil {
		return modelOutput{BenchmarkStatus: "not_executed", Skipped: true}, err
	}
	request.Header.Set("content-type", "application/json")
	if apiKey != "" {
		request.Header.Set("authorization", "Bearer "+apiKey)
	}

	start := time.Now()
	response, err := http.DefaultClient.Do(request)
	latency := time.Since(start).Milliseconds()
	if err != nil {
		return modelOutput{BenchmarkStatus: "not_executed", Skipped: true, LatencyMS: latency}, err
	}
	defer response.Body.Close()

	responseBytes, readErr := io.ReadAll(response.Body)
	if readErr != nil {
		return modelOutput{BenchmarkStatus: "not_executed", Skipped: true, LatencyMS: latency}, readErr
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return modelOutput{
			BenchmarkStatus: "not_executed",
			Skipped:         true,
			LatencyMS:       latency,
			RawResponse:     string(responseBytes),
		}, fmt.Errorf("provider returned status %d", response.StatusCode)
	}

	raw := extractAssistantContent(responseBytes)
	parsed := parseJSONMap(raw)
	return modelOutput{
		BenchmarkStatus: "executed",
		Skipped:         false,
		LatencyMS:       latency,
		RawResponse:     raw,
		ParsedResponse:  parsed,
	}, nil
}

func extractAssistantContent(responseBytes []byte) string {
	var response openAIChatResponse
	if err := json.Unmarshal(responseBytes, &response); err == nil && len(response.Choices) > 0 {
		content := strings.TrimSpace(response.Choices[0].Message.Content)
		if content != "" {
			return content
		}
	}
	return strings.TrimSpace(string(responseBytes))
}

func scoreOutput(scenario scenario, output modelOutput) float64 {
	response := output.ParsedResponse
	if len(response) == 0 {
		response = parseJSONMap(output.RawResponse)
	}
	if len(response) == 0 {
		return 0
	}

	jsonValidity := 1.0
	actionValidity := 0.0
	expectedActionMatch := 0.0
	requiredFieldsScore := 0.0
	latencyScore := 0.0

	action, _ := response["action"].(string)
	if contains(scenario.AllowedActions, action) {
		actionValidity = 1.0
	}
	if scenario.ExpectedAction != "" && action == scenario.ExpectedAction {
		expectedActionMatch = 1.0
	}
	if len(scenario.RequiredOutputFields) == 0 {
		requiredFieldsScore = 1.0
	} else {
		present := 0
		for _, field := range scenario.RequiredOutputFields {
			if _, ok := response[field]; ok {
				present++
			}
		}
		requiredFieldsScore = float64(present) / float64(len(scenario.RequiredOutputFields))
	}
	if output.LatencyMS > 0 {
		latencyScore = math.Min(1.0, 1000.0/float64(output.LatencyMS))
	}

	return round4(jsonValidity*0.25 + actionValidity*0.25 + requiredFieldsScore*0.20 + expectedActionMatch*0.20 + latencyScore*0.10)
}

func parseJSONMap(value string) map[string]any {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	candidates := []string{value}
	if extracted := extractJSONObject(value); extracted != "" && extracted != value {
		candidates = append([]string{extracted}, candidates...)
	}
	for _, candidate := range candidates {
		parsed := map[string]any{}
		if err := json.Unmarshal([]byte(candidate), &parsed); err == nil {
			return parsed
		}
	}
	return nil
}

func extractJSONObject(value string) string {
	start := strings.Index(value, "{")
	end := strings.LastIndex(value, "}")
	if start < 0 || end < start {
		return ""
	}
	return value[start : end+1]
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func round4(value float64) float64 {
	return math.Round(value*10000) / 10000
}
