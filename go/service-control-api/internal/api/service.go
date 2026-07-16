package api

import (
	"context"
	"fmt"
	"math"
	"path/filepath"
	"sort"
)

type Service struct {
	config        ServerConfig
	runtimeAgents *runtimeAgentStore
}

func NewService(config ServerConfig) Service {
	return Service{
		config:        config,
		runtimeAgents: newRuntimeAgentStore(),
	}
}

func (service Service) ListAgents(ctx context.Context) (map[string]any, error) {
	if err := ensureContext(ctx); err != nil {
		return nil, err
	}
	path := service.config.path("config", "agent_registry.json")
	registry, err := loadAgentRegistry(path)
	if err != nil {
		return nil, err
	}
	for index := range registry.Agents {
		registry.Agents[index].Source = agentSourceConfiguration
	}
	registry.Agents = append(registry.Agents, service.runtimeAgents.list()...)
	sort.SliceStable(registry.Agents, func(i, j int) bool {
		return registry.Agents[i].Name < registry.Agents[j].Name
	})
	return map[string]any{
		"command":             "list-agents",
		"registry":            path,
		"version":             registry.Version,
		"persistence":         "configuration_and_process_memory",
		"runtime_agent_count": service.runtimeAgents.count(),
		"agents":              registry.Agents,
	}, nil
}

func (service Service) ListAgentsFromPath(ctx context.Context, path string) (map[string]any, error) {
	if err := ensureContext(ctx); err != nil {
		return nil, err
	}
	registry, err := loadAgentRegistry(path)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"command":  "list-agents",
		"registry": path,
		"version":  registry.Version,
		"agents":   registry.Agents,
	}, nil
}

func (service Service) ShowAgent(ctx context.Context, agentName string) (AgentProfile, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentProfile{}, err
	}
	if agent, ok := service.runtimeAgents.get(agentName); ok {
		return agent, nil
	}
	agent, err := service.ShowAgentFromPath(ctx, service.config.path("config", "agent_registry.json"), agentName)
	if err != nil {
		return AgentProfile{}, err
	}
	agent.Source = agentSourceConfiguration
	return agent, nil
}

func (service Service) ShowAgentFromPath(ctx context.Context, path string, agentName string) (AgentProfile, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentProfile{}, err
	}
	registry, err := loadAgentRegistry(path)
	if err != nil {
		return AgentProfile{}, err
	}
	return findAgent(registry.Agents, agentName)
}

func (service Service) ValidateAgentAction(ctx context.Context, agentName string, action string) (bool, error) {
	agent, err := service.ShowAgent(ctx, agentName)
	if err != nil {
		return false, err
	}
	return agent.Enabled && contains(agent.BoundedActions, action), nil
}

func (service Service) ValidateAgentActionFromPath(ctx context.Context, path string, agentName string, action string) (bool, error) {
	if err := ensureContext(ctx); err != nil {
		return false, err
	}
	registry, err := loadAgentRegistry(path)
	if err != nil {
		return false, err
	}
	agent, err := findAgent(registry.Agents, agentName)
	if err != nil {
		return false, err
	}
	return agent.Enabled && contains(agent.BoundedActions, action), nil
}

func (service Service) SelectOpsLLM(ctx context.Context, policyName string) (OpsLLMSelectionResponse, error) {
	return service.SelectOpsLLMFromPath(ctx, service.config.path("config", "ops_llm_benchmark.json"), policyName)
}

func (service Service) SelectOpsLLMFromPath(ctx context.Context, path string, policyName string) (OpsLLMSelectionResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return OpsLLMSelectionResponse{}, err
	}
	if policyName == "" {
		policyName = "quality_first"
	}

	config, err := loadJSON[OpsLLMBenchmark](path)
	if err != nil {
		return OpsLLMSelectionResponse{}, err
	}

	policy, ok := config.Policies[policyName]
	if !ok {
		return OpsLLMSelectionResponse{}, fmt.Errorf("unknown LLM selection policy: %s", policyName)
	}
	if len(config.Candidates) == 0 {
		return OpsLLMSelectionResponse{}, fmt.Errorf("at least one LLM candidate is required")
	}
	for _, candidate := range config.Candidates {
		if candidate.Model == "" {
			return OpsLLMSelectionResponse{}, fmt.Errorf("candidate model is required")
		}
		if candidate.TotalDetectionRuns <= 0 {
			return OpsLLMSelectionResponse{}, fmt.Errorf("candidate %s must define total_detection_runs > 0", candidate.Model)
		}
		if candidate.TotalMetricRuns <= 0 {
			return OpsLLMSelectionResponse{}, fmt.Errorf("candidate %s must define total_metric_runs > 0", candidate.Model)
		}
	}

	minTTD := minPositive(config.Candidates, func(candidate OpsLLMCandidate) float64 {
		return candidate.AverageTTDSeconds
	})
	minCost := minPositive(config.Candidates, func(candidate OpsLLMCandidate) float64 {
		return candidate.EstimatedCostPer1KOps
	})
	minLatency := minPositive(config.Candidates, func(candidate OpsLLMCandidate) float64 {
		return candidate.AverageLatencyMS
	})

	ranking := make([]OpsLLMRankedItem, 0, len(config.Candidates))
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
		ranking = append(ranking, OpsLLMRankedItem{
			Model:            candidate.Model,
			ActualModel:      candidate.ActualModel,
			Provider:         candidate.Provider,
			EvaluationSource: candidate.EvaluationSource,
			EvaluationType:   candidate.EvaluationType,
			BenchmarkStatus:  candidate.BenchmarkStatus,
			Role:             candidate.Role,
			Score:            round6(score),
			Metrics:          roundMetrics(metrics),
			Notes:            candidate.Notes,
		})
	}

	sort.SliceStable(ranking, func(i, j int) bool {
		if ranking[i].Score == ranking[j].Score {
			return ranking[i].Model < ranking[j].Model
		}
		return ranking[i].Score > ranking[j].Score
	})

	selected := ranking[0]
	return OpsLLMSelectionResponse{
		Valid:               true,
		Policy:              policyName,
		SelectedModel:       selected.Model,
		SelectedActualModel: selected.ActualModel,
		SelectedProvider:    selected.Provider,
		EvaluationSource:    selected.EvaluationSource,
		EvaluationType:      selected.EvaluationType,
		BenchmarkStatus:     selected.BenchmarkStatus,
		SelectedScore:       selected.Score,
		Rationale: fmt.Sprintf(
			"%s ranked first under %s because the weighted Ops accuracy, action safety, consistency, latency, and cost criteria produced the highest score.",
			selected.Model,
			policyName,
		),
		Ranking: ranking,
	}, nil
}

func (service Service) RecommendPlacement(ctx context.Context, workloadID string) (PlacementResponse, error) {
	return service.RecommendPlacementFromPath(ctx, service.config.path("config", "inference_optimization.json"), workloadID)
}

func (service Service) RecommendPlacementFromPath(ctx context.Context, path string, workloadID string) (PlacementResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return PlacementResponse{}, err
	}
	_, workload, candidates, rejected, err := service.rankPlacementFromPath(ctx, path, workloadID)
	if err != nil {
		return PlacementResponse{}, err
	}
	if len(candidates) == 0 {
		return PlacementResponse{
			Valid:             false,
			Workload:          workload.ID,
			SelectedResource:  "",
			Action:            "manual_review_required",
			Reason:            "no eligible CPU/GPU VM resource satisfied the workload constraints",
			RejectedResources: rejected,
		}, nil
	}

	sort.SliceStable(candidates, func(i, j int) bool {
		if candidates[i].Score == candidates[j].Score {
			if candidates[i].CostPerHour == candidates[j].CostPerHour {
				return candidates[i].Resource < candidates[j].Resource
			}
			return candidates[i].CostPerHour < candidates[j].CostPerHour
		}
		return candidates[i].Score > candidates[j].Score
	})
	best := candidates[0]
	return PlacementResponse{
		Valid:             true,
		Workload:          workload.ID,
		SelectedResource:  best.Resource,
		Action:            best.Action,
		Score:             best.Score,
		LatencyMS:         best.LatencyMS,
		ThroughputRPS:     best.ThroughputRPS,
		CostPerHour:       best.CostPerHour,
		SLOSatisfied:      true,
		Reason:            "selected resource satisfies latency, throughput, accelerator, and capacity constraints",
		RejectedResources: rejected,
		RankedCandidates:  candidates,
	}, nil
}

func (service Service) BuildDeploymentPlan(ctx context.Context, workloadID string) (DeploymentPlanResponse, error) {
	return service.BuildDeploymentPlanFromPath(ctx, service.config.path("config", "inference_optimization.json"), workloadID)
}

func (service Service) BuildDeploymentPlanFromPath(ctx context.Context, path string, workloadID string) (DeploymentPlanResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return DeploymentPlanResponse{}, err
	}
	placement, err := service.RecommendPlacementFromPath(ctx, path, workloadID)
	if err != nil {
		return DeploymentPlanResponse{}, err
	}
	if !placement.Valid {
		return DeploymentPlanResponse{PlacementResponse: placement}, nil
	}

	config, err := loadJSON[InferenceConfig](path)
	if err != nil {
		return DeploymentPlanResponse{}, err
	}
	workload, ok := findWorkload(config.Workloads, workloadID)
	if !ok {
		return DeploymentPlanResponse{}, fmt.Errorf("unknown workload: %s", workloadID)
	}
	resource := findResource(config.Resources, placement.SelectedResource)
	if resource.ID == "" {
		return DeploymentPlanResponse{}, fmt.Errorf("selected resource is not defined: %s", placement.SelectedResource)
	}

	capacity := copyStringMap(resource.ResourceCapacity)
	if resource.Accelerator == "gpu" {
		if _, ok := capacity["accelerator_count"]; !ok {
			capacity["accelerator_count"] = "1"
		}
	}
	if resource.Accelerator == "npu" {
		if _, ok := capacity["accelerator_count"]; !ok {
			capacity["accelerator_count"] = "1"
		}
	}
	if workload.EstimatedVRAMGB > 0 && resource.Accelerator != "cpu" {
		capacity["vram_gb"] = trimFloat(workload.EstimatedVRAMGB)
	}
	placementLabels := copyStringMap(resource.PlacementLabels)
	if len(placementLabels) == 0 {
		placementLabels["aiops.resource/accelerator"] = resource.Accelerator
	}

	plan := DeploymentPlan{
		ServiceName:       workload.ServiceName,
		ContainerImage:    workload.ContainerImage,
		TargetResource:    resource.ID,
		TargetAccelerator: resource.Accelerator,
		VM: VMDeploymentPlan{
			Service:              workload.ServiceName,
			Instances:            workload.Instances,
			PlacementConstraints: placementLabels,
			Resources: ResourceSpec{
				Requests: map[string]string{
					"cpu_cores": fmt.Sprintf("%d", maxInt(1, minInt(resource.CPUCores, 8))),
					"memory_gb": fmt.Sprintf("%d", maxInt(1, minInt(resource.MemoryGB, 32))),
				},
				Limits: capacity,
			},
		},
		ControlActions: []string{
			placement.Action,
			"scale_instances",
			"monitor_latency",
			"rollback_on_slo_violation",
		},
		MonitoringMetrics: []string{
			"inference_latency_ms",
			"inference_throughput_rps",
			"gpu_memory_utilization",
			"cost_per_hour",
		},
		SLO: map[string]float64{
			"latency_ms":         workload.LatencySLOMS,
			"min_throughput_rps": workload.MinThroughputRPS,
		},
	}

	return DeploymentPlanResponse{
		PlacementResponse: placement,
		DeploymentPlan:    plan,
	}, nil
}

func (service Service) RunServiceOperations(ctx context.Context, request ServiceOperationsRequest) (ServiceOperationsResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return ServiceOperationsResponse{}, err
	}
	if request.LLMPolicy == "" {
		request.LLMPolicy = "quality_first"
	}
	if request.Mode == "" {
		request.Mode = "mock"
	}
	if request.GuardBackend == "" {
		request.GuardBackend = "go"
	}
	llmConfigPath := service.resolvePath(request.LLMConfigPath, "config", "ops_llm_benchmark.json")
	inferenceConfigPath := service.resolvePath(request.InferenceConfig, "config", "inference_optimization.json")

	llmSelection, err := service.SelectOpsLLMFromPath(ctx, llmConfigPath, request.LLMPolicy)
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	deploymentPlan, err := service.BuildDeploymentPlanFromPath(ctx, inferenceConfigPath, request.Workload)
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	operationService, operationResource := normalizeOperationContext(request, deploymentPlan)
	deploymentValidation := validateVMDeploymentPlan(deploymentPlan.DeploymentPlan, request.Mode)
	reviews := buildAgentReviews(deploymentPlan)
	operation := OperationReadiness{
		Valid:          operationService != "" && operationResource != "",
		Skipped:        true,
		Service:        operationService,
		TargetResource: operationResource,
		Reason:         "operation target is validated for VM deployment and control readiness",
	}
	guardValidation := buildGuardValidation(request, operationService, operationResource)
	ready := deploymentValidation.Valid &&
		reviews.Application.Approved &&
		reviews.Infrastructure.Approved &&
		reviews.Cost.Approved &&
		operation.Valid &&
		guardValidation.Valid

	return ServiceOperationsResponse{
		Command:                 "run-service-operations",
		Valid:                   ready,
		SelectedLLM:             llmSelection.SelectedModel,
		SelectedActualModel:     llmSelection.SelectedActualModel,
		SelectedProvider:        llmSelection.SelectedProvider,
		EvaluationSource:        llmSelection.EvaluationSource,
		EvaluationType:          llmSelection.EvaluationType,
		BenchmarkStatus:         llmSelection.BenchmarkStatus,
		RuntimeModel:            runtimeModelFromSelection(llmSelection),
		SelectedResource:        deploymentPlan.SelectedResource,
		DeploymentPlan:          deploymentPlan.DeploymentPlan,
		InferenceDeploymentPlan: deploymentPlan,
		DeploymentValidation:    deploymentValidation,
		DeploymentExecutionMode: request.Mode,
		AgentReviews:            reviews,
		Operation:               operation,
		OperationPipelineReady:  ready,
		GuardBackend:            request.GuardBackend,
		GuardValidation:         guardValidation,
		Metadata: map[string]string{
			"llm_policy":                request.LLMPolicy,
			"workload":                  request.Workload,
			"mode":                      request.Mode,
			"operation_service":         operationService,
			"operation_resource":        operationResource,
			"selected_actual_model":     llmSelection.SelectedActualModel,
			"selected_provider":         llmSelection.SelectedProvider,
			"evaluation_source":         llmSelection.EvaluationSource,
			"evaluation_type":           llmSelection.EvaluationType,
			"benchmark_status":          llmSelection.BenchmarkStatus,
			"deployment_execution_mode": request.Mode,
		},
	}, nil
}

func normalizeOperationContext(request ServiceOperationsRequest, plan DeploymentPlanResponse) (string, string) {
	serviceName := request.OperationService
	if serviceName == "" {
		serviceName = plan.DeploymentPlan.ServiceName
	}
	targetResource := request.OperationResource
	if targetResource == "" {
		targetResource = plan.SelectedResource
	}
	return serviceName, targetResource
}

func buildGuardValidation(request ServiceOperationsRequest, serviceName string, targetResource string) GuardValidation {
	result := GuardValidation{
		Backend:           request.GuardBackend,
		RuntimeWired:      false,
		Mode:              request.Mode,
		Boundary:          "standalone aiops-guard bounded VM action contract",
		OperationService:  serviceName,
		OperationResource: targetResource,
		CheckedActions: []string{
			"observe_only",
			"restart_service",
			"scale_out",
			"scale_in",
		},
	}
	if request.GuardBackend != "go" {
		result.Valid = false
		result.Reason = "only the Go guard backend is supported in the prototype validation path"
		return result
	}
	if serviceName == "" || targetResource == "" {
		result.Valid = false
		result.Reason = "operation_service and operation_resource are required to prepare bounded guard validation"
		return result
	}
	result.Valid = true
	result.Reason = "service-control-api prepared the bounded VM operation context; infrastructure execution remains outside this prototype"
	return result
}

func (service Service) resolvePath(path string, defaultParts ...string) string {
	if path == "" {
		return service.config.path(defaultParts...)
	}
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(service.config.RepoRoot, path)
}

func (service Service) rankPlacementFromPath(ctx context.Context, path string, workloadID string) (InferenceConfig, InferenceWorkload, []PlacementCandidate, map[string]string, error) {
	if err := ensureContext(ctx); err != nil {
		return InferenceConfig{}, InferenceWorkload{}, nil, nil, err
	}
	config, err := loadJSON[InferenceConfig](path)
	if err != nil {
		return InferenceConfig{}, InferenceWorkload{}, nil, nil, err
	}
	workload, ok := findWorkload(config.Workloads, workloadID)
	if !ok {
		return InferenceConfig{}, InferenceWorkload{}, nil, nil, fmt.Errorf("unknown workload: %s", workloadID)
	}

	rejected := map[string]string{}
	eligible := []InferenceResource{}
	for _, resource := range config.Resources {
		if reason := rejectPlacement(workload, resource); reason != "" {
			rejected[resource.ID] = reason
			continue
		}
		eligible = append(eligible, resource)
	}
	if len(eligible) == 0 {
		return config, workload, nil, rejected, nil
	}

	minCost := math.MaxFloat64
	maxCapacity := 1
	for _, resource := range eligible {
		if resource.CostPerHour > 0 && resource.CostPerHour < minCost {
			minCost = resource.CostPerHour
		}
		if resource.AvailableInstances > maxCapacity {
			maxCapacity = resource.AvailableInstances
		}
	}
	if minCost == math.MaxFloat64 {
		minCost = 0
	}

	candidates := []PlacementCandidate{}
	for _, resource := range eligible {
		latencyScore := cappedRatio(workload.LatencySLOMS, resource.ExpectedLatencyMS)
		throughputScore := cappedRatio(resource.ExpectedThroughputRPS, workload.MinThroughputRPS)
		costScore := inverseScore(minCost, resource.CostPerHour)
		capacityScore := float64(resource.AvailableInstances) / float64(maxCapacity)
		score := config.Weights["latency"]*latencyScore +
			config.Weights["throughput"]*throughputScore +
			config.Weights["cost"]*costScore +
			config.Weights["capacity"]*capacityScore
		candidates = append(candidates, PlacementCandidate{
			Resource:           resource.ID,
			Accelerator:        resource.Accelerator,
			Score:              round6(score),
			LatencyMS:          resource.ExpectedLatencyMS,
			ThroughputRPS:      resource.ExpectedThroughputRPS,
			CostPerHour:        resource.CostPerHour,
			AvailableInstances: resource.AvailableInstances,
			Action:             actionForResource(resource),
		})
	}
	return config, workload, candidates, rejected, nil
}

func rejectPlacement(workload InferenceWorkload, resource InferenceResource) string {
	if workload.RequiresAccelerator && resource.Accelerator == "cpu" {
		return "accelerator required but resource is CPU-only"
	}
	if !contains(resource.SupportedModelTypes, workload.ModelType) {
		return fmt.Sprintf("model type %s is not supported", workload.ModelType)
	}
	if resource.Accelerator != "cpu" && workload.EstimatedVRAMGB > resource.GPUMemoryGB {
		return fmt.Sprintf("estimated VRAM %gGB exceeds resource GPU memory %gGB", workload.EstimatedVRAMGB, resource.GPUMemoryGB)
	}
	if resource.ExpectedLatencyMS > workload.LatencySLOMS {
		return fmt.Sprintf("latency %gms exceeds SLO %gms", resource.ExpectedLatencyMS, workload.LatencySLOMS)
	}
	if resource.ExpectedThroughputRPS < workload.MinThroughputRPS {
		return fmt.Sprintf("throughput %grps is below required %grps", resource.ExpectedThroughputRPS, workload.MinThroughputRPS)
	}
	if resource.AvailableInstances <= 0 {
		return "no available VM capacity"
	}
	return ""
}

func loadAgentRegistry(path string) (AgentRegistry, error) {
	registry, err := loadJSON[AgentRegistry](path)
	if err != nil {
		return AgentRegistry{}, err
	}
	if len(registry.Agents) == 0 {
		return AgentRegistry{}, fmt.Errorf("registry must contain at least one agent")
	}
	seen := map[string]bool{}
	for _, agent := range registry.Agents {
		if agent.Name == "" {
			return AgentRegistry{}, fmt.Errorf("agent name is required")
		}
		if seen[agent.Name] {
			return AgentRegistry{}, fmt.Errorf("duplicate agent: %s", agent.Name)
		}
		seen[agent.Name] = true
		if len(agent.BoundedActions) == 0 {
			return AgentRegistry{}, fmt.Errorf("agent %s must define bounded_actions", agent.Name)
		}
	}
	sort.SliceStable(registry.Agents, func(i, j int) bool {
		return registry.Agents[i].Name < registry.Agents[j].Name
	})
	return registry, nil
}

func findAgent(agents []AgentProfile, name string) (AgentProfile, error) {
	for _, agent := range agents {
		if agent.Name == name {
			return agent, nil
		}
	}
	return AgentProfile{}, fmt.Errorf("unknown agent: %s", name)
}

func actionForResource(resource InferenceResource) string {
	if resource.Accelerator == "gpu" {
		return "deploy_on_gpu_vm"
	}
	if resource.Accelerator == "npu" {
		return "deploy_on_npu_vm"
	}
	return "deploy_on_cpu_vm"
}

func validateVMDeploymentPlan(plan DeploymentPlan, mode string) DeploymentValidation {
	if mode == "" {
		mode = "mock"
	}
	checks := []string{
		"service_name_present",
		"container_image_present",
		"target_resource_present",
		"instance_count_positive",
		"resource_request_present",
	}
	valid := plan.ServiceName != "" &&
		plan.ContainerImage != "" &&
		plan.TargetResource != "" &&
		plan.VM.Service != "" &&
		plan.VM.Instances > 0 &&
		len(plan.VM.Resources.Requests) > 0
	reason := "VM deployment specification passed the prototype readiness checks"
	if !valid {
		reason = "VM deployment specification is missing a required field"
	}
	return DeploymentValidation{
		Mode:   mode,
		Valid:  valid,
		Checks: checks,
		Reason: reason,
	}
}

func buildAgentReviews(plan DeploymentPlanResponse) AgentReviews {
	return AgentReviews{
		Application: AgentReview{
			Agent:    "AIApplicationManagementAgent",
			Action:   "app_plan_deployment",
			Reward:   0.8,
			Approved: plan.Valid,
			Reason:   "AI application deployment plan is ready for CPU/GPU VM deployment validation.",
			Parameters: map[string]string{
				"workload":          plan.Workload,
				"service":           plan.DeploymentPlan.VM.Service,
				"instances":         fmt.Sprintf("%d", plan.DeploymentPlan.VM.Instances),
				"selected_resource": plan.SelectedResource,
			},
		},
		Infrastructure: buildInfrastructureReview(plan),
		Cost:           buildCostReview(plan),
	}
}

func buildInfrastructureReview(plan DeploymentPlanResponse) AgentReview {
	if !plan.Valid {
		return AgentReview{
			Agent:      "AISemiconductorInfraOpsAgent",
			Action:     "infra_placement_rejected",
			Reward:     -1.0,
			Approved:   false,
			Reason:     plan.Reason,
			Parameters: map[string]string{"selected_resource": plan.SelectedResource},
		}
	}
	return AgentReview{
		Agent:    "AISemiconductorInfraOpsAgent",
		Action:   "infra_placement_approved",
		Reward:   0.7,
		Approved: true,
		Reason:   "Selected CPU/GPU VM resource satisfies SLO and capacity constraints.",
		Parameters: map[string]string{
			"selected_resource": plan.SelectedResource,
			"latency_ms":        trimFloat(plan.LatencyMS),
			"throughput_rps":    trimFloat(plan.ThroughputRPS),
			"cost_per_hour":     trimFloat(plan.CostPerHour),
		},
	}
}

func buildCostReview(plan DeploymentPlanResponse) AgentReview {
	if !plan.Valid {
		return AgentReview{
			Agent:      "CostOptimizationAgent",
			Action:     "cost_placement_rejected",
			Reward:     -0.5,
			Approved:   false,
			Reason:     plan.Reason,
			Parameters: map[string]string{"selected_resource": plan.SelectedResource},
		}
	}
	return AgentReview{
		Agent:    "CostOptimizationAgent",
		Action:   "cost_placement_approved",
		Reward:   0.55,
		Approved: true,
		Reason:   "Selected CPU/GPU VM resource is within the cost policy.",
		Parameters: map[string]string{
			"selected_resource": plan.SelectedResource,
			"cost_per_hour":     fmt.Sprintf("%.2f", plan.CostPerHour),
		},
	}
}

func runtimeModelFromSelection(selection OpsLLMSelectionResponse) string {
	if selection.SelectedModel != "code-cross-check-agent" {
		return selection.SelectedModel
	}
	for _, candidate := range selection.Ranking {
		if candidate.Model != "" && candidate.Model != "code-cross-check-agent" {
			return candidate.Model
		}
	}
	return selection.SelectedModel
}

func ensureContext(ctx context.Context) error {
	if ctx == nil {
		return fmt.Errorf("request context is required")
	}
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("request context ended: %w", err)
	}
	return nil
}

func findWorkload(workloads []InferenceWorkload, id string) (InferenceWorkload, bool) {
	for _, workload := range workloads {
		if workload.ID == id {
			return workload, true
		}
	}
	return InferenceWorkload{}, false
}

func findResource(resources []InferenceResource, id string) InferenceResource {
	for _, resource := range resources {
		if resource.ID == id {
			return resource
		}
	}
	return InferenceResource{}
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func safeRatio(numerator float64, denominator float64) float64 {
	if denominator <= 0 {
		return 0
	}
	return numerator / denominator
}

func minPositive(candidates []OpsLLMCandidate, value func(OpsLLMCandidate) float64) float64 {
	min := math.MaxFloat64
	for _, candidate := range candidates {
		current := value(candidate)
		if current > 0 && current < min {
			min = current
		}
	}
	if min == math.MaxFloat64 {
		return 0
	}
	return min
}

func inverseScore(minimum float64, value float64) float64 {
	if minimum <= 0 || value <= 0 {
		return 0
	}
	return minimum / value
}

func cappedRatio(numerator float64, denominator float64) float64 {
	if denominator <= 0 {
		return 0
	}
	score := numerator / denominator
	if score > 1 {
		return 1
	}
	return score
}

func round6(value float64) float64 {
	return math.Round(value*1_000_000) / 1_000_000
}

func roundMetrics(metrics map[string]float64) map[string]float64 {
	rounded := map[string]float64{}
	for key, value := range metrics {
		rounded[key] = round6(value)
	}
	return rounded
}

func copyStringMap(input map[string]string) map[string]string {
	output := map[string]string{}
	for key, value := range input {
		output[key] = value
	}
	return output
}

func maxInt(left int, right int) int {
	if left > right {
		return left
	}
	return right
}

func minInt(left int, right int) int {
	if left < right {
		return left
	}
	return right
}

func trimFloat(value float64) string {
	if math.Mod(value, 1) == 0 {
		return fmt.Sprintf("%.0f", value)
	}
	return fmt.Sprintf("%.2f", value)
}
