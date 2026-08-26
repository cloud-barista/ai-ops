package audittrail

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"
)

const (
	SchemaVersion = "ai-ops.trusted-automation-audit/v1"

	KindTrustedAutomation = "trusted_automation_run"

	PersistenceRecording = "RECORDING"
	PersistenceComplete  = "COMPLETE"
	PersistenceDegraded  = "DEGRADED"

	StageRequest            = "request"
	StageSafeguard          = "safeguard"
	StageGeon               = "geon_revision_flow"
	StageApprovedProjection = "approved_projection"
	StageTerminalResponse   = "terminal_response"
)

var safeLabelPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$`)

// Identity contains only server-issued protocol identifiers. Caller-provided
// application, deployment, target, and runtime identifiers are intentionally
// excluded from the persistent audit trail.
type Identity struct {
	RequestID     string `json:"request_id,omitempty"`
	MessageID     string `json:"message_id,omitempty"`
	CorrelationID string `json:"correlation_id,omitempty"`
	TraceID       string `json:"trace_id,omitempty"`
	RunID         string `json:"run_id,omitempty"`
}

// RequestSummary binds the exact request without retaining its natural-language
// text, arbitrary labels, artifact URI, entrypoint, or caller identity.
type RequestSummary struct {
	InputType             string `json:"input_type"`
	CandidateIDSHA256     string `json:"candidate_id_sha256"`
	PayloadSHA256         string `json:"payload_sha256"`
	UserRequestRuneCount  int    `json:"user_request_rune_count,omitempty"`
	StructuredInput       bool   `json:"structured_input"`
	RequestedBySHA256     string `json:"requested_by_sha256,omitempty"`
	AppVersionIDSHA256    string `json:"app_version_id_sha256,omitempty"`
	RawPayloadStored      bool   `json:"raw_payload_stored"`
	RawModelContentStored bool   `json:"raw_model_content_stored"`
}

type ResourceSummary struct {
	CPUCoresMin                  int    `json:"cpu_cores_min,omitempty"`
	MemoryMiBMin                 int    `json:"memory_mib_min,omitempty"`
	StorageGiBMin                int    `json:"storage_gib_min,omitempty"`
	AcceleratorRequired          bool   `json:"accelerator_required"`
	AcceleratorType              string `json:"accelerator_type,omitempty"`
	AcceleratorCountMin          int    `json:"accelerator_count_min,omitempty"`
	AcceleratorMemoryMiBPerDevice int   `json:"accelerator_memory_mib_per_device,omitempty"`
	ReplicasMin                  int    `json:"replicas_min,omitempty"`
	ReplicasMax                  int    `json:"replicas_max,omitempty"`
}

// Evidence is a typed allowlist. It must never be replaced with an arbitrary
// request/result map because those objects contain raw user and provider text.
type Evidence struct {
	InputSHA256                    string           `json:"input_sha256,omitempty"`
	OutputSHA256                   string           `json:"output_sha256,omitempty"`
	PolicyVersion                 string           `json:"policy_version,omitempty"`
	CandidateIDSHA256             string           `json:"candidate_id_sha256,omitempty"`
	Provider                      string           `json:"provider,omitempty"`
	ActualModel                   string           `json:"actual_model,omitempty"`
	ModelLatencyMS                int64            `json:"model_latency_ms,omitempty"`
	Decision                      string           `json:"decision,omitempty"`
	ReasonCode                    string           `json:"reason_code,omitempty"`
	Confidence                    *float64          `json:"confidence,omitempty"`
	Approved                      *bool             `json:"approved,omitempty"`
	RequestGuardStatus            string           `json:"request_guard_status,omitempty"`
	RequestGuardValid             *bool             `json:"request_guard_valid,omitempty"`
	RequestBinding                string           `json:"request_binding,omitempty"`
	BindingAlgorithm              string           `json:"binding_algorithm,omitempty"`
	AnalysisMode                  string           `json:"analysis_mode,omitempty"`
	CatalogVersion                string           `json:"catalog_version,omitempty"`
	RecommendationStatus          string           `json:"recommendation_status,omitempty"`
	SelectedResourceSHA256        string           `json:"selected_resource_sha256,omitempty"`
	Resources                     *ResourceSummary `json:"resources,omitempty"`
	AgentName                     string           `json:"agent_name,omitempty"`
	AgentAuthorized               *bool             `json:"agent_authorized,omitempty"`
	FlowState                     string           `json:"flow_state,omitempty"`
	GuardStatus                   string           `json:"guard_status,omitempty"`
	RevisionCount                 int              `json:"revision_count,omitempty"`
	LatestRevisionPhase           string           `json:"latest_revision_phase,omitempty"`
	DesiredDeploymentSpecSHA256   string           `json:"desired_deployment_spec_sha256,omitempty"`
	SubmissionMode                string           `json:"submission_mode,omitempty"`
	DeploymentAdapter             string           `json:"deployment_adapter,omitempty"`
	DeploymentSubmissionStatus    string           `json:"deployment_submission_status,omitempty"`
	DeploymentSubmissionSimulated *bool             `json:"deployment_submission_simulated,omitempty"`
	IdempotentReplay              bool             `json:"idempotent_replay,omitempty"`
	OrchestrationStatus           string           `json:"orchestration_status,omitempty"`
}

type EventIntegrity struct {
	Algorithm           string `json:"algorithm"`
	PreviousEventSHA256 string `json:"previous_event_sha256,omitempty"`
	PayloadSHA256       string `json:"payload_sha256"`
	Authenticated       bool   `json:"authenticated"`
}

type Event struct {
	SchemaVersion string         `json:"schema_version"`
	AuditID       string         `json:"audit_id"`
	Sequence      uint64         `json:"sequence"`
	RecordedAt   string         `json:"recorded_at"`
	Identity     Identity       `json:"identity"`
	Stage        string         `json:"stage"`
	Action       string         `json:"action"`
	Outcome      string         `json:"outcome"`
	DurationMS   int64          `json:"duration_ms,omitempty"`
	ErrorCode    string         `json:"error_code,omitempty"`
	Evidence     Evidence       `json:"evidence,omitempty"`
	Integrity    EventIntegrity `json:"integrity"`
}

type ResultSummary struct {
	FinalStatus                string `json:"final_status"`
	SafeguardStatus            string `json:"safeguard_status,omitempty"`
	SafeguardApproved          bool   `json:"safeguard_approved"`
	SafeguardDecision          string `json:"safeguard_decision,omitempty"`
	SafeguardReasonCode        string `json:"safeguard_reason_code,omitempty"`
	AutomationStatus           string `json:"automation_status,omitempty"`
	AnalysisMode               string `json:"analysis_mode,omitempty"`
	FlowState                  string `json:"flow_state,omitempty"`
	GuardStatus                string `json:"guard_status,omitempty"`
	RevisionCount              int    `json:"revision_count,omitempty"`
	LatestRevisionPhase        string `json:"latest_revision_phase,omitempty"`
	DesiredDeploymentSpecSHA256 string `json:"desired_deployment_spec_sha256,omitempty"`
	DeploymentAdapter          string `json:"deployment_adapter,omitempty"`
	DeploymentSubmissionStatus string `json:"deployment_submission_status,omitempty"`
	DeploymentSimulated        bool   `json:"deployment_simulated"`
	IdempotentReplay           bool   `json:"idempotent_replay"`
	ApprovedProjectionPrepared bool   `json:"approved_projection_prepared"`
}

type Failure struct {
	Stage string `json:"stage"`
	Code  string `json:"code"`
}

type SummaryIntegrity struct {
	Algorithm        string `json:"algorithm"`
	LastEventSHA256  string `json:"last_event_sha256"`
	PayloadSHA256    string `json:"payload_sha256"`
	Authenticated    bool   `json:"authenticated"`
}

type Artifacts struct {
	EventsPath  string `json:"events_path"`
	SummaryPath string `json:"summary_path"`
}

type Summary struct {
	SchemaVersion string           `json:"schema_version"`
	AuditID       string           `json:"audit_id"`
	Kind          string           `json:"kind"`
	Status        string           `json:"status"`
	PersistenceStatus string       `json:"persistence_status"`
	Complete      bool             `json:"complete"`
	StartedAt     string           `json:"started_at"`
	CompletedAt   string           `json:"completed_at"`
	DurationMS    int64            `json:"duration_ms"`
	Identity      Identity         `json:"identity"`
	Request       RequestSummary   `json:"request_summary"`
	Timeline      []Event          `json:"timeline"`
	Result        ResultSummary    `json:"result_summary"`
	Failure       *Failure         `json:"failure,omitempty"`
	Integrity     SummaryIntegrity `json:"integrity"`
	Artifacts     Artifacts        `json:"artifacts"`
}

type Reference struct {
	SchemaVersion     string `json:"schema_version"`
	AuditID           string `json:"audit_id"`
	PersistenceStatus string `json:"persistence_status"`
	Complete          bool   `json:"complete"`
	EventCount        int    `json:"event_count"`
	RelativeDirectory string `json:"relative_directory,omitempty"`
	EventsPath        string `json:"events_path,omitempty"`
	SummaryPath       string `json:"summary_path,omitempty"`
	LastEventSHA256   string `json:"last_event_sha256,omitempty"`
	SummarySHA256     string `json:"summary_sha256,omitempty"`
}

type StartMetadata struct {
	StartedAt time.Time
	Request   RequestSummary
}

type FinalizeMetadata struct {
	CompletedAt    time.Time
	TerminalOutcome string
	ErrorCode      string
	Result         ResultSummary
	Failure        *Failure
}

func DigestJSON(value any) (string, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("encode audit digest input: %w", err)
	}
	return DigestBytes(encoded), nil
}

func DigestString(value string) string {
	if value == "" {
		return ""
	}
	return DigestBytes([]byte(value))
}

// SafeLabel returns a bounded, non-secret operational label. Values outside
// the documented character set are dropped instead of being written to disk.
func SafeLabel(value string) string {
	value = strings.TrimSpace(value)
	if len(value) == 0 || len(value) > 128 || !safeLabelPattern.MatchString(value) {
		return ""
	}
	return value
}

func DigestBytes(value []byte) string {
	digest := sha256.Sum256(value)
	return hex.EncodeToString(digest[:])
}

func NewAuditID() (string, error) {
	value := make([]byte, 12)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate audit id: %w", err)
	}
	return "audit-" + hex.EncodeToString(value), nil
}

func Bool(value bool) *bool {
	return &value
}
