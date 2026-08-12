package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"math"
	"path/filepath"
	"sort"
	"strings"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/automation"
	"kyunghee-aiops/service-control-api/internal/autonomy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type Service struct {
	config             ServerConfig
	runtimeAgents      *runtimeAgentStore
	automationFeedback *automationFeedbackStore
	autonomyManager    *autonomy.Manager
	controlRuns        *controlrun.Store
	agentDispatcher    *agentDispatcher
	agentControl       *agentcontrol.Service
	operationRuntime   agentcontrol.OperationOptimizationRuntime
	automationRunner   *agentcontrol.AutomationRunner
}

func NewService(config ServerConfig) Service {
	reasoner := newAgentControlReasoner(config, llmclient.NewClient(nil))
	authorizer := newAgentControlRegistryAuthorizer(config)
	runtimeAgents := newRuntimeAgentStore()
	internalExecutors := map[string]agentExecutor{}
	if registry, err := loadAgentRegistry(config.path("config", "agent_registry.json")); err == nil {
		if defaultAgent := strings.TrimSpace(registry.Defaults[agentcontrol.AutomationCapability]); defaultAgent != "" {
			internalExecutors[defaultAgent] = newInternalDecisionAgentExecutor()
		}
		if defaultAgent := strings.TrimSpace(registry.Defaults[agentcontrol.OperationOptimizationCapability]); defaultAgent != "" {
			internalExecutors[defaultAgent] = newInternalOperationOptimizationExecutor()
		}
	}
	dispatcher := newAgentDispatcher(
		internalExecutors,
		newHTTPAgentExecutor(
			newAgentHTTPClient(config.AgentExecutionTimeout),
			config.AgentExecutionTimeout,
		),
	)
	decisionRuntime := newDecisionAgentRuntime(config, runtimeAgents, dispatcher)
	operationRuntime := newOperationOptimizationRuntime(config, runtimeAgents, dispatcher)
	agentControlService := agentcontrol.NewServiceWithRuntimes(
		reasoner,
		authorizer,
		decisionRuntime,
		operationRuntime,
	)
	resourceCatalog, _ := agentcontrol.LoadResourceCatalog(config.ResourceCatalogPath)
	deploymentAdapter, _ := agentcontrol.NewDeploymentAdapter(config.DeploymentAdapterMode)
	service := Service{
		config:             config,
		runtimeAgents:      runtimeAgents,
		automationFeedback: newAutomationFeedbackStore(),
		controlRuns:        controlrun.NewStore(),
		agentDispatcher:    dispatcher,
		agentControl:       agentControlService,
		operationRuntime:   operationRuntime,
		automationRunner: agentcontrol.NewAutomationRunnerWithAdapter(
			newAgentControlRequirementAnalyzer(config, llmclient.NewClient(nil)),
			agentcontrol.CatalogResourceRecommender{Catalog: resourceCatalog},
			agentControlService,
			deploymentAdapter,
		),
	}
	var control autonomy.AppDeployControl
	if strings.TrimSpace(config.AppDeployBaseURL) != "" {
		client, err := appdeploy.NewClient(config.AppDeployBaseURL, nil)
		if err == nil {
			control = client
		}
	}
	planner := newAutonomyDecisionPlanner(config, automation.NewPlanner(llmclient.NewClient(nil)))
	service.autonomyManager = autonomy.NewManager(control, planner, newAutonomyActionAuthorizer(config))
	return service
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
	runtimeAgents := service.runtimeAgents.list()
	eligibleDecision := eligibleDecisionAgents(registry, runtimeAgents)
	eligibleOperation := eligibleOperationAgents(registry, runtimeAgents)
	registry.Agents = append(registry.Agents, runtimeAgents...)
	sort.SliceStable(registry.Agents, func(i, j int) bool {
		return registry.Agents[i].Name < registry.Agents[j].Name
	})
	return map[string]any{
		"command":                   "list-agents",
		"registry":                  path,
		"version":                   registry.Version,
		"defaults":                  registry.Defaults,
		"persistence":               "configuration_and_process_memory",
		"runtime_agent_count":       service.runtimeAgents.count(),
		"agents":                    registry.Agents,
		"eligible_decision_agents":  eligibleDecision,
		"eligible_operation_agents": eligibleOperation,
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

func (service Service) ValidateVMSuitability(ctx context.Context, request VMCompatibilityRequest) (VMCompatibilityResponse, error) {
	return service.ValidateVMSuitabilityFromPath(
		ctx,
		service.config.path("config", "vm_workload_requirements.json"),
		request,
	)
}

func (service Service) ValidateVMSuitabilityFromPath(ctx context.Context, path string, request VMCompatibilityRequest) (VMCompatibilityResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return VMCompatibilityResponse{}, err
	}
	config, err := loadJSON[VMRequirementsConfig](path)
	if err != nil {
		return VMCompatibilityResponse{}, err
	}
	workload, ok := findVMWorkload(config.Workloads, request.Workload)
	if !ok {
		return VMCompatibilityResponse{}, fmt.Errorf("unknown workload: %s", request.Workload)
	}
	if strings.TrimSpace(request.TargetVM.ID) == "" {
		return VMCompatibilityResponse{}, fmt.Errorf("target VM id is required")
	}

	resourceChecks := buildVMCompatibilityChecks(workload, request.TargetVM)
	resourceChecksPassed := true
	for _, check := range resourceChecks {
		if check.Status == "fail" {
			resourceChecksPassed = false
			break
		}
	}

	performanceStatus := strings.TrimSpace(request.TargetVM.Performance.Status)
	if performanceStatus == "" {
		performanceStatus = "not_measured"
	}
	performanceChecks := buildVMPerformanceChecks(workload, request.TargetVM, performanceStatus)
	performanceChecksPassed := true
	for _, check := range performanceChecks {
		if check.Status == "fail" {
			performanceChecksPassed = false
			break
		}
	}
	checks := append(resourceChecks, performanceChecks...)

	compatibilityStatus := "incompatible"
	action := "manual_review_required"
	reason := "the provided VM does not satisfy the declared workload requirements"
	if resourceChecksPassed {
		if performanceStatus != "measured" {
			compatibilityStatus = "provisionally_compatible"
			action = "benchmark_then_prepare_control_plan"
			reason = "resource checks passed, but workload performance remains unmeasured"
		} else if performanceChecksPassed {
			compatibilityStatus = "compatible"
			action = "prepare_registered_agent_control_plan"
			reason = "the provided VM satisfies the declared resource and measured performance requirements"
		} else {
			reason = "the provided VM hardware is suitable, but measured performance does not satisfy the declared workload requirements"
		}
	}

	validationMode := config.ValidationMode
	if validationMode == "" {
		validationMode = "actual_vm_compatibility"
	}
	return VMCompatibilityResponse{
		Valid:                true,
		ValidationMode:       validationMode,
		Workload:             workload.ID,
		TargetVMID:           request.TargetVM.ID,
		ResourceSource:       request.TargetVM.Source,
		EvidenceStatus:       request.TargetVM.EvidenceStatus,
		CompatibilityStatus:  compatibilityStatus,
		ResourceChecksPassed: resourceChecksPassed,
		PerformanceStatus:    performanceStatus,
		Action:               action,
		Reason:               reason,
		Checks:               checks,
	}, nil
}

func buildVMCompatibilityChecks(workload VMWorkloadRequirement, target VMResourceSnapshot) []VMCompatibilityCheck {
	checks := []VMCompatibilityCheck{}
	add := func(name string, passed bool, actual string, required string, passReason string, failReason string) {
		status := "pass"
		reason := passReason
		if !passed {
			status = "fail"
			reason = failReason
		}
		checks = append(checks, VMCompatibilityCheck{
			Name:     name,
			Status:   status,
			Actual:   actual,
			Required: required,
			Reason:   reason,
		})
	}

	add(
		"evidence_status",
		strings.EqualFold(target.EvidenceStatus, "collected"),
		target.EvidenceStatus,
		"collected",
		"target VM evidence was collected",
		"target VM evidence has not been collected",
	)
	if workload.RequiredAccelerator != "" {
		add(
			"accelerator",
			strings.EqualFold(target.Accelerator, workload.RequiredAccelerator),
			target.Accelerator,
			workload.RequiredAccelerator,
			"target VM accelerator matches the workload requirement",
			"target VM accelerator does not match the workload requirement",
		)
	}
	if workload.MinimumCPUCores > 0 {
		add(
			"cpu_cores",
			target.CPUCores >= workload.MinimumCPUCores,
			fmt.Sprintf("%d", target.CPUCores),
			fmt.Sprintf("%d", workload.MinimumCPUCores),
			"target VM CPU capacity satisfies the declared minimum",
			"target VM CPU capacity is below the declared minimum or was not collected",
		)
	}
	if workload.MinimumMemoryGB > 0 {
		add(
			"memory_gb",
			target.MemoryGB >= workload.MinimumMemoryGB,
			trimFloat(target.MemoryGB),
			trimFloat(workload.MinimumMemoryGB),
			"target VM memory satisfies the declared minimum",
			"target VM memory is below the declared minimum or was not collected",
		)
	}
	if workload.MinimumGPUMemoryGB > 0 {
		actualGB := float64(target.GPUMemoryMiB) / 1024
		add(
			"gpu_memory_gb",
			actualGB >= workload.MinimumGPUMemoryGB,
			trimFloat(actualGB),
			trimFloat(workload.MinimumGPUMemoryGB),
			"target VM GPU memory satisfies the declared minimum",
			"target VM GPU memory is below the declared minimum or was not collected",
		)
	}
	return checks
}

func buildVMPerformanceChecks(workload VMWorkloadRequirement, target VMResourceSnapshot, performanceStatus string) []VMCompatibilityCheck {
	checks := []VMCompatibilityCheck{}
	if workload.LatencySLOMS != nil {
		check := VMCompatibilityCheck{
			Name:     "latency_slo_ms",
			Required: trimFloat(*workload.LatencySLOMS),
		}
		switch {
		case performanceStatus != "measured":
			check.Status = "not_measured"
			check.Reason = "latency evidence has not been measured"
		case target.Performance.LatencyMS == nil:
			check.Status = "fail"
			check.Reason = "performance is marked measured, but latency evidence is missing"
		default:
			check.Actual = trimFloat(*target.Performance.LatencyMS)
			if *target.Performance.LatencyMS <= *workload.LatencySLOMS {
				check.Status = "pass"
				check.Reason = "measured latency satisfies the declared SLO"
			} else {
				check.Status = "fail"
				check.Reason = "measured latency exceeds the declared SLO"
			}
		}
		checks = append(checks, check)
	}
	if workload.MinimumThroughputRPS != nil {
		check := VMCompatibilityCheck{
			Name:     "minimum_throughput_rps",
			Required: trimFloat(*workload.MinimumThroughputRPS),
		}
		switch {
		case performanceStatus != "measured":
			check.Status = "not_measured"
			check.Reason = "throughput evidence has not been measured"
		case target.Performance.ThroughputRPS == nil:
			check.Status = "fail"
			check.Reason = "performance is marked measured, but throughput evidence is missing"
		default:
			check.Actual = trimFloat(*target.Performance.ThroughputRPS)
			if *target.Performance.ThroughputRPS >= *workload.MinimumThroughputRPS {
				check.Status = "pass"
				check.Reason = "measured throughput satisfies the declared minimum"
			} else {
				check.Status = "fail"
				check.Reason = "measured throughput is below the declared minimum"
			}
		}
		checks = append(checks, check)
	}
	return checks
}

func (service Service) BuildDeploymentPlan(ctx context.Context, request VMCompatibilityRequest) (DeploymentPlanResponse, error) {
	return service.BuildDeploymentPlanFromPath(
		ctx,
		service.config.path("config", "vm_workload_requirements.json"),
		request,
	)
}

func (service Service) BuildDeploymentPlanFromPath(ctx context.Context, path string, request VMCompatibilityRequest) (DeploymentPlanResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return DeploymentPlanResponse{}, err
	}
	compatibility, err := service.ValidateVMSuitabilityFromPath(ctx, path, request)
	if err != nil {
		return DeploymentPlanResponse{}, err
	}
	if !compatibility.ResourceChecksPassed {
		return DeploymentPlanResponse{VMCompatibilityResponse: compatibility}, nil
	}
	config, err := loadJSON[VMRequirementsConfig](path)
	if err != nil {
		return DeploymentPlanResponse{}, err
	}
	workload, ok := findVMWorkload(config.Workloads, request.Workload)
	if !ok {
		return DeploymentPlanResponse{}, fmt.Errorf("unknown workload: %s", request.Workload)
	}

	preconditions := []string{"target_vm_evidence_collected"}
	if compatibility.PerformanceStatus != "measured" {
		preconditions = append(preconditions, "measure_latency_throughput_and_cost")
	}
	requiredCapability := "ai_application_deployment_control"
	requestedAction := "deploy_application"
	selectedExecutor := service.selectExecutionAgent(requiredCapability, requestedAction)
	if selectedExecutor == "" {
		preconditions = append(preconditions, "register_executor_agent")
	} else {
		preconditions = append(preconditions, "registered_executor_agent_validated")
	}
	return DeploymentPlanResponse{
		VMCompatibilityResponse: compatibility,
		DeploymentPlan: DeploymentPlan{
			Workload:           workload.ID,
			ServiceName:        workload.ServiceName,
			TargetVMID:         request.TargetVM.ID,
			TargetAccelerator:  request.TargetVM.Accelerator,
			ExecutorType:       "registered_external_agent",
			RequiredCapability: requiredCapability,
			SelectedExecutor:   selectedExecutor,
			RequestedAction:    requestedAction,
			AllowedActions:     append([]string(nil), workload.AllowedControlActions...),
			Preconditions:      preconditions,
			ExecutionStatus:    "not_executed",
			FeedbackRequired:   true,
		},
	}, nil
}

func (service Service) selectExecutionAgent(capability string, action string) string {
	for _, agent := range service.runtimeAgents.list() {
		if agent.Enabled && contains(agent.Capabilities, capability) && contains(agent.BoundedActions, action) {
			return agent.Name
		}
	}
	return ""
}

func (service Service) PlanLLMAutomationAction(ctx context.Context, request LLMAutomationActionRequest) (LLMAutomationActionResponse, error) {
	return service.PlanLLMAutomationActionFromPaths(
		ctx,
		service.config.path("config", "vm_workload_requirements.json"),
		service.config.LLMCandidatesPath,
		request,
	)
}

func (service Service) PlanLLMAutomationActionFromPaths(
	ctx context.Context,
	requirementsPath string,
	candidatesPath string,
	request LLMAutomationActionRequest,
) (LLMAutomationActionResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return LLMAutomationActionResponse{}, err
	}
	if err := service.validateOperationalControlRun(request.RunID); err != nil {
		return LLMAutomationActionResponse{RunID: request.RunID, Status: "run_rejected"}, err
	}
	compatibility, err := service.ValidateVMSuitabilityFromPath(ctx, requirementsPath, VMCompatibilityRequest{
		Workload: request.Workload,
		TargetVM: request.TargetVM,
	})
	if err != nil {
		return LLMAutomationActionResponse{}, err
	}
	result := LLMAutomationActionResponse{
		Status:          "not_executed",
		RunID:           request.RunID,
		VMCompatibility: compatibility,
		Decision: LLMDecisionResult{
			DecisionExecutionStatus: "not_executed",
			CandidateID:             request.CandidateID,
		},
		Guard: GuardDecision{
			Status: "not_evaluated",
			Reason: "LLM Action proposal has not been evaluated",
		},
	}
	if !compatibility.ResourceChecksPassed || compatibility.CompatibilityStatus == "incompatible" {
		result.Status = "vm_incompatible"
		result.Guard.Status = "rejected"
		result.Guard.Reason = "actual VM compatibility checks failed before the LLM decision call"
		return result, nil
	}

	requirements, err := loadJSON[VMRequirementsConfig](requirementsPath)
	if err != nil {
		return result, err
	}
	workload, ok := findVMWorkload(requirements.Workloads, request.Workload)
	if !ok {
		return result, fmt.Errorf("unknown workload: %s", request.Workload)
	}
	candidateConfig, err := llmclient.LoadCandidateConfig(candidatesPath)
	if err != nil {
		return result, err
	}
	candidate, err := llmclient.FindEnabledCandidate(candidateConfig, request.CandidateID)
	if err != nil {
		return result, err
	}

	planner := automation.NewPlanner(llmclient.NewClient(nil))
	decision, planErr := planner.Plan(ctx, candidate, automation.DecisionContext{
		Workload:            workload.ID,
		ServiceName:         workload.ServiceName,
		TargetVMID:          request.TargetVM.ID,
		CompatibilityStatus: compatibility.CompatibilityStatus,
		Checks:              automationChecks(compatibility.Checks),
		Observations:        request.Observations,
		AllowedActions:      append([]string(nil), workload.AllowedControlActions...),
		RequiredCapability:  "ai_application_deployment_control",
	})
	result.Decision = mapLLMDecision(decision)
	if planErr != nil {
		result.Status = decision.ExecutionStatus
		result.Guard.Status = "rejected"
		result.Guard.Reason = "the LLM decision did not produce a valid bounded Action proposal"
		return result, planErr
	}

	proposal := decision.Proposal
	automationAgent, err := service.ShowAgent(ctx, "AIApplicationAutomationAgent")
	if err != nil {
		return result, fmt.Errorf("load automation agent policy: %w", err)
	}
	guard := validateLLMActionProposal(workload, request.TargetVM.ID, automationAgent, proposal)
	result.Guard = guard
	if !guard.Valid {
		result.Status = "rejected"
		return result, nil
	}
	selectedExecutor := service.selectExecutionAgent(proposal.RequiredCapability, proposal.Action)
	if selectedExecutor == "" {
		result.Status = "pending_executor"
		return result, nil
	}
	parameters := copyAnyMap(proposal.Parameters)
	parameters["workload"] = workload.ID
	parameters["target_vm_id"] = request.TargetVM.ID
	handoff, err := service.BuildAgentInvocationPlan(ctx, selectedExecutor, AgentInvocationPlanRequest{
		Capability: proposal.RequiredCapability,
		Action:     proposal.Action,
		Parameters: parameters,
	})
	if err != nil {
		return result, err
	}
	correlationID, err := newCorrelationID()
	if err != nil {
		return result, err
	}
	result.Valid = true
	result.Status = "approved"
	result.CorrelationID = correlationID
	result.Handoff = handoff
	service.automationFeedback.register(correlationID, selectedExecutor, request.RunID)
	if strings.TrimSpace(request.RunID) != "" {
		if _, err := service.controlRuns.Update(request.RunID, func(run *controlrun.Run) error {
			run.CorrelationIDs = append(run.CorrelationIDs, correlationID)
			run.Stages = append(run.Stages, completedControlRunStage(
				"action_proposal",
				"approved",
				"Qwen Action proposal passed Agent Registry and Go Guard validation",
				map[string]any{"correlation_id": correlationID, "action": proposal.Action},
			))
			return nil
		}); err != nil {
			return result, err
		}
	}
	return result, nil
}

func (service Service) validateOperationalControlRun(runID string) error {
	runID = strings.TrimSpace(runID)
	if runID == "" {
		return nil
	}
	run, ok := service.controlRuns.Get(runID)
	if !ok {
		return fmt.Errorf("ControlRun was not found: %s", runID)
	}
	if run.Status != controlrun.StatusDeployed || run.Deployment == nil || strings.TrimSpace(run.Deployment.DeploymentID) == "" {
		return fmt.Errorf("ControlRun is not linked to a deployed application: %s", runID)
	}
	return nil
}

func automationChecks(checks []VMCompatibilityCheck) []map[string]string {
	items := make([]map[string]string, 0, len(checks))
	for _, check := range checks {
		items = append(items, map[string]string{
			"name":     check.Name,
			"status":   check.Status,
			"actual":   check.Actual,
			"required": check.Required,
			"reason":   check.Reason,
		})
	}
	return items
}

func mapLLMDecision(decision automation.DecisionResult) LLMDecisionResult {
	return LLMDecisionResult{
		DecisionExecutionStatus: decision.ExecutionStatus,
		CandidateID:             decision.CandidateID,
		Provider:                decision.Provider,
		ActualModel:             decision.ActualModel,
		LatencyMS:               decision.LatencyMS,
		Proposal: LLMActionProposal{
			Action:             decision.Proposal.Action,
			Reason:             decision.Proposal.Reason,
			Confidence:         decision.Proposal.Confidence,
			RequiredCapability: decision.Proposal.RequiredCapability,
			TargetVMID:         decision.Proposal.TargetVMID,
			Parameters:         copyAnyMap(decision.Proposal.Parameters),
		},
	}
}

func validateLLMActionProposal(
	workload VMWorkloadRequirement,
	targetVMID string,
	automationAgent AgentProfile,
	proposal automation.ActionProposal,
) GuardDecision {
	result := GuardDecision{Status: "rejected"}
	if proposal.TargetVMID != targetVMID {
		result.Reason = "LLM proposal target VM differs from the validated VM"
		return result
	}
	if proposal.RequiredCapability != "ai_application_deployment_control" {
		result.Reason = "LLM proposal requested an unsupported capability"
		return result
	}
	if !automationAgent.Enabled || !contains(automationAgent.Capabilities, proposal.RequiredCapability) {
		result.Reason = "LLM proposal capability is not enabled in the Agent Registry"
		return result
	}
	if !contains(automationAgent.BoundedActions, proposal.Action) {
		result.Reason = "LLM proposal Action is outside the Agent Registry bounded Action list"
		return result
	}
	if !contains(workload.AllowedControlActions, proposal.Action) {
		result.Reason = "LLM proposal Action is outside the workload bounded Action list"
		return result
	}
	result.Valid = true
	result.Status = "approved"
	result.Reason = "LLM proposal matches the validated VM, required capability, and bounded Action policy"
	return result
}

func copyAnyMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source)+2)
	for key, value := range source {
		result[key] = value
	}
	return result
}

func newCorrelationID() (string, error) {
	bytes := make([]byte, 12)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate correlation id: %w", err)
	}
	return "automation-" + hex.EncodeToString(bytes), nil
}

func (service Service) RunServiceOperations(ctx context.Context, request ServiceOperationsRequest) (ServiceOperationsResponse, error) {
	if err := ensureContext(ctx); err != nil {
		return ServiceOperationsResponse{}, err
	}
	if request.LLMPolicy == "" {
		request.LLMPolicy = "quality_first"
	}
	if request.Mode == "" {
		request.Mode = "plan_only"
	}
	if request.GuardBackend == "" {
		request.GuardBackend = "go"
	}
	llmConfigPath := service.resolvePath(request.LLMConfigPath, "config", "ops_llm_benchmark.json")
	vmRequirementsPath := service.resolvePath(request.VMRequirements, "config", "vm_workload_requirements.json")

	llmSelection, err := service.SelectOpsLLMFromPath(ctx, llmConfigPath, request.LLMPolicy)
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	deploymentPlan, err := service.BuildDeploymentPlanFromPath(ctx, vmRequirementsPath, VMCompatibilityRequest{
		Workload: request.Workload,
		TargetVM: request.TargetVM,
	})
	if err != nil {
		return ServiceOperationsResponse{}, err
	}
	llmAutomationAction := LLMAutomationActionResponse{
		Status: "not_executed",
		Decision: LLMDecisionResult{
			DecisionExecutionStatus: "not_executed",
		},
		Guard: GuardDecision{
			Status: "not_evaluated",
			Reason: "llm_candidate_id was not provided",
		},
	}
	if request.LLMCandidateID != "" {
		llmCandidatesPath := service.resolvePath(request.LLMCandidatesPath, "config", "ops_llm_eval_candidates.json")
		llmAutomationAction, err = service.PlanLLMAutomationActionFromPaths(ctx, vmRequirementsPath, llmCandidatesPath, LLMAutomationActionRequest{
			Workload:     request.Workload,
			TargetVM:     request.TargetVM,
			CandidateID:  request.LLMCandidateID,
			Observations: request.Observations,
		})
		if err != nil {
			return ServiceOperationsResponse{}, err
		}
	}
	operationService, operationResource := normalizeOperationContext(request, deploymentPlan)
	deploymentValidation := validateVMDeploymentPlan(deploymentPlan.DeploymentPlan, request.Mode)
	reviews := buildAgentReviews(deploymentPlan)
	operation := OperationReadiness{
		Valid:          false,
		Skipped:        true,
		Service:        operationService,
		TargetResource: operationResource,
		Reason:         "external agent execution is outside this prototype; only a capability-based non-executing handoff plan was generated",
	}
	guardValidation := buildGuardValidation(request, operationService, operationResource)
	planningValid := deploymentPlan.Valid &&
		deploymentPlan.ResourceChecksPassed &&
		deploymentValidation.Valid &&
		reviews.Application.Approved &&
		reviews.Infrastructure.Approved &&
		guardValidation.Valid
	if request.LLMCandidateID != "" {
		planningValid = planningValid && llmAutomationAction.Valid && llmAutomationAction.Guard.Valid
	}

	return ServiceOperationsResponse{
		Command:                 "run-service-operations",
		Valid:                   planningValid,
		SelectedLLM:             llmSelection.SelectedModel,
		SelectedActualModel:     llmSelection.SelectedActualModel,
		SelectedProvider:        llmSelection.SelectedProvider,
		EvaluationSource:        llmSelection.EvaluationSource,
		EvaluationType:          llmSelection.EvaluationType,
		BenchmarkStatus:         llmSelection.BenchmarkStatus,
		DecisionExecutionStatus: llmAutomationAction.Decision.DecisionExecutionStatus,
		LLMAutomationAction:     llmAutomationAction,
		RuntimeModel:            runtimeModelFromSelection(llmSelection),
		SelectedResource:        deploymentPlan.TargetVMID,
		DeploymentPlan:          deploymentPlan.DeploymentPlan,
		InferenceDeploymentPlan: deploymentPlan,
		DeploymentValidation:    deploymentValidation,
		DeploymentExecutionMode: request.Mode,
		AgentReviews:            reviews,
		Operation:               operation,
		OperationPipelineReady:  false,
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
			"decision_execution_status": llmAutomationAction.Decision.DecisionExecutionStatus,
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
		targetResource = plan.TargetVMID
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

func validateVMDeploymentPlan(plan DeploymentPlan, mode string) DeploymentValidation {
	if mode == "" {
		mode = "plan_only"
	}
	checks := []string{
		"workload_present",
		"service_name_present",
		"actual_target_vm_present",
		"registered_external_agent_executor_type",
		"required_capability_present",
		"execution_status_not_executed",
	}
	valid := plan.Workload != "" &&
		plan.ServiceName != "" &&
		plan.TargetVMID != "" &&
		plan.ExecutorType == "registered_external_agent" &&
		plan.RequiredCapability != "" &&
		plan.ExecutionStatus == "not_executed"
	reason := "VM compatibility result was converted into a non-executing registered-agent handoff plan"
	if !valid {
		reason = "registered-agent handoff plan is missing a required field"
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
			ReviewerType: "llm_agent",
			Agent:        "AIApplicationAutomationAgent",
			Action:       "prepare_bounded_control_handoff",
			Reward:       0.8,
			Approved:     plan.Valid && plan.ResourceChecksPassed,
			Reason:       "AI application control request is prepared for capability-based external-agent handoff without executing it.",
			Parameters: map[string]string{
				"workload":          plan.Workload,
				"service":           plan.DeploymentPlan.ServiceName,
				"selected_resource": plan.TargetVMID,
			},
		},
		Infrastructure: buildInfrastructureReview(plan),
		Cost:           buildCostReview(plan),
	}
}

func buildInfrastructureReview(plan DeploymentPlanResponse) AgentReview {
	if !plan.Valid || !plan.ResourceChecksPassed {
		return AgentReview{
			ReviewerType: "deterministic_go_validator",
			Action:       "vm_suitability_rejected",
			Reward:       -1.0,
			Approved:     false,
			Reason:       plan.Reason,
			Parameters:   map[string]string{"target_vm_id": plan.TargetVMID},
		}
	}
	return AgentReview{
		ReviewerType: "deterministic_go_validator",
		Action:       "vm_suitability_validated",
		Reward:       0.7,
		Approved:     true,
		Reason:       "The provided VM satisfies the declared resource requirements; performance evidence is reported separately.",
		Parameters: map[string]string{
			"target_vm_id":       plan.TargetVMID,
			"resource_source":    plan.ResourceSource,
			"performance_status": plan.PerformanceStatus,
		},
	}
}

func buildCostReview(plan DeploymentPlanResponse) AgentReview {
	return AgentReview{
		ReviewerType: "evidence_check",
		Action:       "cost_review_not_measured",
		Reward:       0,
		Approved:     false,
		Skipped:      true,
		Reason:       "No measured VM cost evidence was supplied, so cost approval was not claimed.",
		Parameters: map[string]string{
			"target_vm_id": plan.TargetVMID,
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

func findVMWorkload(workloads []VMWorkloadRequirement, id string) (VMWorkloadRequirement, bool) {
	for _, workload := range workloads {
		if workload.ID == id {
			return workload, true
		}
	}
	return VMWorkloadRequirement{}, false
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

func trimFloat(value float64) string {
	if math.Mod(value, 1) == 0 {
		return fmt.Sprintf("%.0f", value)
	}
	return fmt.Sprintf("%.2f", value)
}
