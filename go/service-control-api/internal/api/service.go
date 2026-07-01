package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os/exec"
	"path/filepath"
	"sort"
)

const deploymentDryRunCommand = "kubectl apply -f - --dry-run=server"

type Service struct {
	config ServerConfig
}

func NewService(config ServerConfig) Service {
	return Service{config: config}
}

func (service Service) ListAgents() (map[string]any, error) {
	return service.ListAgentsFromPath(service.config.path("config", "agent_registry.json"))
}

func (service Service) ListAgentsFromPath(path string) (map[string]any, error) {
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

func (service Service) ShowAgent(agentName string) (AgentProfile, error) {
	return service.ShowAgentFromPath(service.config.path("config", "agent_registry.json"), agentName)
}

func (service Service) ShowAgentFromPath(path string, agentName string) (AgentProfile, error) {
	registry, err := loadAgentRegistry(path)
	if err != nil {
		return AgentProfile{}, err
	}
	return findAgent(registry.Agents, agentName)
}

func (service Service) ValidateAgentAction(agentName string, action string) (bool, error) {
	return service.ValidateAgentActionFromPath(service.config.path("config", "agent_registry.json"), agentName, action)
}

func (service Service) ValidateAgentActionFromPath(path string, agentName string, action string) (bool, error) {
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

func (service Service) SelectOpsLLM(policyName string) (OpsLLMSelectionResponse, error) {
	return service.SelectOpsLLMFromPath(service.config.path("config", "ops_llm_benchmark.json"), policyName)
}

func (service Service) SelectOpsLLMFromPath(path string, policyName string) (OpsLLMSelectionResponse, error) {
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

func (service Service) RecommendPlacement(workloadID string) (PlacementResponse, error) {
	return service.RecommendPlacementFromPath(service.config.path("config", "inference_optimization.json"), workloadID)
}

func (service Service) RecommendPlacementFromPath(path string, workloadID string) (PlacementResponse, error) {
	_, workload, candidates, rejected, err := service.rankPlacementFromPath(path, workloadID)
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

func (service Service) BuildDeploymentPlan(workloadID string) (DeploymentPlanResponse, error) {
	return service.BuildDeploymentPlanFromPath(service.config.path("config", "inference_optimization.json"), workloadID)
}

func (service Service) BuildDeploymentPlanFromPath(path string, workloadID string) (DeploymentPlanResponse, error) {
	placement, err := service.RecommendPlacementFromPath(path, workloadID)
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

	limits := copyStringMap(resource.ResourceLimits)
	if resource.Accelerator == "gpu" {
		if _, ok := limits["nvidia.com/gpu"]; !ok {
			limits["nvidia.com/gpu"] = "1"
		}
	}
	if resource.Accelerator == "npu" {
		if _, ok := limits["aiops.dev/npu"]; !ok {
			limits["aiops.dev/npu"] = "1"
		}
	}
	if workload.EstimatedVRAMGB > 0 && resource.Accelerator != "cpu" {
		limits["aiops.dev/vram-gb"] = trimFloat(workload.EstimatedVRAMGB)
	}
	nodeSelector := copyStringMap(resource.NodeSelector)
	if len(nodeSelector) == 0 {
		nodeSelector["aiops.resource/accelerator"] = resource.Accelerator
	}

	plan := DeploymentPlan{
		ServiceName:       workload.ServiceName,
		ContainerImage:    workload.ContainerImage,
		TargetResource:    resource.ID,
		TargetAccelerator: resource.Accelerator,
		Kubernetes: KubernetesPlan{
			Namespace:    workload.Namespace,
			Deployment:   workload.ServiceName,
			Replicas:     workload.Replicas,
			NodeSelector: nodeSelector,
			Resources: ResourceSpec{
				Requests: map[string]string{
					"cpu":    fmt.Sprintf("%d", maxInt(1, minInt(resource.CPUCores, 8))),
					"memory": fmt.Sprintf("%dGi", maxInt(1, minInt(resource.MemoryGB, 32))),
				},
				Limits: limits,
			},
		},
		ControlActions: []string{
			placement.Action,
			"scale_replicas",
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

func (service Service) RunServiceOperations(request ServiceOperationsRequest) (ServiceOperationsResponse, error) {
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

	llmSelection, err := service.SelectOpsLLMFromPath(llmConfigPath, request.LLMPolicy)
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	deploymentPlan, err := service.BuildDeploymentPlanFromPath(inferenceConfigPath, request.Workload)
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	manifest, err := renderDeploymentManifest(deploymentPlan.DeploymentPlan)
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	recoveryNamespace, recoveryDeployment := normalizeRecoveryContext(request)
	dryRun := validateDeploymentManifest(manifest, request.Mode)
	reviews := buildAgentReviews(deploymentPlan)
	recovery := RecoveryReadiness{
		Valid:      recoveryNamespace != "" && recoveryDeployment != "",
		Skipped:    true,
		Namespace:  recoveryNamespace,
		Deployment: recoveryDeployment,
		Reason:     "no alert supplied; recovery context is validated for readiness only",
	}
	guardValidation := buildGuardValidation(request, recoveryNamespace, recoveryDeployment)
	ready := dryRun.Valid &&
		reviews.Application.Approved &&
		reviews.Infrastructure.Approved &&
		reviews.Cost.Approved &&
		recovery.Valid &&
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
		DeploymentManifest:      manifest,
		DeploymentDryRun:        dryRun,
		AgentReviews:            reviews,
		Recovery:                recovery,
		RecoveryPipelineReady:   ready,
		GuardBackend:            request.GuardBackend,
		GuardValidation:         guardValidation,
		Metadata: map[string]string{
			"llm_policy":            request.LLMPolicy,
			"workload":              request.Workload,
			"mode":                  request.Mode,
			"recovery_namespace":    recoveryNamespace,
			"recovery_deployment":   recoveryDeployment,
			"selected_actual_model": llmSelection.SelectedActualModel,
			"selected_provider":     llmSelection.SelectedProvider,
			"evaluation_source":     llmSelection.EvaluationSource,
			"evaluation_type":       llmSelection.EvaluationType,
			"benchmark_status":      llmSelection.BenchmarkStatus,
		},
	}, nil
}

func normalizeRecoveryContext(request ServiceOperationsRequest) (string, string) {
	namespace := request.RecoveryNamespace
	if namespace == "" {
		namespace = request.Namespace
	}
	deployment := request.RecoveryDeployment
	if deployment == "" {
		deployment = request.Deployment
	}
	return namespace, deployment
}

func buildGuardValidation(request ServiceOperationsRequest, namespace string, deployment string) GuardValidation {
	result := GuardValidation{
		Backend:            request.GuardBackend,
		RuntimeWired:       false,
		Mode:               request.Mode,
		Boundary:           "standalone aiops-guard bounded-action contract",
		RecoveryNamespace:  namespace,
		RecoveryDeployment: deployment,
		CheckedActions: []string{
			"get_status",
			"restart_deployment",
			"scale_out",
			"scale_in",
		},
	}
	if request.GuardBackend != "go" {
		result.Valid = false
		result.Reason = "only the Go guard backend is supported in the prototype validation path"
		return result
	}
	if namespace == "" || deployment == "" {
		result.Valid = false
		result.Reason = "recovery_namespace and recovery_deployment are required to prepare bounded guard validation"
		return result
	}
	result.Valid = true
	result.Reason = "service-control-api prepared the bounded recovery context; full runtime invocation of aiops-guard remains a planned integration step"
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

func (service Service) rankPlacementFromPath(path string, workloadID string) (InferenceConfig, InferenceWorkload, []PlacementCandidate, map[string]string, error) {
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
		if resource.AvailableReplicas > maxCapacity {
			maxCapacity = resource.AvailableReplicas
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
		capacityScore := float64(resource.AvailableReplicas) / float64(maxCapacity)
		score := config.Weights["latency"]*latencyScore +
			config.Weights["throughput"]*throughputScore +
			config.Weights["cost"]*costScore +
			config.Weights["capacity"]*capacityScore
		candidates = append(candidates, PlacementCandidate{
			Resource:          resource.ID,
			Accelerator:       resource.Accelerator,
			Score:             round6(score),
			LatencyMS:         resource.ExpectedLatencyMS,
			ThroughputRPS:     resource.ExpectedThroughputRPS,
			CostPerHour:       resource.CostPerHour,
			AvailableReplicas: resource.AvailableReplicas,
			Action:            actionForResource(resource),
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
	if resource.AvailableReplicas <= 0 {
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

func renderDeploymentManifest(plan DeploymentPlan) (DeploymentManifest, error) {
	deployment := plan.Kubernetes.Deployment
	namespace := plan.Kubernetes.Namespace
	image := plan.ContainerImage
	if deployment == "" || namespace == "" || image == "" {
		return DeploymentManifest{}, fmt.Errorf("deployment plan must include deployment, namespace, and image")
	}
	labels := map[string]string{"app": deployment}
	return DeploymentManifest{
		APIVersion: "apps/v1",
		Kind:       "Deployment",
		Metadata: map[string]any{
			"name":      deployment,
			"namespace": namespace,
			"labels":    labels,
		},
		Spec: map[string]any{
			"replicas": plan.Kubernetes.Replicas,
			"selector": map[string]any{
				"matchLabels": labels,
			},
			"template": map[string]any{
				"metadata": map[string]any{
					"labels": labels,
				},
				"spec": map[string]any{
					"nodeSelector": plan.Kubernetes.NodeSelector,
					"containers": []map[string]any{
						{
							"name":      deployment,
							"image":     image,
							"resources": plan.Kubernetes.Resources,
						},
					},
				},
			},
		},
	}, nil
}

func validateDeploymentManifest(manifest DeploymentManifest, mode string) DeploymentDryRun {
	if mode == "" {
		mode = "mock"
	}
	if mode == "mock" {
		return DeploymentDryRun{
			Command: deploymentDryRunCommand,
			Mode:    mode,
			Valid:   true,
			Stdout:  "mock: deployment manifest generated and not applied",
			Stderr:  "",
		}
	}
	payload, err := marshalJSON(manifest)
	if err != nil {
		return DeploymentDryRun{
			Command: deploymentDryRunCommand,
			Mode:    mode,
			Valid:   false,
			Stdout:  "",
			Stderr:  err.Error(),
		}
	}
	command := exec.Command("kubectl", "apply", "-f", "-", "--dry-run=server")
	command.Stdin = bytes.NewReader(payload)
	output, err := command.CombinedOutput()
	return DeploymentDryRun{
		Command: deploymentDryRunCommand,
		Mode:    mode,
		Valid:   err == nil,
		Stdout:  string(bytes.TrimSpace(output)),
		Stderr:  errorString(err),
	}
}

func buildAgentReviews(plan DeploymentPlanResponse) AgentReviews {
	return AgentReviews{
		Application: AgentReview{
			Agent:    "AIApplicationManagementAgent",
			Action:   "app_plan_deployment",
			Reward:   0.8,
			Approved: plan.Valid,
			Reason:   "AI application deployment plan is ready for Kubernetes manifest generation and dry-run validation.",
			Parameters: map[string]string{
				"workload":          plan.Workload,
				"namespace":         plan.DeploymentPlan.Kubernetes.Namespace,
				"deployment":        plan.DeploymentPlan.Kubernetes.Deployment,
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

func errorString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func marshalJSON(value any) ([]byte, error) {
	return json.Marshal(value)
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
