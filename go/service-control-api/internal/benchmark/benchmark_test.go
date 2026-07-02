package benchmark

import (
	"bufio"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunOpsLLMBenchmarkDryRunCreatesNonExecutedOutputs(t *testing.T) {
	outputDir := t.TempDir()

	result, err := RunOpsLLMBenchmark(RunOptions{
		ScenariosPath:  filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl"),
		CandidatesPath: filepath.Join("..", "..", "..", "..", "config", "ops_llm_eval_candidates.json"),
		OutputDir:      outputDir,
		DryRun:         true,
	})
	if err != nil {
		t.Fatalf("RunOpsLLMBenchmark returned error: %v", err)
	}
	if !result.Valid {
		t.Fatalf("expected dry-run result to be valid")
	}
	if result.BenchmarkStatus != "dry_run" {
		t.Fatalf("expected benchmark_status dry_run, got %q", result.BenchmarkStatus)
	}
	if result.OutputsPath == "" {
		t.Fatalf("expected outputs path")
	}

	rows := readJSONL(t, result.OutputsPath)
	if len(rows) == 0 {
		t.Fatalf("expected dry-run output rows")
	}
	for _, row := range rows {
		if row["benchmark_status"] == "executed" {
			t.Fatalf("dry-run row must not be marked executed: %#v", row)
		}
		if row["actual_model"] == "" {
			t.Fatalf("expected actual_model linkage in row: %#v", row)
		}
		if row["prompt"] == "" {
			t.Fatalf("expected prompt in dry-run row: %#v", row)
		}
	}
}

func TestEvaluateOpsLLMOutputsKeepsDryRunSeparateFromExecutedBenchmark(t *testing.T) {
	outputDir := t.TempDir()
	runResult, err := RunOpsLLMBenchmark(RunOptions{
		ScenariosPath:  filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl"),
		CandidatesPath: filepath.Join("..", "..", "..", "..", "config", "ops_llm_eval_candidates.json"),
		OutputDir:      outputDir,
		DryRun:         true,
	})
	if err != nil {
		t.Fatalf("RunOpsLLMBenchmark returned error: %v", err)
	}

	summary, err := EvaluateOpsLLMOutputs(EvaluateOptions{
		ScenariosPath: filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl"),
		OutputsPath:   runResult.OutputsPath,
		SummaryPath:   filepath.Join(outputDir, "evaluation_summary.json"),
	})
	if err != nil {
		t.Fatalf("EvaluateOpsLLMOutputs returned error: %v", err)
	}
	if !summary.Valid {
		t.Fatalf("expected dry-run evaluation summary to be valid")
	}
	if summary.BenchmarkStatus != "dry_run" {
		t.Fatalf("expected dry-run evaluation status, got %q", summary.BenchmarkStatus)
	}
	if summary.SelectedActualModel != "" {
		t.Fatalf("dry-run must not select a final actual model, got %q", summary.SelectedActualModel)
	}
	if _, err := os.Stat(summary.SummaryPath); err != nil {
		t.Fatalf("expected summary file to exist: %v", err)
	}
}

func TestRunOpsLLMBenchmarkExecutedModeCallsOpenAICompatibleProvider(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Fatalf("expected POST request, got %s", r.Method)
		}
		if !strings.HasSuffix(r.URL.Path, "/v1/chat/completions") {
			t.Fatalf("expected chat completions path, got %s", r.URL.Path)
		}
		w.Header().Set("content-type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"scale_replicas\",\"reason\":\"latency SLO violation\",\"confidence\":0.91}"}}]}`))
	}))
	defer server.Close()

	dir := t.TempDir()
	scenariosPath := filepath.Join(dir, "scenarios.jsonl")
	candidatesPath := filepath.Join(dir, "candidates.json")
	writeFile(t, scenariosPath, `{"id":"ops-test","scenario":"Scale the service.","allowed_actions":["scale_replicas","monitor_latency"],"expected_action":"scale_replicas","required_output_fields":["action","reason","confidence"]}`+"\n")
	writeFile(t, candidatesPath, `{"version":"1","candidates":[{"candidate_id":"local-provider","role_label":"primary-ops-llm","provider":"local-openai-compatible","actual_model":"test-model","endpoint":"`+server.URL+`/v1/chat/completions","enabled":true}]}`)

	result, err := RunOpsLLMBenchmark(RunOptions{
		ScenariosPath:  scenariosPath,
		CandidatesPath: candidatesPath,
		OutputDir:      filepath.Join(dir, "outputs"),
	})
	if err != nil {
		t.Fatalf("RunOpsLLMBenchmark returned error: %v", err)
	}
	if result.BenchmarkStatus != "executed" {
		t.Fatalf("expected executed benchmark, got %q", result.BenchmarkStatus)
	}

	rows := readJSONL(t, result.OutputsPath)
	if len(rows) != 1 {
		t.Fatalf("expected one provider output row, got %d", len(rows))
	}
	if rows[0]["benchmark_status"] != "executed" {
		t.Fatalf("expected executed output row: %#v", rows[0])
	}
	if rows[0]["dry_run"] == true {
		t.Fatalf("executed provider call must not be marked dry_run: %#v", rows[0])
	}
}

func TestRunOpsLLMBenchmarkExecutedModeFailsWhenNoCandidateRuns(t *testing.T) {
	dir := t.TempDir()
	scenariosPath := filepath.Join(dir, "scenarios.jsonl")
	candidatesPath := filepath.Join(dir, "candidates.json")
	writeFile(t, scenariosPath, `{"id":"ops-test","scenario":"Scale the service.","allowed_actions":["scale_replicas"],"expected_action":"scale_replicas","required_output_fields":["action"]}`+"\n")
	writeFile(t, candidatesPath, `{"version":"1","candidates":[{"candidate_id":"disabled-provider","role_label":"primary-ops-llm","provider":"local-openai-compatible","actual_model":"test-model","endpoint":"http://127.0.0.1:1/v1/chat/completions","enabled":false}]}`)

	_, err := RunOpsLLMBenchmark(RunOptions{
		ScenariosPath:  scenariosPath,
		CandidatesPath: candidatesPath,
		OutputDir:      filepath.Join(dir, "outputs"),
	})
	if err == nil {
		t.Fatal("expected executed benchmark to fail when no candidate actually runs")
	}
	if !strings.Contains(err.Error(), "no enabled LLM candidate executed") {
		t.Fatalf("expected no executed candidate error, got %v", err)
	}
}

func TestEvaluateOpsLLMOutputsScoresExecutedJSONResponses(t *testing.T) {
	dir := t.TempDir()
	scenariosPath := filepath.Join(dir, "scenarios.jsonl")
	outputsPath := filepath.Join(dir, "model_outputs.jsonl")
	summaryPath := filepath.Join(dir, "evaluation_summary.json")

	writeFile(t, scenariosPath, `{"id":"ops-test","scenario":"Scale the service.","allowed_actions":["scale_replicas","monitor_latency"],"expected_action":"scale_replicas","required_output_fields":["action","reason","confidence"]}`+"\n")
	writeFile(t, outputsPath, `{"scenario_id":"ops-test","candidate_id":"candidate-a","role_label":"primary-ops-llm","actual_model":"ops-model-a","provider":"test-provider","benchmark_status":"executed","latency_ms":100,"raw_response":"{\"action\":\"scale_replicas\",\"reason\":\"latency SLO violation\",\"confidence\":0.91}"}`+"\n")

	summary, err := EvaluateOpsLLMOutputs(EvaluateOptions{
		ScenariosPath: scenariosPath,
		OutputsPath:   outputsPath,
		SummaryPath:   summaryPath,
	})
	if err != nil {
		t.Fatalf("EvaluateOpsLLMOutputs returned error: %v", err)
	}
	if summary.BenchmarkStatus != "executed" {
		t.Fatalf("expected executed status, got %q", summary.BenchmarkStatus)
	}
	if summary.SelectedActualModel != "ops-model-a" {
		t.Fatalf("expected selected actual model ops-model-a, got %q", summary.SelectedActualModel)
	}
	if len(summary.Candidates) != 1 {
		t.Fatalf("expected one candidate summary, got %d", len(summary.Candidates))
	}
	if summary.Candidates[0].AverageScore < 0.99 {
		t.Fatalf("expected high score for valid response, got %f", summary.Candidates[0].AverageScore)
	}
}

func readJSONL(t *testing.T, path string) []map[string]any {
	t.Helper()

	file, err := os.Open(path)
	if err != nil {
		t.Fatalf("failed to open %s: %v", path, err)
	}
	defer file.Close()

	rows := []map[string]any{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		row := map[string]any{}
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			t.Fatalf("failed to parse jsonl row: %v", err)
		}
		rows = append(rows, row)
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("failed to scan jsonl: %v", err)
	}
	return rows
}

func writeFile(t *testing.T, path string, value string) {
	t.Helper()

	if err := os.WriteFile(path, []byte(value), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}
}
