package profileapi

import (
	"path/filepath"
	"testing"

	"github.com/spf13/viper"
)

func TestNewConfigUsesApplicationProfileCandidateConfiguration(t *testing.T) {
	viper.Reset()
	t.Cleanup(viper.Reset)
	repoRoot := t.TempDir()
	t.Setenv("AIOPS_REPO_ROOT", repoRoot)

	config := NewConfig()
	if config.CandidateID != "qwen3.5-profile-generator" {
		t.Fatalf("CandidateID = %q", config.CandidateID)
	}
	if config.MaxLLMAttempts != 2 {
		t.Fatalf("MaxLLMAttempts = %d", config.MaxLLMAttempts)
	}
	wantPath := filepath.Join(repoRoot, "config", "application_profile_llm_candidates.json")
	if config.LLMCandidatesPath != wantPath {
		t.Fatalf("LLMCandidatesPath = %q, want %q", config.LLMCandidatesPath, wantPath)
	}
	wantBenchmarkPath := filepath.Join(repoRoot, "config", "ops_llm_benchmark.json")
	if config.OpsLLMBenchmarkPath != wantBenchmarkPath {
		t.Fatalf("OpsLLMBenchmarkPath = %q, want %q", config.OpsLLMBenchmarkPath, wantBenchmarkPath)
	}
	wantOpenAPIPath := filepath.Join(
		repoRoot,
		"go",
		"service-control-api",
		"docs",
		"swagger",
		"swagger.yaml",
	)
	if config.OpenAPIPath != wantOpenAPIPath {
		t.Fatalf("OpenAPIPath = %q, want %q", config.OpenAPIPath, wantOpenAPIPath)
	}
	if err := ValidateConfig(config); err != nil {
		t.Fatalf("ValidateConfig() error = %v", err)
	}
}
