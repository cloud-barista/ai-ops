package profileapi

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/viper"
)

const defaultMaxRequestBytes int64 = 128 << 10

type Config struct {
	RepoRoot            string
	BindAddress         string
	OpenAPIPath         string
	LLMCandidatesPath   string
	OpsLLMBenchmarkPath string
	CandidateID         string
	MaxLLMAttempts      int
	MaxRequestBytes     int64
}

func NewConfig() Config {
	viper.SetEnvPrefix("AIOPS")
	viper.AutomaticEnv()

	repoRoot := strings.TrimSpace(viper.GetString("REPO_ROOT"))
	if repoRoot == "" {
		repoRoot = findRepoRoot()
	}
	openAPIPath := resolvePath(repoRoot, viper.GetString("OPENAPI_PATH"))
	if strings.TrimSpace(viper.GetString("OPENAPI_PATH")) == "" {
		openAPIPath = filepath.Join(
			repoRoot,
			"go",
			"service-control-api",
			"docs",
			"swagger",
			"swagger.yaml",
		)
	}
	candidatesPath := resolvePath(repoRoot, viper.GetString("LLM_CANDIDATES_PATH"))
	if strings.TrimSpace(viper.GetString("LLM_CANDIDATES_PATH")) == "" {
		candidatesPath = filepath.Join(repoRoot, "config", "application_profile_llm_candidates.json")
	}
	benchmarkPath := resolvePath(repoRoot, viper.GetString("OPS_LLM_BENCHMARK_PATH"))
	if strings.TrimSpace(viper.GetString("OPS_LLM_BENCHMARK_PATH")) == "" {
		benchmarkPath = filepath.Join(repoRoot, "config", "ops_llm_benchmark.json")
	}
	bindAddress := strings.TrimSpace(viper.GetString("BIND_ADDRESS"))
	if bindAddress == "" {
		bindAddress = "127.0.0.1"
	}
	candidateID := strings.TrimSpace(viper.GetString("LLMOP_CANDIDATE_ID"))
	if candidateID == "" {
		candidateID = "qwen3.5-profile-generator"
	}
	maxRequestBytes := viper.GetInt64("LLMOP_MAX_REQUEST_BYTES")
	if maxRequestBytes <= 0 {
		maxRequestBytes = defaultMaxRequestBytes
	}
	maxLLMAttempts := viper.GetInt("LLMOP_MAX_LLM_ATTEMPTS")
	if maxLLMAttempts <= 0 {
		maxLLMAttempts = 2
	}

	return Config{
		RepoRoot:            repoRoot,
		BindAddress:         bindAddress,
		OpenAPIPath:         openAPIPath,
		LLMCandidatesPath:   candidatesPath,
		OpsLLMBenchmarkPath: benchmarkPath,
		CandidateID:         candidateID,
		MaxLLMAttempts:      maxLLMAttempts,
		MaxRequestBytes:     maxRequestBytes,
	}
}

func ValidateConfig(config Config) error {
	if strings.TrimSpace(config.BindAddress) == "" {
		return fmt.Errorf("bind address is required")
	}
	if strings.TrimSpace(config.OpenAPIPath) == "" {
		return fmt.Errorf("OpenAPI path is required")
	}
	if strings.TrimSpace(config.LLMCandidatesPath) == "" {
		return fmt.Errorf("LLM candidates path is required")
	}
	if strings.TrimSpace(config.OpsLLMBenchmarkPath) == "" {
		return fmt.Errorf("Ops LLM benchmark path is required")
	}
	if strings.TrimSpace(config.CandidateID) == "" {
		return fmt.Errorf("LLM_Op candidate ID is required")
	}
	if config.MaxLLMAttempts < 1 || config.MaxLLMAttempts > 3 {
		return fmt.Errorf("LLM_Op maximum LLM attempts must be between 1 and 3")
	}
	if config.MaxRequestBytes <= 0 || config.MaxRequestBytes > 1<<20 {
		return fmt.Errorf("LLM_Op maximum request bytes must be between 1 and 1048576")
	}
	return nil
}

func resolvePath(repoRoot string, value string) string {
	value = strings.TrimSpace(value)
	if value == "" || filepath.IsAbs(value) {
		return value
	}
	return filepath.Join(repoRoot, value)
}

func findRepoRoot() string {
	current, err := os.Getwd()
	if err != nil {
		return "."
	}
	for range 10 {
		marker := filepath.Join(current, "config", "application_profile_llm_candidates.json")
		if _, err := os.Stat(marker); err == nil {
			return current
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
		current = parent
	}
	return "."
}
