package main

import (
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
