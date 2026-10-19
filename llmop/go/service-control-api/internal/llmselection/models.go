// Package llmselection ranks configured Ops LLM candidates without performing
// deployment or Agent operations.
package llmselection

type Benchmark struct {
	Version    string            `json:"version"`
	Metadata   map[string]any    `json:"metadata"`
	Policies   map[string]Policy `json:"policies"`
	Candidates []Candidate       `json:"candidates"`
}

type Policy struct {
	Description string             `json:"description"`
	Weights     map[string]float64 `json:"weights"`
}

type Candidate struct {
	CandidateID           string   `json:"candidate_id,omitempty"`
	Model                 string   `json:"model"`
	ActualModel           string   `json:"actual_model"`
	Provider              string   `json:"provider"`
	EvaluationSource      string   `json:"evaluation_source"`
	EvaluationType        string   `json:"evaluation_type"`
	BenchmarkStatus       string   `json:"benchmark_status"`
	Role                  string   `json:"role"`
	CorrectDetectionRuns  float64  `json:"correct_detection_runs"`
	TotalDetectionRuns    float64  `json:"total_detection_runs"`
	MetricSuccessRuns     float64  `json:"metric_success_runs"`
	TotalMetricRuns       float64  `json:"total_metric_runs"`
	AverageTTDSeconds     float64  `json:"average_ttd_seconds"`
	AverageActionSteps    float64  `json:"average_action_steps,omitempty"`
	ActionValidityRate    float64  `json:"action_validity_rate"`
	ConsistencyScore      float64  `json:"consistency_score"`
	EstimatedCostPer1KOps float64  `json:"estimated_cost_per_1k_ops"`
	AverageLatencyMS      float64  `json:"average_latency_ms"`
	Notes                 []string `json:"notes"`
}

type SelectRequest struct {
	Policy string `json:"policy,omitempty" example:"quality_first"`
}

type SelectionResponse struct {
	Valid               bool         `json:"valid" validate:"required"`
	Policy              string       `json:"policy" validate:"required"`
	SelectedCandidateID string       `json:"selected_candidate_id,omitempty"`
	SelectedModel       string       `json:"selected_model" validate:"required"`
	SelectedActualModel string       `json:"selected_actual_model" validate:"required"`
	SelectedProvider    string       `json:"selected_provider" validate:"required"`
	EvaluationSource    string       `json:"evaluation_source" validate:"required"`
	EvaluationType      string       `json:"evaluation_type" validate:"required"`
	BenchmarkStatus     string       `json:"benchmark_status" validate:"required"`
	SelectedScore       float64      `json:"selected_score"`
	Rationale           string       `json:"rationale" validate:"required"`
	Ranking             []RankedItem `json:"ranking" validate:"required"`
}

type RankedItem struct {
	CandidateID      string             `json:"candidate_id,omitempty"`
	Model            string             `json:"model"`
	ActualModel      string             `json:"actual_model"`
	Provider         string             `json:"provider"`
	EvaluationSource string             `json:"evaluation_source"`
	EvaluationType   string             `json:"evaluation_type"`
	BenchmarkStatus  string             `json:"benchmark_status"`
	Role             string             `json:"role"`
	Score            float64            `json:"score"`
	Metrics          map[string]float64 `json:"metrics"`
	Notes            []string           `json:"notes"`
}
