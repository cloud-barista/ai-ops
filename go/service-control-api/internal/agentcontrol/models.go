package agentcontrol

const (
	ContractVersionV1 = "1.0"

	MessageApplicationAnalysisRequest    = "application.analysis.request"
	MessageApplicationContextCreated     = "application.context.created"
	MessageResourceRecommendationCreated = "resource.recommendation.created"
	MessageDeploymentCreateRequest       = "deployment.create.request"
	MessageDeploymentStatusChanged       = "deployment.status.changed"
	MessageOptimizationFeedbackCreated   = "optimization.feedback.created"

	StateWaitingForContext          = "WAITING_FOR_APPLICATION_CONTEXT"
	StateWaitingForRecommendation   = "WAITING_FOR_RESOURCE_RECOMMENDATION"
	StateReady                      = "READY"
	StateDecisionApproved           = "DEPLOY_APPROVED"
	StateDecisionRejected           = "REJECTED"
	StateRetryRequired              = "RETRY_REQUIRED"
	StateAgentAuthorizationRejected = "AGENT_AUTHORIZATION_REJECTED"
	StateAgentExecutionFailed       = "AGENT_EXECUTION_FAILED"
	StateAgentResultRejected        = "AGENT_RESULT_REJECTED"

	ActionDeploy = "DEPLOY"
	ActionReject = "REJECT"
	ActionRetry  = "RETRY"

	ScalingActionNoAction = "NO_ACTION"
	ScalingActionScaleOut = "SCALE_OUT"
	ScalingActionScaleIn  = "SCALE_IN"

	AutomationAgentName      = "AIApplicationAutomationAgent"
	AutomationCapability     = "ai_application_automation"
	AutomationDecisionAction = "generate_deployment_decision"

	ReasoningModeRuleBased = "rule_based"

	InputTypeNaturalLanguage = "natural_language"
	InputTypeStructured      = "structured"

	AnalysisModeLocalRule  = "local_rule"
	AnalysisModeStructured = "structured"
	AnalysisModeQwen       = "qwen"

	CorrectionTargetApplicationProfile     = "application_profile"
	CorrectionTargetResourceRecommendation = "resource_recommendation"

	GuardApproved      = "APPROVED"
	GuardRejected      = "REJECTED"
	GuardRetryRequired = "RETRY_REQUIRED"
	GuardNotApplied    = "NOT_APPLIED"

	DeploymentStateQueued       = "QUEUED"
	DeploymentStateProvisioning = "PROVISIONING"
	DeploymentStateDeploying    = "DEPLOYING"
	DeploymentStateRunning      = "RUNNING"
	DeploymentStateFailed       = "FAILED"
	DeploymentStateStopping     = "STOPPING"
	DeploymentStateStopped      = "STOPPED"

	FeedbackOutcomeSucceeded = "SUCCEEDED"
	FeedbackOutcomeFailed    = "FAILED"

	ReasoningModeSimpleInference    = "simple_inference"
	ReasoningModeValidatedInference = "validated_inference"
	ReasoningExecutionExecuted      = "executed"
	ReasoningExecutionSkipped       = "skipped"
	ReasoningProviderUnavailable    = "provider_unavailable"
)

type Endpoint struct {
	System    string `json:"system"`
	Component string `json:"component"`
}

type AutomationRunInput struct {
	InputType     string             `json:"input_type"`
	Request       string             `json:"request,omitempty"`
	RequestedBy   string             `json:"requested_by,omitempty"`
	DecisionAgent string             `json:"decision_agent,omitempty"`
	AppSpec       *StructuredAppSpec `json:"app_spec,omitempty"`
}

type StructuredAppSpec struct {
	AppID                string            `json:"app_id,omitempty"`
	AppVersion           string            `json:"app_version,omitempty"`
	WorkloadType         string            `json:"workload_type,omitempty"`
	CPUCores             int               `json:"cpu_cores"`
	MemoryMiB            int               `json:"memory_mib"`
	StorageGiB           int               `json:"storage_gib"`
	AcceleratorType      string            `json:"accelerator_type,omitempty"`
	AcceleratorCount     int               `json:"accelerator_count,omitempty"`
	AcceleratorMemoryMiB int               `json:"accelerator_memory_mib,omitempty"`
	ReplicasMin          int               `json:"replicas_min,omitempty"`
	ReplicasMax          int               `json:"replicas_max,omitempty"`
	Artifact             *Artifact         `json:"artifact,omitempty"`
	ExpectedRPS          float64           `json:"expected_rps,omitempty"`
	MaxInputTokens       int               `json:"max_input_tokens,omitempty"`
	Labels               map[string]string `json:"labels,omitempty"`
}

type RequirementAnalysisEvidence struct {
	Mode        string   `json:"mode"`
	SourceInput string   `json:"source_input,omitempty"`
	Assumptions []string `json:"assumptions,omitempty"`
	CandidateID string   `json:"candidate_id,omitempty"`
	Provider    string   `json:"provider,omitempty"`
	ActualModel string   `json:"actual_model,omitempty"`
	LatencyMS   int64    `json:"latency_ms,omitempty"`
}

type RequirementAnalysisResult struct {
	ApplicationProfile  ApplicationProfile          `json:"application_profile"`
	ModelRecommendation ModelRecommendation         `json:"model_recommendation"`
	Mode                string                      `json:"mode"`
	Evidence            RequirementAnalysisEvidence `json:"evidence"`
}

type Envelope struct {
	ContractVersion string   `json:"contract_version"`
	MessageID       string   `json:"message_id"`
	MessageType     string   `json:"message_type"`
	OccurredAt      string   `json:"occurred_at"`
	CorrelationID   string   `json:"correlation_id"`
	TraceID         string   `json:"trace_id"`
	CausationID     string   `json:"causation_id,omitempty"`
	Source          Endpoint `json:"source"`
	Target          Endpoint `json:"target"`
}

type Artifact struct {
	Type       string   `json:"type"`
	URI        string   `json:"uri"`
	Entrypoint []string `json:"entrypoint,omitempty"`
}

type ApplicationAnalysisRequestEnvelope struct {
	Envelope
	Data ApplicationAnalysisRequestData `json:"data"`
}

type ApplicationAnalysisRequestData struct {
	Application AnalysisRequestApplication `json:"application"`
}

type AnalysisRequestApplication struct {
	AppID        string                  `json:"app_id"`
	AppVersion   string                  `json:"app_version"`
	Artifact     Artifact                `json:"artifact"`
	UserRequest  string                  `json:"user_request"`
	DeclaredSpec DeclaredApplicationSpec `json:"declared_spec,omitempty"`
	Labels       map[string]string       `json:"labels,omitempty"`
}

type DeclaredApplicationSpec struct {
	ExpectedRPS    float64 `json:"expected_rps,omitempty"`
	MaxInputTokens int     `json:"max_input_tokens,omitempty"`
}

type ApplicationContextEnvelope struct {
	Envelope
	Data ApplicationContextData `json:"data"`
}

type ApplicationContextData struct {
	ApplicationProfile  ApplicationProfile  `json:"application_profile"`
	ModelRecommendation ModelRecommendation `json:"model_recommendation"`
}

type ApplicationProfile struct {
	ProfileID    string                  `json:"profile_id"`
	AppID        string                  `json:"app_id"`
	AppVersion   string                  `json:"app_version"`
	Artifact     *Artifact               `json:"artifact,omitempty"`
	Workload     WorkloadProfile         `json:"workload,omitempty"`
	Requirements ApplicationRequirements `json:"requirements"`
	Analysis     AnalysisSummary         `json:"analysis,omitempty"`
}

type WorkloadProfile struct {
	TaskType       string  `json:"task_type,omitempty"`
	RequestPattern string  `json:"request_pattern,omitempty"`
	ExpectedRPS    float64 `json:"expected_rps,omitempty"`
}

type ApplicationRequirements struct {
	Compute     ComputeRequirements     `json:"compute"`
	Accelerator AcceleratorRequirements `json:"accelerator"`
	Deployment  DeploymentRequirements  `json:"deployment"`
	SLO         SLORequirements         `json:"slo,omitempty"`
	Cost        CostRequirements        `json:"cost,omitempty"`
}

type ComputeRequirements struct {
	CPUCoresMin   int `json:"cpu_cores_min"`
	MemoryMiBMin  int `json:"memory_mib_min"`
	StorageGiBMin int `json:"storage_gib_min"`
}

type AcceleratorRequirements struct {
	Required              bool   `json:"required"`
	Type                  string `json:"type,omitempty"`
	CountMin              int    `json:"count_min,omitempty"`
	MemoryMiBMinPerDevice int    `json:"memory_mib_min_per_device,omitempty"`
}

type DeploymentRequirements struct {
	ReplicasMin int    `json:"replicas_min"`
	ReplicasMax int    `json:"replicas_max"`
	Isolation   string `json:"isolation"`
}

type SLORequirements struct {
	LatencyP95MSMax  float64 `json:"latency_p95_ms_max,omitempty"`
	ThroughputRPSMin float64 `json:"throughput_rps_min,omitempty"`
}

type CostRequirements struct {
	Currency       string  `json:"currency,omitempty"`
	CostPerHourMax float64 `json:"cost_per_hour_max,omitempty"`
}

type AnalysisSummary struct {
	Confidence    float64  `json:"confidence,omitempty"`
	Assumptions   []string `json:"assumptions,omitempty"`
	MissingFields []string `json:"missing_fields,omitempty"`
	Warnings      []string `json:"warnings,omitempty"`
}

type ModelRecommendation struct {
	RecommendationID       string                 `json:"recommendation_id,omitempty"`
	SelectedModel          SelectedModel          `json:"selected_model"`
	InferenceConfiguration InferenceConfiguration `json:"inference_configuration"`
}

type SelectedModel struct {
	ModelID      string `json:"model_id"`
	ModelVersion string `json:"model_version"`
	Source       string `json:"source"`
}

type InferenceConfiguration struct {
	RuntimeEngine      string `json:"runtime_engine"`
	Precision          string `json:"precision"`
	MaxBatchSize       int    `json:"max_batch_size"`
	MaxConcurrency     int    `json:"max_concurrency"`
	TensorParallelSize int    `json:"tensor_parallel_size"`
	Replicas           int    `json:"replicas"`
}

type ResourceRecommendationEnvelope struct {
	Envelope
	Data ResourceRecommendationData `json:"data"`
}

type ResourceRecommendationData struct {
	ResourceRecommendation ResourceRecommendation `json:"resource_recommendation"`
}

type ResourceRecommendation struct {
	RecommendationID    string              `json:"recommendation_id"`
	ProfileID           string              `json:"profile_id"`
	SnapshotID          string              `json:"snapshot_id"`
	Status              string              `json:"status"`
	SelectedCandidateID string              `json:"selected_candidate_id"`
	Candidates          []ResourceCandidate `json:"candidates"`
}

type ResourceCandidate struct {
	CandidateID           string                `json:"candidate_id"`
	Rank                  int                   `json:"rank"`
	Feasible              bool                  `json:"feasible"`
	DesiredInfrastructure DesiredInfrastructure `json:"desired_infrastructure"`
	ResourceHints         []string              `json:"resource_hints,omitempty"`
	Scores                ResourceScores        `json:"scores"`
	RejectionReasons      []string              `json:"rejection_reasons,omitempty"`
}

type DesiredInfrastructure struct {
	NodeCount         int                   `json:"node_count"`
	CPUCoresPerNode   int                   `json:"cpu_cores_per_node"`
	MemoryMiBPerNode  int                   `json:"memory_mib_per_node"`
	StorageGiBPerNode int                   `json:"storage_gib_per_node"`
	Accelerator       AcceleratorAllocation `json:"accelerator,omitempty"`
	Isolation         string                `json:"isolation"`
}

type AcceleratorAllocation struct {
	Type                  string `json:"type,omitempty"`
	Count                 int    `json:"count,omitempty"`
	MemoryMiBMinPerDevice int    `json:"memory_mib_min_per_device,omitempty"`
}

type ResourceScores struct {
	ResourceFit    float64 `json:"resource_fit"`
	SLOHeadroom    float64 `json:"slo_headroom"`
	CostEfficiency float64 `json:"cost_efficiency"`
	Availability   float64 `json:"availability"`
	Total          float64 `json:"total"`
}

type ValidationIssue struct {
	Field    string `json:"field"`
	Code     string `json:"code"`
	Reason   string `json:"reason"`
	Expected any    `json:"expected,omitempty"`
	Actual   any    `json:"actual,omitempty"`
}

type CorrectionRequest struct {
	Target string            `json:"target"`
	Reason string            `json:"reason"`
	Issues []ValidationIssue `json:"issues"`
}

type AutomationDecision struct {
	DecisionID          string             `json:"decision_id"`
	Action              string             `json:"action"`
	Reason              string             `json:"reason"`
	ReasoningMode       string             `json:"reasoning_mode"`
	Confidence          float64            `json:"confidence"`
	SelectedCandidateID string             `json:"selected_candidate_id,omitempty"`
	CorrectionRequest   *CorrectionRequest `json:"correction_request,omitempty"`
	CreatedAt           string             `json:"created_at"`
}

type AgentAuthorizationRequest struct {
	AgentName  string `json:"agent_name"`
	Capability string `json:"capability"`
	Action     string `json:"action"`
}

type AgentAuthorization struct {
	AgentName  string `json:"agent_name"`
	Capability string `json:"capability"`
	Action     string `json:"action"`
	Authorized bool   `json:"authorized"`
	Reason     string `json:"reason"`
}

type DeploymentPlan struct {
	PlanID                 string                 `json:"plan_id"`
	ProfileID              string                 `json:"profile_id"`
	AppID                  string                 `json:"app_id"`
	AppVersion             string                 `json:"app_version"`
	SelectedCandidateID    string                 `json:"selected_candidate_id"`
	TargetRuntime          string                 `json:"target_runtime"`
	DesiredInfrastructure  DesiredInfrastructure  `json:"desired_infrastructure"`
	InferenceConfiguration InferenceConfiguration `json:"inference_configuration"`
	ResourceHints          []string               `json:"resource_hints,omitempty"`
	ReasoningMode          string                 `json:"reasoning_mode"`
	CreatedAt              string                 `json:"created_at"`
}

type GuardCheck struct {
	Name   string `json:"name"`
	Passed bool   `json:"passed"`
	Reason string `json:"reason"`
}

type GuardResult struct {
	Status string            `json:"status"`
	Checks []GuardCheck      `json:"checks"`
	Issues []ValidationIssue `json:"issues,omitempty"`
}

type DeploymentCreateRequestEnvelope struct {
	Envelope
	Data DeploymentCreateRequestData `json:"data"`
}

type DeploymentCreateRequestData struct {
	DeploymentRequest DeploymentRequest `json:"deployment_request"`
}

type DesiredDeploymentSpec struct {
	SpecVersion            string                 `json:"spec_version"`
	DecisionID             string                 `json:"decision_id"`
	Application            ManifestApplication    `json:"application"`
	TargetRuntime          string                 `json:"target_runtime"`
	DesiredInfrastructure  DesiredInfrastructure  `json:"desired_infrastructure"`
	InferenceConfiguration InferenceConfiguration `json:"inference_configuration"`
	Runtime                RuntimeConfiguration   `json:"runtime,omitempty"`
	PolicyHints            []string               `json:"policy_hints,omitempty"`
	Metadata               ManifestMetadata       `json:"metadata"`
}

type DeploymentRequest struct {
	RequestID          string                `json:"request_id"`
	DecisionID         string                `json:"decision_id"`
	Application        DeploymentApplication `json:"application"`
	DeploymentManifest DeploymentManifest    `json:"deployment_manifest"`
}

type DeploymentApplication struct {
	AppID      string    `json:"app_id"`
	AppVersion string    `json:"app_version"`
	Artifact   *Artifact `json:"artifact,omitempty"`
}

type DeploymentManifest struct {
	ManifestID             string                 `json:"manifest_id"`
	ManifestVersion        string                 `json:"manifest_version"`
	DecisionID             string                 `json:"decision_id"`
	Application            ManifestApplication    `json:"application"`
	TargetRuntime          string                 `json:"target_runtime"`
	DesiredInfrastructure  DesiredInfrastructure  `json:"desired_infrastructure"`
	InferenceConfiguration InferenceConfiguration `json:"inference_configuration"`
	Runtime                RuntimeConfiguration   `json:"runtime,omitempty"`
	ResourceHints          []string               `json:"resource_hints,omitempty"`
	Metadata               ManifestMetadata       `json:"metadata"`
}

type ManifestApplication struct {
	AppID      string `json:"app_id"`
	AppVersion string `json:"app_version"`
}

type RuntimeConfiguration struct {
	Command       []string          `json:"command,omitempty"`
	Args          []string          `json:"args,omitempty"`
	Environment   map[string]string `json:"environment,omitempty"`
	Ports         []RuntimePort     `json:"ports,omitempty"`
	RestartPolicy string            `json:"restart_policy,omitempty"`
}

type RuntimePort struct {
	Name     string `json:"name"`
	Port     int    `json:"port"`
	Protocol string `json:"protocol"`
}

type ManifestMetadata struct {
	ProfileID string `json:"profile_id"`
}

type DeploymentStatusEnvelope struct {
	Envelope
	Data DeploymentStatusData `json:"data"`
}

type DeploymentStatusData struct {
	DeploymentStatus DeploymentStatus `json:"deployment_status"`
}

type DeploymentStatus struct {
	DeploymentID         string               `json:"deployment_id"`
	DecisionID           string               `json:"decision_id"`
	State                string               `json:"state"`
	ActualInfrastructure ActualInfrastructure `json:"actual_infrastructure,omitempty"`
	Message              string               `json:"message,omitempty"`
	UpdatedAt            string               `json:"updated_at"`
}

type ActualInfrastructure struct {
	Provider    string   `json:"provider,omitempty"`
	Region      string   `json:"region,omitempty"`
	ResourceIDs []string `json:"resource_ids,omitempty"`
}

type OptimizationFeedbackEnvelope struct {
	Envelope
	Data OptimizationFeedbackData `json:"data"`
}

type OptimizationFeedbackData struct {
	OptimizationFeedback OptimizationFeedback `json:"optimization_feedback"`
}

type OptimizationFeedback struct {
	FeedbackID        string              `json:"feedback_id"`
	DecisionID        string              `json:"decision_id"`
	DeploymentID      string              `json:"deployment_id"`
	Outcome           string              `json:"outcome"`
	ObservationWindow ObservationWindow   `json:"observation_window"`
	Metrics           OptimizationMetrics `json:"metrics"`
	SLOViolations     []string            `json:"slo_violations,omitempty"`
	CreatedAt         string              `json:"created_at"`
}

type ObservationWindow struct {
	StartedAt string `json:"started_at"`
	EndedAt   string `json:"ended_at"`
}

type OptimizationMetrics struct {
	Resource  ResourceMetrics  `json:"resource"`
	Inference InferenceMetrics `json:"inference"`
	Cost      CostMetrics      `json:"cost"`
}

type ResourceMetrics struct {
	CPUAveragePercent         float64 `json:"cpu_average_percent"`
	MemoryPeakMiB             float64 `json:"memory_peak_mib"`
	AcceleratorAveragePercent float64 `json:"accelerator_average_percent"`
	AcceleratorMemoryPeakMiB  float64 `json:"accelerator_memory_peak_mib"`
}

type InferenceMetrics struct {
	LatencyP95MS     float64 `json:"latency_p95_ms"`
	ThroughputRPS    float64 `json:"throughput_rps"`
	ErrorRatePercent float64 `json:"error_rate_percent"`
}

type CostMetrics struct {
	Currency      string  `json:"currency"`
	EstimatedCost float64 `json:"estimated_cost"`
}

type FeedbackSummary struct {
	DeploymentID    string               `json:"deployment_id"`
	DecisionID      string               `json:"decision_id"`
	DeploymentState string               `json:"deployment_state"`
	Outcome         string               `json:"outcome,omitempty"`
	Success         bool                 `json:"success"`
	Cause           string               `json:"cause"`
	SLOViolations   []string             `json:"slo_violations,omitempty"`
	Metrics         *OptimizationMetrics `json:"metrics,omitempty"`
	UpdatedAt       string               `json:"updated_at"`
}

type ScalingDecision struct {
	Action          string   `json:"action"`
	Reason          string   `json:"reason"`
	CurrentReplicas int      `json:"current_replicas"`
	DesiredReplicas int      `json:"desired_replicas"`
	Evidence        []string `json:"evidence,omitempty"`
	CreatedAt       string   `json:"created_at"`
}

type ReasoningInput struct {
	ApplicationProfile     ApplicationProfile     `json:"application_profile"`
	ResourceRecommendation ResourceRecommendation `json:"resource_recommendation"`
}

type ReasoningProposal struct {
	Action              string  `json:"action"`
	SelectedCandidateID string  `json:"selected_candidate_id,omitempty"`
	Reason              string  `json:"reason"`
	Confidence          float64 `json:"confidence"`
}

type ModelReasoningResult struct {
	ExecutionStatus string            `json:"execution_status"`
	CandidateID     string            `json:"candidate_id,omitempty"`
	Provider        string            `json:"provider,omitempty"`
	ActualModel     string            `json:"actual_model,omitempty"`
	LatencyMS       int64             `json:"latency_ms"`
	Proposal        ReasoningProposal `json:"proposal"`
}

type ReasoningTrial struct {
	Mode                string  `json:"mode"`
	ExecutionStatus     string  `json:"execution_status"`
	CandidateID         string  `json:"candidate_id,omitempty"`
	Provider            string  `json:"provider,omitempty"`
	ActualModel         string  `json:"actual_model,omitempty"`
	LatencyMS           int64   `json:"latency_ms"`
	Action              string  `json:"action,omitempty"`
	ProposedAction      string  `json:"proposed_action,omitempty"`
	SelectedCandidateID string  `json:"selected_candidate_id,omitempty"`
	Reason              string  `json:"reason,omitempty"`
	Confidence          float64 `json:"confidence,omitempty"`
	GuardStatus         string  `json:"guard_status"`
	GuardReason         string  `json:"guard_reason,omitempty"`
	Error               string  `json:"error,omitempty"`
}

type ReasoningAgreement struct {
	ActionMatch    bool `json:"action_match"`
	CandidateMatch bool `json:"candidate_match"`
}

type ReasoningComparison struct {
	CorrelationID      string             `json:"correlation_id"`
	CandidateID        string             `json:"candidate_id"`
	RuleBased          ReasoningTrial     `json:"rule_based"`
	SimpleInference    ReasoningTrial     `json:"simple_inference"`
	ValidatedInference ReasoningTrial     `json:"validated_inference"`
	Agreement          ReasoningAgreement `json:"agreement"`
	CreatedAt          string             `json:"created_at"`
}

type Flow struct {
	CorrelationID          string                           `json:"correlation_id"`
	TraceID                string                           `json:"trace_id"`
	ProfileID              string                           `json:"profile_id,omitempty"`
	AutomationRunID        string                           `json:"automation_run_id,omitempty"`
	RequestedDecisionAgent string                           `json:"requested_decision_agent,omitempty"`
	State                  string                           `json:"state"`
	ApplicationContext     *ApplicationContextEnvelope      `json:"application_context,omitempty"`
	ResourceRecommendation *ResourceRecommendationEnvelope  `json:"resource_recommendation,omitempty"`
	AgentAuthorization     *AgentAuthorization              `json:"agent_authorization,omitempty"`
	AgentExecution         *DecisionAgentResult             `json:"agent_execution,omitempty"`
	Decision               *AutomationDecision              `json:"decision,omitempty"`
	DeploymentPlan         *DeploymentPlan                  `json:"deployment_plan,omitempty"`
	Guard                  *GuardResult                     `json:"guard,omitempty"`
	DesiredDeploymentSpec  *DesiredDeploymentSpec           `json:"desired_deployment_spec,omitempty"`
	DeploymentRequest      *DeploymentCreateRequestEnvelope `json:"deployment_request,omitempty"`
	DeploymentStatus       *DeploymentStatusEnvelope        `json:"deployment_status,omitempty"`
	OptimizationFeedback   *OptimizationFeedbackEnvelope    `json:"optimization_feedback,omitempty"`
	FeedbackSummary        *FeedbackSummary                 `json:"feedback_summary,omitempty"`
	ScalingDecision        *ScalingDecision                 `json:"scaling_decision,omitempty"`
	ReasoningComparison    *ReasoningComparison             `json:"reasoning_comparison,omitempty"`
	UpdatedAt              string                           `json:"updated_at"`
}
