package llmselection

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
)

const DefaultPolicy = "quality_first"

var ErrUnknownPolicy = errors.New("unknown LLM selection policy")

var supportedMetrics = map[string]struct{}{
	"accuracy":        {},
	"metric_success":  {},
	"action_validity": {},
	"consistency":     {},
	"ttd":             {},
	"cost":            {},
	"latency":         {},
}

type Selector struct {
	BenchmarkPath string
}

func NewSelector(benchmarkPath string) Selector {
	return Selector{BenchmarkPath: benchmarkPath}
}

func (selector Selector) Select(
	ctx context.Context,
	policyName string,
) (SelectionResponse, error) {
	if err := ctx.Err(); err != nil {
		return SelectionResponse{}, err
	}
	policyName = strings.TrimSpace(policyName)
	if policyName == "" {
		policyName = DefaultPolicy
	}
	config, err := loadBenchmark(selector.BenchmarkPath)
	if err != nil {
		return SelectionResponse{}, err
	}
	policy, ok := config.Policies[policyName]
	if !ok {
		return SelectionResponse{}, fmt.Errorf("%w: %s", ErrUnknownPolicy, policyName)
	}
	if err := validatePolicy(policyName, policy); err != nil {
		return SelectionResponse{}, err
	}
	if err := validateCandidates(config.Candidates); err != nil {
		return SelectionResponse{}, err
	}

	minTTD := minPositive(config.Candidates, func(candidate Candidate) float64 {
		return candidate.AverageTTDSeconds
	})
	minCost := minPositive(config.Candidates, func(candidate Candidate) float64 {
		return candidate.EstimatedCostPer1KOps
	})
	minLatency := minPositive(config.Candidates, func(candidate Candidate) float64 {
		return candidate.AverageLatencyMS
	})

	ranking := make([]RankedItem, 0, len(config.Candidates))
	for _, candidate := range config.Candidates {
		metrics := map[string]float64{
			"accuracy":        safeRatio(candidate.CorrectDetectionRuns, candidate.TotalDetectionRuns),
			"metric_success":  safeRatio(candidate.MetricSuccessRuns, candidate.TotalMetricRuns),
			"action_validity": candidate.ActionValidityRate,
			"consistency":     candidate.ConsistencyScore,
			"ttd":             inverseScore(minTTD, candidate.AverageTTDSeconds),
			"cost":            inverseScore(minCost, candidate.EstimatedCostPer1KOps),
			"latency":         inverseScore(minLatency, candidate.AverageLatencyMS),
		}
		score := 0.0
		for metric, weight := range policy.Weights {
			score += weight * metrics[metric]
		}
		ranking = append(ranking, RankedItem{
			CandidateID:      candidate.CandidateID,
			Model:            candidate.Model,
			ActualModel:      candidate.ActualModel,
			Provider:         candidate.Provider,
			EvaluationSource: candidate.EvaluationSource,
			EvaluationType:   candidate.EvaluationType,
			BenchmarkStatus:  candidate.BenchmarkStatus,
			Role:             candidate.Role,
			Score:            round6(score),
			Metrics:          roundMetrics(metrics),
			Notes:            append([]string(nil), candidate.Notes...),
		})
	}
	sort.SliceStable(ranking, func(i int, j int) bool {
		if ranking[i].Score == ranking[j].Score {
			return ranking[i].Model < ranking[j].Model
		}
		return ranking[i].Score > ranking[j].Score
	})

	selected := ranking[0]
	return SelectionResponse{
		Valid:               true,
		Policy:              policyName,
		SelectedCandidateID: selected.CandidateID,
		SelectedModel:       selected.Model,
		SelectedActualModel: selected.ActualModel,
		SelectedProvider:    selected.Provider,
		EvaluationSource:    selected.EvaluationSource,
		EvaluationType:      selected.EvaluationType,
		BenchmarkStatus:     selected.BenchmarkStatus,
		SelectedScore:       selected.Score,
		Rationale: fmt.Sprintf(
			"%s ranked first under %s because the weighted Ops accuracy, bounded-output validity, consistency, latency, and cost criteria produced the highest score.",
			selected.Model,
			policyName,
		),
		Ranking: ranking,
	}, nil
}

func loadBenchmark(path string) (Benchmark, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return Benchmark{}, fmt.Errorf("read Ops LLM benchmark: %w", err)
	}
	var benchmark Benchmark
	if err := json.Unmarshal(content, &benchmark); err != nil {
		return Benchmark{}, fmt.Errorf("parse Ops LLM benchmark: %w", err)
	}
	return benchmark, nil
}

func validatePolicy(name string, policy Policy) error {
	if len(policy.Weights) == 0 {
		return fmt.Errorf("LLM selection policy %s has no weights", name)
	}
	weightTotal := 0.0
	for metric, weight := range policy.Weights {
		if _, ok := supportedMetrics[metric]; !ok {
			return fmt.Errorf("LLM selection policy %s uses unknown metric %s", name, metric)
		}
		if weight < 0 || math.IsNaN(weight) || math.IsInf(weight, 0) {
			return fmt.Errorf("LLM selection policy %s has an invalid weight", name)
		}
		weightTotal += weight
	}
	if math.Abs(weightTotal-1) > 0.000001 {
		return fmt.Errorf("LLM selection policy %s weights must sum to 1", name)
	}
	return nil
}

func validateCandidates(candidates []Candidate) error {
	if len(candidates) == 0 {
		return fmt.Errorf("at least one LLM candidate is required")
	}
	seen := map[string]struct{}{}
	for _, candidate := range candidates {
		if strings.TrimSpace(candidate.Model) == "" {
			return fmt.Errorf("candidate model is required")
		}
		if _, ok := seen[candidate.Model]; ok {
			return fmt.Errorf("candidate model %s is duplicated", candidate.Model)
		}
		seen[candidate.Model] = struct{}{}
		if candidate.TotalDetectionRuns <= 0 ||
			candidate.CorrectDetectionRuns < 0 ||
			candidate.CorrectDetectionRuns > candidate.TotalDetectionRuns {
			return fmt.Errorf("candidate %s has invalid detection runs", candidate.Model)
		}
		if candidate.TotalMetricRuns <= 0 ||
			candidate.MetricSuccessRuns < 0 ||
			candidate.MetricSuccessRuns > candidate.TotalMetricRuns {
			return fmt.Errorf("candidate %s has invalid metric runs", candidate.Model)
		}
		if !unitInterval(candidate.ActionValidityRate) || !unitInterval(candidate.ConsistencyScore) {
			return fmt.Errorf("candidate %s has an invalid bounded metric", candidate.Model)
		}
		if candidate.AverageTTDSeconds <= 0 || candidate.EstimatedCostPer1KOps <= 0 || candidate.AverageLatencyMS <= 0 {
			return fmt.Errorf("candidate %s has an invalid positive metric", candidate.Model)
		}
	}
	return nil
}

func unitInterval(value float64) bool {
	return value >= 0 && value <= 1 && !math.IsNaN(value) && !math.IsInf(value, 0)
}

func safeRatio(numerator float64, denominator float64) float64 {
	if denominator <= 0 {
		return 0
	}
	return numerator / denominator
}

func minPositive(candidates []Candidate, value func(Candidate) float64) float64 {
	minimum := math.MaxFloat64
	for _, candidate := range candidates {
		current := value(candidate)
		if current > 0 && current < minimum {
			minimum = current
		}
	}
	if minimum == math.MaxFloat64 {
		return 0
	}
	return minimum
}

func inverseScore(minimum float64, value float64) float64 {
	if minimum <= 0 || value <= 0 {
		return 0
	}
	return minimum / value
}

func round6(value float64) float64 {
	return math.Round(value*1_000_000) / 1_000_000
}

func roundMetrics(metrics map[string]float64) map[string]float64 {
	rounded := make(map[string]float64, len(metrics))
	for key, value := range metrics {
		rounded[key] = round6(value)
	}
	return rounded
}
