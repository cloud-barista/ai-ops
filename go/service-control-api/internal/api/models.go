package api

type AgentRegistry struct {
	Version string         `json:"version"`
	Agents  []AgentProfile `json:"agents"`
}

type AgentProfile struct {
	Name             string   `json:"name"`
	KoreanName       string   `json:"korean_name"`
	Role             string   `json:"role"`
	Responsibilities []string `json:"responsibilities"`
	BoundedActions   []string `json:"bounded_actions"`
	RewardSignals    []string `json:"reward_signals"`
	Enabled          bool     `json:"enabled"`
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
	Provider              string   `json:"provider"`
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
	Policy string `json:"policy"`
}

type OpsLLMSelectionResponse struct {
	Valid         bool               `json:"valid"`
	Policy        string             `json:"policy"`
	SelectedModel string             `json:"selected_model"`
	SelectedScore float64            `json:"selected_score"`
	Rationale     string             `json:"rationale"`
	Ranking       []OpsLLMRankedItem `json:"ranking"`
}

type OpsLLMRankedItem struct {
	Model    string             `json:"model"`
	Provider string             `json:"provider"`
	Role     string             `json:"role"`
	Score    float64            `json:"score"`
	Metrics  map[string]float64 `json:"metrics"`
	Notes    []string           `json:"notes"`
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
	AvailableReplicas     int               `json:"available_replicas"`
	NodeSelector          map[string]string `json:"node_selector"`
	ResourceLimits        map[string]string `json:"resource_limits"`
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
	Namespace           string  `json:"namespace"`
	ContainerImage      string  `json:"container_image"`
	Replicas            int     `json:"replicas"`
}

type WorkloadRequest struct {
	Workload string `json:"workload"`
}

type ServiceOperationsRequest struct {
	LLMConfigPath   string `json:"llm_config"`
	InferenceConfig string `json:"inference_config"`
	LLMPolicy       string `json:"llm_policy"`
	Workload        string `json:"workload"`
	Namespace       string `json:"namespace"`
	Deployment      string `json:"deployment"`
	Mode            string `json:"mode"`
	GuardBackend    string `json:"guard_backend"`
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
	Resource          string  `json:"resource"`
	Accelerator       string  `json:"accelerator"`
	Score             float64 `json:"score"`
	LatencyMS         float64 `json:"latency_ms"`
	ThroughputRPS     float64 `json:"throughput_rps"`
	CostPerHour       float64 `json:"cost_per_hour"`
	AvailableReplicas int     `json:"available_replicas"`
	Action            string  `json:"action"`
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
	Kubernetes        KubernetesPlan     `json:"kubernetes"`
	ControlActions    []string           `json:"control_actions"`
	MonitoringMetrics []string           `json:"monitoring_metrics"`
	SLO               map[string]float64 `json:"slo"`
}

type DeploymentManifest struct {
	APIVersion string                   `json:"apiVersion"`
	Kind       string                   `json:"kind"`
	Metadata   map[string]any           `json:"metadata"`
	Spec       map[string]any           `json:"spec"`
}

type DeploymentDryRun struct {
	Command string `json:"command"`
	Mode    string `json:"mode"`
	Valid   bool   `json:"valid"`
	Stdout  string `json:"stdout"`
	Stderr  string `json:"stderr"`
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

type RecoveryReadiness struct {
	Valid   bool   `json:"valid"`
	Skipped bool   `json:"skipped"`
	Reason  string `json:"reason"`
}

type ServiceOperationsResponse struct {
	Command                 string                 `json:"command"`
	Valid                   bool                   `json:"valid"`
	SelectedLLM             string                 `json:"selected_llm"`
	RuntimeModel            string                 `json:"runtime_model"`
	SelectedResource        string                 `json:"selected_resource"`
	DeploymentPlan          DeploymentPlan         `json:"deployment_plan"`
	InferenceDeploymentPlan DeploymentPlanResponse `json:"inference_deployment_plan"`
	DeploymentManifest      DeploymentManifest     `json:"deployment_manifest"`
	DeploymentDryRun        DeploymentDryRun       `json:"deployment_dry_run"`
	AgentReviews            AgentReviews           `json:"agent_reviews"`
	Recovery                RecoveryReadiness      `json:"recovery"`
	RecoveryPipelineReady   bool                   `json:"recovery_pipeline_ready"`
	GuardBackend            string                 `json:"guard_backend"`
	Metadata                map[string]string      `json:"metadata"`
}

type KubernetesPlan struct {
	Namespace    string            `json:"namespace"`
	Deployment   string            `json:"deployment"`
	Replicas     int               `json:"replicas"`
	NodeSelector map[string]string `json:"node_selector"`
	Resources    ResourceSpec      `json:"resources"`
}

type ResourceSpec struct {
	Requests map[string]string `json:"requests"`
	Limits   map[string]string `json:"limits"`
}
