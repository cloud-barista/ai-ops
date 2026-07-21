package api

type AgentRegistry struct {
	Version string         `json:"version"`
	Agents  []AgentProfile `json:"agents"`
}

type AgentProfile struct {
	Name             string   `json:"name"`
	KoreanName       string   `json:"korean_name"`
	Version          string   `json:"version,omitempty"`
	Role             string   `json:"role"`
	Responsibilities []string `json:"responsibilities"`
	Capabilities     []string `json:"capabilities,omitempty"`
	BoundedActions   []string `json:"bounded_actions"`
	RewardSignals    []string `json:"reward_signals"`
	Enabled          bool     `json:"enabled"`
	Endpoint         string   `json:"endpoint,omitempty"`
	InvocationPath   string   `json:"invocation_path,omitempty"`
	Source           string   `json:"source,omitempty"`
	RegisteredAt     string   `json:"registered_at,omitempty"`
}

type ExternalAgentRegistrationRequest struct {
	Name             string   `json:"name" validate:"required"`
	KoreanName       string   `json:"korean_name"`
	Version          string   `json:"version" validate:"required"`
	Role             string   `json:"role" validate:"required"`
	Responsibilities []string `json:"responsibilities"`
	Endpoint         string   `json:"endpoint" validate:"required"`
	InvocationPath   string   `json:"invocation_path" validate:"required"`
	Capabilities     []string `json:"capabilities" validate:"required,min=1,dive,required"`
	BoundedActions   []string `json:"bounded_actions" validate:"required,min=1,dive,required"`
	RewardSignals    []string `json:"reward_signals"`
	Enabled          *bool    `json:"enabled,omitempty"`
}

type AgentInvocationPlanRequest struct {
	Capability string         `json:"capability" validate:"required"`
	Action     string         `json:"action" validate:"required"`
	Parameters map[string]any `json:"parameters,omitempty"`
}

type AgentInvocationPlan struct {
	Valid           bool           `json:"valid"`
	Agent           string         `json:"agent"`
	Capability      string         `json:"capability"`
	Action          string         `json:"action"`
	TargetURL       string         `json:"target_url"`
	Parameters      map[string]any `json:"parameters,omitempty"`
	ExecutionStatus string         `json:"execution_status"`
	Reason          string         `json:"reason"`
}

type LLMAutomationActionRequest struct {
	Workload     string             `json:"workload" validate:"required" example:"llm-chat-inference"`
	TargetVM     VMResourceSnapshot `json:"target_vm" validate:"required"`
	CandidateID  string             `json:"candidate_id" validate:"required" example:"qwen3.5-ops-planner"`
	Observations map[string]any     `json:"observations,omitempty"`
}

type LLMActionProposal struct {
	Action             string         `json:"action"`
	Reason             string         `json:"reason"`
	Confidence         float64        `json:"confidence"`
	RequiredCapability string         `json:"required_capability"`
	TargetVMID         string         `json:"target_vm_id"`
	Parameters         map[string]any `json:"parameters,omitempty"`
}

type LLMDecisionResult struct {
	DecisionExecutionStatus string            `json:"decision_execution_status"`
	CandidateID             string            `json:"candidate_id"`
	Provider                string            `json:"provider"`
	ActualModel             string            `json:"actual_model"`
	LatencyMS               int64             `json:"latency_ms"`
	Proposal                LLMActionProposal `json:"proposal"`
}

type GuardDecision struct {
	Valid  bool   `json:"valid"`
	Status string `json:"status"`
	Reason string `json:"reason"`
}

type LLMAutomationActionResponse struct {
	Valid           bool                    `json:"valid"`
	Status          string                  `json:"status"`
	CorrelationID   string                  `json:"correlation_id,omitempty"`
	VMCompatibility VMCompatibilityResponse `json:"vm_compatibility"`
	Decision        LLMDecisionResult       `json:"decision"`
	Guard           GuardDecision           `json:"guard"`
	Handoff         AgentInvocationPlan     `json:"handoff"`
}

type AutomationFeedbackRequest struct {
	CorrelationID       string   `json:"correlation_id" validate:"required"`
	Executor            string   `json:"executor" validate:"required"`
	Status              string   `json:"status" validate:"required"`
	ExternalExecutionID string   `json:"external_execution_id,omitempty"`
	LatencyMS           *float64 `json:"latency_ms,omitempty"`
	ThroughputRPS       *float64 `json:"throughput_rps,omitempty"`
	Message             string   `json:"message,omitempty"`
}

type AutomationFeedbackRecord struct {
	AutomationFeedbackRequest
	ReceivedAt string `json:"received_at"`
}

type OpsLLMBenchmark struct {
	Version    string                  `json:"version"`
	Metadata   map[string]any          `json:"metadata"`
	Policies   map[string]OpsLLMPolicy `json:"policies"`
	Candidates []OpsLLMCandidate       `json:"candidates"`
}

type OpsLLMPolicy struct {
	Description string             `json:"description"`
	Weights     map[string]float64 `json:"weights"`
}

type OpsLLMCandidate struct {
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
	ActionValidityRate    float64  `json:"action_validity_rate"`
	ConsistencyScore      float64  `json:"consistency_score"`
	EstimatedCostPer1KOps float64  `json:"estimated_cost_per_1k_ops"`
	AverageLatencyMS      float64  `json:"average_latency_ms"`
	Notes                 []string `json:"notes"`
}

type OpsLLMSelectRequest struct {
	Policy string `json:"policy" example:"quality_first"`
}

type ErrorResponse struct {
	Valid   bool   `json:"valid" example:"false"`
	Message string `json:"message" example:"Malformed request body: check JSON syntax"`
}

type OpsLLMSelectionResponse struct {
	Valid               bool               `json:"valid"`
	Policy              string             `json:"policy"`
	SelectedModel       string             `json:"selected_model"`
	SelectedActualModel string             `json:"selected_actual_model"`
	SelectedProvider    string             `json:"selected_provider"`
	EvaluationSource    string             `json:"evaluation_source"`
	EvaluationType      string             `json:"evaluation_type"`
	BenchmarkStatus     string             `json:"benchmark_status"`
	SelectedScore       float64            `json:"selected_score"`
	Rationale           string             `json:"rationale"`
	Ranking             []OpsLLMRankedItem `json:"ranking"`
}

type OpsLLMRankedItem struct {
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

type VMCompatibilityRequest struct {
	Workload string             `json:"workload" validate:"required" example:"llm-chat-inference"`
	TargetVM VMResourceSnapshot `json:"target_vm" validate:"required"`
}

type VMRequirementsConfig struct {
	Version        string                  `json:"version"`
	ValidationMode string                  `json:"validation_mode"`
	Workloads      []VMWorkloadRequirement `json:"workloads"`
}

type VMWorkloadRequirement struct {
	ID                     string   `json:"id"`
	ServiceName            string   `json:"service_name"`
	RequiredAccelerator    string   `json:"required_accelerator"`
	MinimumCPUCores        int      `json:"minimum_cpu_cores,omitempty"`
	MinimumMemoryGB        float64  `json:"minimum_memory_gb,omitempty"`
	MinimumGPUMemoryGB     float64  `json:"minimum_gpu_memory_gb,omitempty"`
	LatencySLOMS           *float64 `json:"latency_slo_ms,omitempty"`
	MinimumThroughputRPS   *float64 `json:"minimum_throughput_rps,omitempty"`
	AllowedControlActions  []string `json:"allowed_control_actions"`
	RequirementSource      string   `json:"requirement_source"`
	RequirementsRecordedAt string   `json:"requirements_recorded_at,omitempty"`
}

type VMResourceSnapshot struct {
	ID               string                `json:"id" validate:"required"`
	Source           string                `json:"source" validate:"required"`
	EvidenceStatus   string                `json:"evidence_status" validate:"required"`
	Provider         string                `json:"provider,omitempty"`
	Region           string                `json:"region,omitempty"`
	AvailabilityZone string                `json:"availability_zone,omitempty"`
	InstanceType     string                `json:"instance_type,omitempty"`
	Accelerator      string                `json:"accelerator" validate:"required"`
	CPUCores         int                   `json:"cpu_cores,omitempty"`
	MemoryGB         float64               `json:"memory_gb,omitempty"`
	GPUModel         string                `json:"gpu_model,omitempty"`
	GPUMemoryMiB     int                   `json:"gpu_memory_mib,omitempty"`
	DriverVersion    string                `json:"driver_version,omitempty"`
	CUDAVersion      string                `json:"cuda_version,omitempty"`
	CollectedAt      string                `json:"collected_at,omitempty"`
	Performance      VMPerformanceEvidence `json:"performance"`
}

type VMPerformanceEvidence struct {
	Status        string   `json:"status"`
	LatencyMS     *float64 `json:"latency_ms,omitempty"`
	ThroughputRPS *float64 `json:"throughput_rps,omitempty"`
	CostPerHour   *float64 `json:"cost_per_hour,omitempty"`
	MeasuredAt    string   `json:"measured_at,omitempty"`
	Source        string   `json:"source,omitempty"`
}

type VMCompatibilityCheck struct {
	Name     string `json:"name"`
	Status   string `json:"status"`
	Actual   string `json:"actual,omitempty"`
	Required string `json:"required,omitempty"`
	Reason   string `json:"reason"`
}

type VMCompatibilityResponse struct {
	Valid                bool                   `json:"valid"`
	ValidationMode       string                 `json:"validation_mode"`
	Workload             string                 `json:"workload"`
	TargetVMID           string                 `json:"target_vm_id"`
	ResourceSource       string                 `json:"resource_source"`
	EvidenceStatus       string                 `json:"evidence_status"`
	CompatibilityStatus  string                 `json:"compatibility_status"`
	ResourceChecksPassed bool                   `json:"resource_checks_passed"`
	PerformanceStatus    string                 `json:"performance_status"`
	Action               string                 `json:"action"`
	Reason               string                 `json:"reason"`
	Checks               []VMCompatibilityCheck `json:"checks"`
}

type ServiceOperationsRequest struct {
	LLMConfigPath     string             `json:"llm_config" example:"config/ops_llm_benchmark.json"`
	LLMCandidatesPath string             `json:"llm_candidates,omitempty" example:"config/ops_llm_eval_candidates.local_ollama.json"`
	LLMCandidateID    string             `json:"llm_candidate_id,omitempty" example:"qwen3.5-ops-planner"`
	LLMPolicy         string             `json:"llm_policy" example:"quality_first"`
	VMRequirements    string             `json:"vm_requirements" example:"config/vm_workload_requirements.json"`
	Workload          string             `json:"workload" validate:"required" example:"llm-chat-inference"`
	TargetVM          VMResourceSnapshot `json:"target_vm" validate:"required"`
	Observations      map[string]any     `json:"observations,omitempty"`
	OperationService  string             `json:"operation_service" example:"llm-chat-inference"`
	OperationResource string             `json:"operation_resource"`
	Mode              string             `json:"mode" example:"plan_only"`
	GuardBackend      string             `json:"guard_backend" example:"go"`
}

type DeploymentPlanResponse struct {
	VMCompatibilityResponse
	DeploymentPlan DeploymentPlan `json:"deployment_plan"`
}

type DeploymentPlan struct {
	Workload           string   `json:"workload"`
	ServiceName        string   `json:"service_name"`
	TargetVMID         string   `json:"target_vm_id"`
	TargetAccelerator  string   `json:"target_accelerator"`
	ExecutorType       string   `json:"executor_type"`
	RequiredCapability string   `json:"required_capability"`
	SelectedExecutor   string   `json:"selected_executor,omitempty"`
	RequestedAction    string   `json:"requested_action"`
	AllowedActions     []string `json:"allowed_actions"`
	Preconditions      []string `json:"preconditions"`
	ExecutionStatus    string   `json:"execution_status"`
	FeedbackRequired   bool     `json:"feedback_required"`
}

type DeploymentValidation struct {
	Mode   string   `json:"mode"`
	Valid  bool     `json:"valid"`
	Checks []string `json:"checks"`
	Reason string   `json:"reason"`
}

type AgentReviews struct {
	Application    AgentReview `json:"application"`
	Infrastructure AgentReview `json:"infrastructure"`
	Cost           AgentReview `json:"cost"`
}

type AgentReview struct {
	ReviewerType string            `json:"reviewer_type"`
	Agent        string            `json:"agent,omitempty"`
	Action       string            `json:"action"`
	Reward       float64           `json:"reward"`
	Approved     bool              `json:"approved"`
	Skipped      bool              `json:"skipped,omitempty"`
	Reason       string            `json:"reason"`
	Parameters   map[string]string `json:"parameters"`
}

type OperationReadiness struct {
	Valid          bool   `json:"valid"`
	Skipped        bool   `json:"skipped"`
	Service        string `json:"service"`
	TargetResource string `json:"target_resource"`
	Reason         string `json:"reason"`
}

type GuardValidation struct {
	Backend           string   `json:"backend"`
	Valid             bool     `json:"valid"`
	RuntimeWired      bool     `json:"runtime_wired"`
	Mode              string   `json:"mode"`
	Boundary          string   `json:"boundary"`
	OperationService  string   `json:"operation_service"`
	OperationResource string   `json:"operation_resource"`
	CheckedActions    []string `json:"checked_actions"`
	Reason            string   `json:"reason"`
}

type ServiceOperationsResponse struct {
	Command                 string                      `json:"command"`
	Valid                   bool                        `json:"valid"`
	SelectedLLM             string                      `json:"selected_llm"`
	SelectedActualModel     string                      `json:"selected_actual_model"`
	SelectedProvider        string                      `json:"selected_provider"`
	EvaluationSource        string                      `json:"evaluation_source"`
	EvaluationType          string                      `json:"evaluation_type"`
	BenchmarkStatus         string                      `json:"benchmark_status"`
	DecisionExecutionStatus string                      `json:"decision_execution_status"`
	LLMAutomationAction     LLMAutomationActionResponse `json:"llm_automation_action"`
	RuntimeModel            string                      `json:"runtime_model"`
	SelectedResource        string                      `json:"selected_resource"`
	DeploymentPlan          DeploymentPlan              `json:"deployment_plan"`
	InferenceDeploymentPlan DeploymentPlanResponse      `json:"inference_deployment_plan"`
	DeploymentValidation    DeploymentValidation        `json:"deployment_validation"`
	DeploymentExecutionMode string                      `json:"deployment_execution_mode"`
	AgentReviews            AgentReviews                `json:"agent_reviews"`
	Operation               OperationReadiness          `json:"operation"`
	OperationPipelineReady  bool                        `json:"operation_pipeline_ready"`
	GuardBackend            string                      `json:"guard_backend"`
	GuardValidation         GuardValidation             `json:"guard_validation"`
	Metadata                map[string]string           `json:"metadata"`
}
