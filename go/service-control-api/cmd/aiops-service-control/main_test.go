package main

import (
	"encoding/json"
	"os"
	"path/filepath"
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
