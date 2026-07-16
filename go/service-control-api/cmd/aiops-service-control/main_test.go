package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunOpsLLMBenchmarkCommandCreatesDryRunOutputs(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"run-ops-llm-benchmark",
		"--scenarios", filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl"),
		"--candidates", filepath.Join("..", "..", "..", "..", "config", "ops_llm_eval_candidates.json"),
		"--output-dir", outputDir,
		"--dry-run",
	})
	if err != nil {
		t.Fatalf("run returned error: %v", err)
	}
	if _, err := os.Stat(filepath.Join(outputDir, "model_outputs.jsonl")); err != nil {
		t.Fatalf("expected dry-run model outputs: %v", err)
	}
}

func TestEvaluateOpsLLMOutputsCommandCreatesSummary(t *testing.T) {
	outputDir := t.TempDir()
	scenarios := filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl")
	candidates := filepath.Join("..", "..", "..", "..", "config", "ops_llm_eval_candidates.json")

	if err := run([]string{
		"run-ops-llm-benchmark",
		"--scenarios", scenarios,
		"--candidates", candidates,
		"--output-dir", outputDir,
		"--dry-run",
	}); err != nil {
		t.Fatalf("run benchmark returned error: %v", err)
	}

	summaryPath := filepath.Join(outputDir, "evaluation_summary.json")
	err := run([]string{
		"evaluate-ops-llm-outputs",
		"--scenarios", scenarios,
		"--outputs", filepath.Join(outputDir, "model_outputs.jsonl"),
		"--summary", summaryPath,
	})
	if err != nil {
		t.Fatalf("evaluate returned error: %v", err)
	}
	if _, err := os.Stat(summaryPath); err != nil {
		t.Fatalf("expected evaluation summary: %v", err)
	}
}

func TestValidateSystemLocalCommandCreatesSummaryAndEnvironmentEvidence(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}

	summaryPath := filepath.Join(outputDir, "00_system_validation_summary.json")
	environmentPath := filepath.Join(outputDir, "01_environment.json")
	if _, err := os.Stat(summaryPath); err != nil {
		t.Fatalf("expected system validation summary: %v", err)
	}
	if _, err := os.Stat(environmentPath); err != nil {
		t.Fatalf("expected environment evidence: %v", err)
	}

	var summary map[string]any
	bytes, err := os.ReadFile(summaryPath)
	if err != nil {
		t.Fatalf("failed to read summary: %v", err)
	}
	if err := json.Unmarshal(bytes, &summary); err != nil {
		t.Fatalf("failed to parse summary: %v", err)
	}
	if summary["command"] != "validate-system" {
		t.Fatalf("expected validate-system command, got %#v", summary["command"])
	}
	if summary["target"] != "local" {
		t.Fatalf("expected local target, got %#v", summary["target"])
	}
	steps, ok := summary["steps"].([]any)
	if !ok {
		t.Fatalf("expected steps array in summary: %#v", summary["steps"])
	}
	assertStepSkipped(t, steps, "ops-llm-evaluation", "--run-llm-benchmark was not enabled")
	assertStepSkipped(t, steps, "api-integration-validation", "--run-api-integration was not enabled")
}

func TestValidateSystemRejectsUnknownTarget(t *testing.T) {
	err := run([]string{
		"validate-system",
		"--target", "edge",
		"--output-dir", t.TempDir(),
		"--skip-go-tests",
	})
	if err == nil {
		t.Fatal("expected invalid target error")
	}
}

func TestValidateSystemCanRunExecutedLLMBenchmark(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("content-type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"scale_instances\",\"reason\":\"latency SLO violation\",\"confidence\":0.91}"}}]}`))
	}))
	defer server.Close()

	dir := t.TempDir()
	scenariosPath := filepath.Join(dir, "scenarios.jsonl")
	candidatesPath := filepath.Join(dir, "candidates.json")
	writeMainTestFile(t, scenariosPath, `{"id":"ops-test","scenario":"Scale the service.","allowed_actions":["scale_instances","monitor_latency"],"expected_action":"scale_instances","required_output_fields":["action","reason","confidence"]}`+"\n")
	writeMainTestFile(t, candidatesPath, `{"version":"1","candidates":[{"candidate_id":"local-provider","role_label":"primary-ops-llm","provider":"local-openai-compatible","actual_model":"test-model","endpoint":"`+server.URL+`/v1/chat/completions","enabled":true}]}`)

	outputDir := filepath.Join(dir, "validation")
	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
		"--skip-team-validation",
		"--run-llm-benchmark",
		"--llm-scenarios", scenariosPath,
		"--llm-candidates", candidatesPath,
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}

	summaryBytes, err := os.ReadFile(filepath.Join(outputDir, "00_system_validation_summary.json"))
	if err != nil {
		t.Fatalf("failed to read system validation summary: %v", err)
	}
	summary := string(summaryBytes)
	if !strings.Contains(summary, "ops-llm-evaluation") {
		t.Fatalf("expected LLM benchmark step in summary: %s", summary)
	}

	benchmarkSummaryPath := filepath.Join(outputDir, "ops-llm-benchmark", "evaluation_summary.json")
	benchmarkSummaryBytes, err := os.ReadFile(benchmarkSummaryPath)
	if err != nil {
		t.Fatalf("expected executed LLM benchmark summary: %v", err)
	}
	if !strings.Contains(string(benchmarkSummaryBytes), `"benchmark_status": "executed"`) {
		t.Fatalf("expected executed benchmark summary: %s", string(benchmarkSummaryBytes))
	}
}

func TestAPIIntegrationValidationCommandCreatesSummary(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"api-integration-validation",
		"--output-dir", outputDir,
		"--port", "0",
	})
	if err != nil {
		t.Fatalf("api-integration-validation returned error: %v", err)
	}

	summaryPath := filepath.Join(outputDir, "api-integration-validation-summary.json")
	bytes, err := os.ReadFile(summaryPath)
	if err != nil {
		t.Fatalf("expected API integration summary: %v", err)
	}
	var summary map[string]any
	if err := json.Unmarshal(bytes, &summary); err != nil {
		t.Fatalf("failed to parse API integration summary: %v", err)
	}
	if summary["command"] != "api-integration-validation" {
		t.Fatalf("expected api-integration-validation command, got %#v", summary["command"])
	}
	if summary["valid"] != true {
		t.Fatalf("expected valid API integration validation, got %s", string(bytes))
	}
	if int(summary["endpoint_count"].(float64)) != 8 {
		t.Fatalf("expected eight endpoints including external agent registration, got %#v", summary["endpoint_count"])
	}
	if int(summary["valid_endpoint_count"].(float64)) != 8 {
		t.Fatalf("expected eight valid endpoints, got %#v", summary["valid_endpoint_count"])
	}
	if summary["production_level_validation"] != false {
		t.Fatalf("local API integration must not claim production validation")
	}
}

func TestValidateSystemCanRunAPIIntegrationValidation(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
		"--skip-team-validation",
		"--run-api-integration",
		"--api-port", "0",
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}

	summaryBytes, err := os.ReadFile(filepath.Join(outputDir, "00_system_validation_summary.json"))
	if err != nil {
		t.Fatalf("failed to read system validation summary: %v", err)
	}
	var summary map[string]any
	if err := json.Unmarshal(summaryBytes, &summary); err != nil {
		t.Fatalf("failed to parse summary: %v", err)
	}
	steps := summary["steps"].([]any)
	step := findStep(t, steps, "api-integration-validation")
	if step["skipped"] == true {
		t.Fatalf("api integration step should have run: %#v", step)
	}
	if step["valid"] != true {
		t.Fatalf("api integration step should be valid: %#v", step)
	}
	if step["output_path"] == "" {
		t.Fatalf("api integration step should record output path: %#v", step)
	}
}

func findStep(t *testing.T, steps []any, name string) map[string]any {
	t.Helper()
	for _, raw := range steps {
		step, ok := raw.(map[string]any)
		if !ok {
			t.Fatalf("step is not object: %#v", raw)
		}
		if step["name"] == name {
			return step
		}
	}
	t.Fatalf("step %s not found in %#v", name, steps)
	return nil
}

func assertStepSkipped(t *testing.T, steps []any, name string, reason string) {
	t.Helper()
	step := findStep(t, steps, name)
	if step["valid"] != true {
		t.Fatalf("skipped step should be valid: %#v", step)
	}
	if step["skipped"] != true {
		t.Fatalf("expected step %s to be skipped: %#v", name, step)
	}
	if step["reason"] != reason {
		t.Fatalf("expected skip reason %q, got %#v", reason, step["reason"])
	}
}

func writeMainTestFile(t *testing.T, path string, value string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(value), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}
}
