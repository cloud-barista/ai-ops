package api

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type ServerConfig struct {
	RepoRoot    string
	OpenAPIPath string
}

func NewServerConfig() ServerConfig {
	repoRoot := os.Getenv("AIOPS_REPO_ROOT")
	if repoRoot == "" {
		repoRoot = findRepoRoot()
	}
	return ServerConfig{
		RepoRoot:    repoRoot,
		OpenAPIPath: filepath.Join(repoRoot, "docs", "submission", "openapi_service_control.yaml"),
	}
}

func findRepoRoot() string {
	current, err := os.Getwd()
	if err != nil {
		return "."
	}

	for i := 0; i < 10; i++ {
		marker := filepath.Join(current, "config", "agent_registry.json")
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

func loadJSON[T any](path string) (T, error) {
	var value T
	bytes, err := os.ReadFile(path)
	if err != nil {
		return value, fmt.Errorf("read %s: %w", path, err)
	}
	if err := json.Unmarshal(bytes, &value); err != nil {
		return value, fmt.Errorf("parse %s: %w", path, err)
	}
	return value, nil
}

func (config ServerConfig) path(parts ...string) string {
	items := append([]string{config.RepoRoot}, parts...)
	return filepath.Join(items...)
}
