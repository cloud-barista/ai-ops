package autonomy

import (
	"fmt"
	"math"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

type Mode string

const (
	ModeMonitorOnly Mode = "monitor_only"
	ModeGuardedAuto Mode = "guarded_auto"
)

type Action string

const (
	ActionObserve  Action = "observe_status"
	ActionScaleOut Action = "scale_out_application"
	ActionRestart  Action = "restart_application"
	ActionRollback Action = "rollback_application"
	ActionStop     Action = "stop_application"
)

func (action Action) Valid() bool {
	switch action {
	case ActionObserve, ActionScaleOut, ActionRestart, ActionRollback, ActionStop:
		return true
	default:
		return false
	}
}

type SLOPolicy struct {
	MaxLatencyMS     float64 `json:"max_latency_ms"`
	MinThroughputRPS float64 `json:"min_throughput_rps"`
	MaxErrorRate     float64 `json:"max_error_rate"`
}

type Config struct {
	Mode                    Mode      `json:"mode"`
	PollIntervalSeconds     int       `json:"poll_interval_seconds"`
	ConsecutiveViolations   int       `json:"consecutive_violations"`
	CooldownSeconds         int       `json:"cooldown_seconds"`
	MaxActionsPerDeployment int       `json:"max_actions_per_deployment"`
	MaxMetricAgeSeconds     int       `json:"max_metric_age_seconds"`
	DeploymentID            string    `json:"deployment_id"`
	StandbyTargetProfileID  string    `json:"standby_target_profile_id,omitempty"`
	RollbackAppVersionID    string    `json:"rollback_app_version_id,omitempty"`
	SLO                     SLOPolicy `json:"slo"`
}

func DefaultConfig() Config {
	return Config{
		Mode:                    ModeMonitorOnly,
		PollIntervalSeconds:     10,
		ConsecutiveViolations:   2,
		CooldownSeconds:         120,
		MaxActionsPerDeployment: 3,
		MaxMetricAgeSeconds:     60,
		SLO: SLOPolicy{
			MaxLatencyMS:     500,
			MinThroughputRPS: 1,
			MaxErrorRate:     0.05,
		},
	}
}

func (config Config) Validate() error {
	if config.Mode != ModeMonitorOnly && config.Mode != ModeGuardedAuto {
		return fmt.Errorf("mode must be monitor_only or guarded_auto")
	}
	if config.PollIntervalSeconds < 2 || config.PollIntervalSeconds > 3600 {
		return fmt.Errorf("poll_interval_seconds must be between 2 and 3600")
	}
	if config.ConsecutiveViolations < 1 || config.ConsecutiveViolations > 10 {
		return fmt.Errorf("consecutive_violations must be between 1 and 10")
	}
	if config.CooldownSeconds < 0 || config.CooldownSeconds > 86400 {
		return fmt.Errorf("cooldown_seconds must be between 0 and 86400")
	}
	if config.MaxActionsPerDeployment < 1 || config.MaxActionsPerDeployment > 20 {
		return fmt.Errorf("max_actions_per_deployment must be between 1 and 20")
	}
	if config.MaxMetricAgeSeconds < config.PollIntervalSeconds || config.MaxMetricAgeSeconds > 86400 {
		return fmt.Errorf("max_metric_age_seconds must be at least poll_interval_seconds and at most 86400")
	}
	if strings.TrimSpace(config.DeploymentID) == "" || strings.ContainsAny(config.DeploymentID, "\r\n\t ") {
		return fmt.Errorf("deployment_id must be a non-empty token")
	}
	for name, value := range map[string]string{
		"standby_target_profile_id": config.StandbyTargetProfileID,
		"rollback_app_version_id":   config.RollbackAppVersionID,
	} {
		if strings.ContainsAny(value, "\r\n\t ") {
			return fmt.Errorf("%s must be an empty value or a token", name)
		}
	}
	if !finiteNonNegative(config.SLO.MaxLatencyMS) || !finiteNonNegative(config.SLO.MinThroughputRPS) {
		return fmt.Errorf("latency and throughput SLO values must be finite and non-negative")
	}
	if !finiteNonNegative(config.SLO.MaxErrorRate) || config.SLO.MaxErrorRate > 1 {
		return fmt.Errorf("max_error_rate must be between 0 and 1")
	}
	return nil
}

func finiteNonNegative(value float64) bool {
	return value >= 0 && !math.IsNaN(value) && !math.IsInf(value, 0)
}

type EvaluationStatus string

const (
	EvaluationHealthy              EvaluationStatus = "healthy"
	EvaluationViolated             EvaluationStatus = "violated"
	EvaluationInsufficientEvidence EvaluationStatus = "insufficient_evidence"
)

type Violation struct {
	Code      string  `json:"code"`
	Observed  float64 `json:"observed,omitempty"`
	Threshold float64 `json:"threshold,omitempty"`
	Reason    string  `json:"reason"`
}

type EvaluationInput struct {
	Now                  time.Time
	Metric               *appdeploy.InferenceMetricRecord
	DeploymentStatus     string
	MonitoringStatus     string
	RuntimeHealth        string
	Policy               SLOPolicy
	MaxMetricAge         time.Duration
	EvidenceNotBefore    time.Time
	FailureEvidenceFresh bool
	PreviousConsecutive  int
}

type Evaluation struct {
	Status                EvaluationStatus                 `json:"status"`
	EvidenceFresh         bool                             `json:"evidence_fresh"`
	FailureEvidence       bool                             `json:"failure_evidence"`
	ConsecutiveViolations int                              `json:"consecutive_violations"`
	Metric                *appdeploy.InferenceMetricRecord `json:"metric,omitempty"`
	Violations            []Violation                      `json:"violations"`
	Reason                string                           `json:"reason"`
}

type DecisionInput struct {
	Deployment appdeploy.DeploymentResponse `json:"deployment"`
	Evaluation Evaluation                   `json:"evaluation"`
	Config     Config                       `json:"config"`
}

type Decision struct {
	Action     Action  `json:"action"`
	Reason     string  `json:"reason"`
	Confidence float64 `json:"confidence"`
	Raw        any     `json:"raw,omitempty"`
}

type GuardStatus string

const (
	GuardApproved     GuardStatus = "approved"
	GuardRejected     GuardStatus = "rejected"
	GuardBlocked      GuardStatus = "blocked"
	GuardWouldExecute GuardStatus = "would_execute"
)

type GuardInput struct {
	Mode                   Mode
	Action                 Action
	RegistryApproved       bool
	RegistryReason         string
	Evaluation             Evaluation
	RequiredConsecutive    int
	CooldownUntil          time.Time
	Now                    time.Time
	ActionCount            int
	MaxActions             int
	ActionAlreadyExecuted  bool
	StandbyTargetProfileID string
	RollbackAppVersionID   string
}

type GuardDecision struct {
	Status     GuardStatus `json:"status"`
	Approved   bool        `json:"approved"`
	Executable bool        `json:"executable"`
	Reason     string      `json:"reason"`
}
