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

type InferenceConfig struct {
	Version   string              `json:"version"`
	Weights   map[string]float64  `json:"weights"`
	Resources []InferenceResource `json:"resources"`
	Workloads []InferenceWorkload `json:"workloads"`
}

type InferenceResource struct {
	ID                    string            `json:"id"`
	Accelerator           string            `json:"accelerator"`
	CPUCores              int               `json:"cpu_cores"`
	MemoryGB              int               `json:"memory_gb"`
	GPUMemoryGB           float64           `json:"gpu_memory_gb"`
	ExpectedLatencyMS     float64           `json:"expected_latency_ms"`
	ExpectedThroughputRPS float64           `json:"expected_throughput_rps"`
	CostPerHour           float64           `json:"cost_per_hour"`
	AvailableInstances    int               `json:"available_instances"`
	PlacementLabels       map[string]string `json:"placement_labels"`
	ResourceCapacity      map[string]string `json:"resource_capacity"`
	SupportedModelTypes   []string          `json:"supported_model_types"`
}

type InferenceWorkload struct {
	ID                  string  `json:"id"`
	ModelType           string  `json:"model_type"`
	RequiresAccelerator bool    `json:"requires_accelerator"`
	EstimatedVRAMGB     float64 `json:"estimated_vram_gb"`
	LatencySLOMS        float64 `json:"latency_slo_ms"`
	MinThroughputRPS    float64 `json:"min_throughput_rps"`
	BatchSize           int     `json:"batch_size"`
	ServiceName         string  `json:"service_name"`
	ContainerImage      string  `json:"container_image"`
	Instances           int     `json:"instances"`
}

type WorkloadRequest struct {
	Workload string `json:"workload" validate:"required" example:"llm-chat-inference"`
}

type ServiceOperationsRequest struct {
	LLMConfigPath     string `json:"llm_config" example:"config/ops_llm_benchmark.json"`
	InferenceConfig   string `json:"inference_config" example:"config/inference_optimization.json"`
	LLMPolicy         string `json:"llm_policy" example:"quality_first"`
	Workload          string `json:"workload" validate:"required" example:"llm-chat-inference"`
	OperationService  string `json:"operation_service" example:"llm-chat-inference"`
	OperationResource string `json:"operation_resource" example:"gpu-vm-l4"`
	Mode              string `json:"mode" example:"mock"`
	GuardBackend      string `json:"guard_backend" example:"go"`
}

type PlacementResponse struct {
	Valid             bool                 `json:"valid"`
	Workload          string               `json:"workload"`
	SelectedResource  string               `json:"selected_resource"`
	Action            string               `json:"action"`
	Score             float64              `json:"score"`
	LatencyMS         float64              `json:"latency_ms"`
	ThroughputRPS     float64              `json:"throughput_rps"`
	CostPerHour       float64              `json:"cost_per_hour"`
	SLOSatisfied      bool                 `json:"slo_satisfied"`
	Reason            string               `json:"reason"`
	RejectedResources map[string]string    `json:"rejected_resources"`
	RankedCandidates  []PlacementCandidate `json:"ranked_candidates"`
}

type PlacementCandidate struct {
	Resource           string  `json:"resource"`
	Accelerator        string  `json:"accelerator"`
	Score              float64 `json:"score"`
	LatencyMS          float64 `json:"latency_ms"`
	ThroughputRPS      float64 `json:"throughput_rps"`
	CostPerHour        float64 `json:"cost_per_hour"`
	AvailableInstances int     `json:"available_instances"`
	Action             string  `json:"action"`
}

type DeploymentPlanResponse struct {
	PlacementResponse
	DeploymentPlan DeploymentPlan `json:"deployment_plan"`
}

type DeploymentPlan struct {
	ServiceName       string             `json:"service_name"`
	ContainerImage    string             `json:"container_image"`
	TargetResource    string             `json:"target_resource"`
	TargetAccelerator string             `json:"target_accelerator"`
	VM                VMDeploymentPlan   `json:"vm_deployment"`
	ControlActions    []string           `json:"control_actions"`
	MonitoringMetrics []string           `json:"monitoring_metrics"`
	SLO               map[string]float64 `json:"slo"`
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
	Agent      string            `json:"agent"`
	Action     string            `json:"action"`
	Reward     float64           `json:"reward"`
	Approved   bool              `json:"approved"`
	Reason     string            `json:"reason"`
	Parameters map[string]string `json:"parameters"`
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
	Command                 string                 `json:"command"`
	Valid                   bool                   `json:"valid"`
	SelectedLLM             string                 `json:"selected_llm"`
	SelectedActualModel     string                 `json:"selected_actual_model"`
	SelectedProvider        string                 `json:"selected_provider"`
	EvaluationSource        string                 `json:"evaluation_source"`
	EvaluationType          string                 `json:"evaluation_type"`
	BenchmarkStatus         string                 `json:"benchmark_status"`
	RuntimeModel            string                 `json:"runtime_model"`
	SelectedResource        string                 `json:"selected_resource"`
	DeploymentPlan          DeploymentPlan         `json:"deployment_plan"`
	InferenceDeploymentPlan DeploymentPlanResponse `json:"inference_deployment_plan"`
	DeploymentValidation    DeploymentValidation   `json:"deployment_validation"`
	DeploymentExecutionMode string                 `json:"deployment_execution_mode"`
	AgentReviews            AgentReviews           `json:"agent_reviews"`
	Operation               OperationReadiness     `json:"operation"`
	OperationPipelineReady  bool                   `json:"operation_pipeline_ready"`
	GuardBackend            string                 `json:"guard_backend"`
	GuardValidation         GuardValidation        `json:"guard_validation"`
	Metadata                map[string]string      `json:"metadata"`
}

type VMDeploymentPlan struct {
	Service              string            `json:"service"`
	Instances            int               `json:"instances"`
	PlacementConstraints map[string]string `json:"placement_constraints"`
	Resources            ResourceSpec      `json:"resources"`
}

type ResourceSpec struct {
	Requests map[string]string `json:"requests"`
	Limits   map[string]string `json:"limits"`
}
