package llmselection

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
)

func TestSelectorRanksQualityFirstPolicy(t *testing.T) {
	selector := NewSelector(repositoryBenchmarkPath())
	result, err := selector.Select(context.Background(), "quality_first")
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	if !result.Valid || result.SelectedCandidateID != "qwen3.5-profile-generator" ||
		result.SelectedActualModel != "qwen3.5:4b" || result.SelectedScore != 0.891333 {
		t.Fatalf("selection = %#v", result)
	}
	if result.BenchmarkStatus != "not_executed" || len(result.Ranking) != 3 {
		t.Fatalf("selection metadata = %#v", result)
	}
}

func TestSelectorRanksCostFirstPolicy(t *testing.T) {
	selector := NewSelector(repositoryBenchmarkPath())
	result, err := selector.Select(context.Background(), "cost_first")
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	if result.SelectedCandidateID != "gemma3-profile-generator" {
		t.Fatalf("selected candidate = %q", result.SelectedCandidateID)
	}
}

func TestSelectorRejectsUnknownPolicy(t *testing.T) {
	selector := NewSelector(repositoryBenchmarkPath())
	_, err := selector.Select(context.Background(), "unknown")
	if !errors.Is(err, ErrUnknownPolicy) {
		t.Fatalf("Select() error = %v, want ErrUnknownPolicy", err)
	}
}

func TestSelectorPropagatesCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	selector := NewSelector(repositoryBenchmarkPath())
	_, err := selector.Select(ctx, "quality_first")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Select() error = %v, want context.Canceled", err)
	}
}

func repositoryBenchmarkPath() string {
	return filepath.Join("..", "..", "..", "..", "config", "ops_llm_benchmark.json")
}
