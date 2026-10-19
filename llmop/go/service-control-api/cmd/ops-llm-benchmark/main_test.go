package main

import (
	"bytes"
	"context"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunSelectOpsLLM(t *testing.T) {
	var output bytes.Buffer
	err := run(context.Background(), []string{
		"select-ops-llm",
		"--config", filepath.Join(findRepoRoot(), "config", "ops_llm_benchmark.json"),
		"--policy", "cost_first",
	}, &output)
	if err != nil {
		t.Fatalf("run() error = %v", err)
	}
	if !strings.Contains(output.String(), `"selected_candidate_id": "gemma3-profile-generator"`) ||
		!strings.Contains(output.String(), `"benchmark_status": "not_executed"`) {
		t.Fatalf("output = %s", output.String())
	}
}

func TestRunBenchmarkDryRun(t *testing.T) {
	var output bytes.Buffer
	err := run(context.Background(), []string{
		"run-ops-llm-benchmark",
		"--scenarios", filepath.Join(findRepoRoot(), "data", "ops_llm_eval_scenarios.jsonl"),
		"--candidates", filepath.Join(findRepoRoot(), "config", "ops_llm_eval_candidates.json"),
		"--output-dir", t.TempDir(),
		"--dry-run",
	}, &output)
	if err != nil {
		t.Fatalf("run() error = %v", err)
	}
	if !strings.Contains(output.String(), `"benchmark_status": "dry_run"`) ||
		!strings.Contains(output.String(), `"scenario_count": 10`) {
		t.Fatalf("output = %s", output.String())
	}
}
