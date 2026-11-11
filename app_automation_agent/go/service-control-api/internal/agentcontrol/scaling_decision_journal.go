package agentcontrol

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// ScalingDecisionSnapshot is a local KHU decision trace. Sender-declared
// measurements remain source claims; this is not an execution or SLO outcome.
// Raw profiles, runtime settings, manifests, and free-text Agent reasoning are
// deliberately excluded from the durable research trace.
type ScalingDecisionSnapshot struct {
	SchemaVersion                    string  `json:"schema_version"`
	RecordType                       string  `json:"record_type"`
	DecisionID                       string  `json:"decision_id"`
	CorrelationID                    string  `json:"correlation_id"`
	ProfileID                        string  `json:"profile_id"`
	AppID                            string  `json:"app_id"`
	AppVersion                       string  `json:"app_version"`
	InputOrigin                      string  `json:"input_origin,omitempty"`
	OperationRunID                   string  `json:"operation_run_id"`
	OperationAgentName               string  `json:"operation_agent_name,omitempty"`
	OperationAgentSource             string  `json:"operation_agent_source,omitempty"`
	Action                           string  `json:"action"`
	PlannedCurrentReplicas           int     `json:"planned_current_replicas"`
	PlannedDesiredReplicas           int     `json:"planned_desired_replicas"`
	DecisionProposedAt               string  `json:"decision_proposed_at"`
	ApprovalRecordedAt               string  `json:"approval_recorded_at"`
	ApplicationContextMessageID      string  `json:"application_context_message_id"`
	ApplicationContextSHA256         string  `json:"application_context_sha256"`
	ApplicationContextAcceptedAt     string  `json:"application_context_accepted_at"`
	ResourceRecommendationMessageID  string  `json:"resource_recommendation_message_id"`
	ResourceRecommendationSHA256     string  `json:"resource_recommendation_sha256"`
	ResourceRecommendationAcceptedAt string  `json:"resource_recommendation_accepted_at"`
	InputManifestRevision            int     `json:"input_manifest_revision"`
	InputManifestRequestID           string  `json:"input_manifest_request_id"`
	DeploymentStatusMessageID        string  `json:"deployment_status_message_id"`
	DeploymentStatusSHA256           string  `json:"deployment_status_sha256"`
	DeploymentStatusSourceUpdatedAt  string  `json:"deployment_status_source_updated_at"`
	DeploymentStatusAcceptedAt       string  `json:"deployment_status_accepted_at"`
	FeedbackMessageID                string  `json:"feedback_message_id"`
	FeedbackSHA256                   string  `json:"feedback_sha256"`
	FeedbackSourceOutcome            string  `json:"feedback_source_outcome"`
	FeedbackWindowStartedAt          string  `json:"feedback_window_started_at"`
	FeedbackWindowEndedAt            string  `json:"feedback_window_ended_at"`
	FeedbackAcceptedAt               string  `json:"feedback_accepted_at"`
	SourceLatencyP95MS               float64 `json:"source_latency_p95_ms"`
	SourceThroughputRPS              float64 `json:"source_throughput_rps"`
	SourceErrorRatePercent           float64 `json:"source_error_rate_percent"`
	SourceCPUAveragePercent          float64 `json:"source_cpu_average_percent"`
	SourceAcceleratorAveragePercent  float64 `json:"source_accelerator_average_percent"`
	SLOLatencyP95MSMax               float64 `json:"slo_latency_p95_ms_max"`
	SLOThroughputRPSMin              float64 `json:"slo_throughput_rps_min"`
	ReplicasMin                      int     `json:"replicas_min"`
	ReplicasMax                      int     `json:"replicas_max"`
	OutputManifestRevision           int     `json:"output_manifest_revision,omitempty"`
	OutputManifestRequestID          string  `json:"output_manifest_request_id,omitempty"`
}

type ScalingDecisionJournal interface {
	SaveScalingDecision(ScalingDecisionSnapshot) error
}

// FileScalingDecisionJournal stores one immutable JSON file per decision. It
// is opt-in, local to this PoC, and independent of AppDeploy delivery receipts.
type FileScalingDecisionJournal struct {
	Directory string
}

func scalingDecisionSnapshot(record ScalingDecisionRecord, result OperationOptimizationResult, correlationID string) (ScalingDecisionSnapshot, error) {
	applicationDigest, err := snapshotDigest(record.ApplicationContext)
	if err != nil {
		return ScalingDecisionSnapshot{}, err
	}
	recommendationDigest, err := snapshotDigest(record.ResourceRecommendation)
	if err != nil {
		return ScalingDecisionSnapshot{}, err
	}
	statusDigest, err := snapshotDigest(record.DeploymentStatus)
	if err != nil {
		return ScalingDecisionSnapshot{}, err
	}
	feedbackDigest, err := snapshotDigest(record.OptimizationFeedback)
	if err != nil {
		return ScalingDecisionSnapshot{}, err
	}
	feedback := record.OptimizationFeedback.Data.OptimizationFeedback
	return ScalingDecisionSnapshot{
		SchemaVersion:                    "0.1.0",
		RecordType:                       "KHU_SCALING_DECISION_SNAPSHOT",
		DecisionID:                       record.DecisionID,
		CorrelationID:                    correlationID,
		ProfileID:                        record.ProfileID,
		AppID:                            record.AppID,
		AppVersion:                       record.AppVersion,
		InputOrigin:                      record.InputOrigin,
		OperationRunID:                   record.OperationRunID,
		OperationAgentName:               result.AgentName,
		OperationAgentSource:             result.Source,
		Action:                           record.Decision.Action,
		PlannedCurrentReplicas:           record.Decision.CurrentReplicas,
		PlannedDesiredReplicas:           record.Decision.DesiredReplicas,
		DecisionProposedAt:               record.Decision.CreatedAt,
		ApprovalRecordedAt:               record.CommittedAt,
		ApplicationContextMessageID:      record.ApplicationContextMessageID,
		ApplicationContextSHA256:         applicationDigest,
		ApplicationContextAcceptedAt:     record.ApplicationContextAcceptedAt,
		ResourceRecommendationMessageID:  record.ResourceRecommendationMessageID,
		ResourceRecommendationSHA256:     recommendationDigest,
		ResourceRecommendationAcceptedAt: record.ResourceRecommendationAcceptedAt,
		InputManifestRevision:            record.InputManifestRevision,
		InputManifestRequestID:           record.InputManifestRequestID,
		DeploymentStatusMessageID:        record.DeploymentStatus.MessageID,
		DeploymentStatusSHA256:           statusDigest,
		DeploymentStatusSourceUpdatedAt:  record.DeploymentStatus.Data.DeploymentStatus.UpdatedAt,
		DeploymentStatusAcceptedAt:       record.DeploymentStatusAcceptedAt,
		FeedbackMessageID:                record.OptimizationFeedback.MessageID,
		FeedbackSHA256:                   feedbackDigest,
		FeedbackSourceOutcome:            feedback.Outcome,
		FeedbackWindowStartedAt:          feedback.ObservationWindow.StartedAt,
		FeedbackWindowEndedAt:            feedback.ObservationWindow.EndedAt,
		FeedbackAcceptedAt:               record.OptimizationFeedbackAcceptedAt,
		SourceLatencyP95MS:               feedback.Metrics.Inference.LatencyP95MS,
		SourceThroughputRPS:              feedback.Metrics.Inference.ThroughputRPS,
		SourceErrorRatePercent:           feedback.Metrics.Inference.ErrorRatePercent,
		SourceCPUAveragePercent:          feedback.Metrics.Resource.CPUAveragePercent,
		SourceAcceleratorAveragePercent:  feedback.Metrics.Resource.AcceleratorAveragePercent,
		SLOLatencyP95MSMax:               record.SLO.LatencyP95MSMax,
		SLOThroughputRPSMin:              record.SLO.ThroughputRPSMin,
		ReplicasMin:                      record.ReplicasMin,
		ReplicasMax:                      record.ReplicasMax,
		OutputManifestRevision:           record.OutputManifestRevision,
		OutputManifestRequestID:          record.OutputManifestRequestID,
	}, nil
}

func snapshotDigest(value any) (string, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("decision input could not be hashed: %w", err)
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func (journal FileScalingDecisionJournal) SaveScalingDecision(snapshot ScalingDecisionSnapshot) error {
	if strings.TrimSpace(journal.Directory) == "" || strings.TrimSpace(snapshot.DecisionID) == "" {
		return fmt.Errorf("decision journal directory and decision_id are required")
	}
	encoded, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		return fmt.Errorf("decision snapshot could not be encoded: %w", err)
	}
	encoded = append(encoded, '\n')
	if err := os.MkdirAll(journal.Directory, 0700); err != nil {
		return fmt.Errorf("decision journal directory could not be created: %w", err)
	}
	nameDigest := sha256.Sum256([]byte(snapshot.DecisionID))
	path := filepath.Join(journal.Directory, hex.EncodeToString(nameDigest[:])+".json")
	if existing, err := os.ReadFile(path); err == nil {
		if string(existing) == string(encoded) {
			return nil
		}
		return fmt.Errorf("decision_id already has different or corrupt journal content")
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("decision journal could not be read: %w", err)
	}
	temporary, err := os.CreateTemp(journal.Directory, ".decision-*")
	if err != nil {
		return fmt.Errorf("decision journal temporary file could not be created: %w", err)
	}
	defer os.Remove(temporary.Name())
	if err := temporary.Chmod(0600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(encoded); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Link(temporary.Name(), path); err != nil {
		if os.IsExist(err) {
			existing, readErr := os.ReadFile(path)
			if readErr == nil && string(existing) == string(encoded) {
				return nil
			}
			return fmt.Errorf("decision_id already has different or corrupt journal content")
		}
		return fmt.Errorf("decision journal could not be published: %w", err)
	}
	if runtime.GOOS != "windows" {
		directory, err := os.Open(journal.Directory)
		if err != nil {
			return fmt.Errorf("decision journal directory could not be opened for sync: %w", err)
		}
		syncErr := directory.Sync()
		closeErr := directory.Close()
		if syncErr != nil {
			return fmt.Errorf("decision journal directory could not be synced: %w", syncErr)
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}
