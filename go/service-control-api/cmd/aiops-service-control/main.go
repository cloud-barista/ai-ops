package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/api"
	"kyunghee-aiops/service-control-api/internal/benchmark"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		_ = emit(map[string]any{
			"valid": false,
			"error": err.Error(),
		})
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("command is required")
	}

	serverConfig := api.NewServerConfig()
	service := api.NewService(serverConfig)
	ctx := context.Background()
	switch args[0] {
	case "list-agents":
		flags := flag.NewFlagSet("list-agents", flag.ContinueOnError)
		registry := flags.String("registry", "config/agent_registry.json", "Agent registry JSON path")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		registryPath := resolveInputPath(serverConfig, *registry)
		result, err := service.ListAgentsFromPath(ctx, registryPath)
		if err != nil {
			return err
		}
		return emitReport("list-agents", result, *saveResultDir)
	case "show-agent":
		flags := flag.NewFlagSet("show-agent", flag.ContinueOnError)
		registry := flags.String("registry", "config/agent_registry.json", "Agent registry JSON path")
		agentName := flags.String("agent", "", "Agent name")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *agentName == "" {
			return fmt.Errorf("--agent is required")
		}
		registryPath := resolveInputPath(serverConfig, *registry)
		agent, err := service.ShowAgentFromPath(ctx, registryPath, *agentName)
		if err != nil {
			return err
		}
		return emitReport("show-agent", map[string]any{
			"command":  "show-agent",
			"valid":    true,
			"registry": registryPath,
			"agent":    agent,
		}, *saveResultDir)
	case "validate-agent-action":
		flags := flag.NewFlagSet("validate-agent-action", flag.ContinueOnError)
		registry := flags.String("registry", "config/agent_registry.json", "Agent registry JSON path")
		agentName := flags.String("agent", "", "Agent name")
		action := flags.String("action", "", "Bounded action")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *agentName == "" {
			return fmt.Errorf("--agent is required")
		}
		if *action == "" {
			return fmt.Errorf("--action is required")
		}
		registryPath := resolveInputPath(serverConfig, *registry)
		valid, err := service.ValidateAgentActionFromPath(ctx, registryPath, *agentName, *action)
		if err != nil {
			return err
		}
		return emitReport("validate-agent-action", map[string]any{
			"command":  "validate-agent-action",
			"valid":    valid,
			"registry": registryPath,
			"agent":    *agentName,
			"action":   *action,
		}, *saveResultDir)
	case "select-ops-llm":
		flags := flag.NewFlagSet("select-ops-llm", flag.ContinueOnError)
		config := flags.String("config", "config/ops_llm_benchmark.json", "Ops LLM benchmark JSON path")
		policy := flags.String("policy", "quality_first", "Selection policy")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		configPath := resolveInputPath(serverConfig, *config)
		result, err := service.SelectOpsLLMFromPath(ctx, configPath, *policy)
		if err != nil {
			return err
		}
		return emitReport("select-ops-llm", withFields(result, map[string]any{
			"command": "select-ops-llm",
			"config":  configPath,
		}), *saveResultDir)
	case "validate-vm-suitability":
		flags := flag.NewFlagSet("validate-vm-suitability", flag.ContinueOnError)
		requirements := flags.String("requirements", "config/vm_workload_requirements.json", "VM workload requirements JSON path")
		snapshot := flags.String("vm-snapshot", "docs/evidence/artifacts/vm_20260707_resource_snapshot.json", "Collected VM resource snapshot JSON path")
		workload := flags.String("workload", "", "Inference workload ID")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		requirementsPath := resolveInputPath(serverConfig, *requirements)
		targetVM, err := loadVMResourceSnapshot(resolveInputPath(serverConfig, *snapshot))
		if err != nil {
			return err
		}
		result, err := service.ValidateVMSuitabilityFromPath(ctx, requirementsPath, api.VMCompatibilityRequest{
			Workload: *workload,
			TargetVM: targetVM,
		})
		if err != nil {
			return err
		}
		return emitReport("validate-vm-suitability", withFields(result, map[string]any{
			"command":      "validate-vm-suitability",
			"requirements": requirementsPath,
			"vm_snapshot":  resolveInputPath(serverConfig, *snapshot),
		}), *saveResultDir)
	case "plan-ai-application-control":
		flags := flag.NewFlagSet("plan-ai-application-control", flag.ContinueOnError)
		requirements := flags.String("requirements", "config/vm_workload_requirements.json", "VM workload requirements JSON path")
		snapshot := flags.String("vm-snapshot", "docs/evidence/artifacts/vm_20260707_resource_snapshot.json", "Collected VM resource snapshot JSON path")
		workload := flags.String("workload", "", "Inference workload ID")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		requirementsPath := resolveInputPath(serverConfig, *requirements)
		targetVM, err := loadVMResourceSnapshot(resolveInputPath(serverConfig, *snapshot))
		if err != nil {
			return err
		}
		result, err := service.BuildDeploymentPlanFromPath(ctx, requirementsPath, api.VMCompatibilityRequest{
			Workload: *workload,
			TargetVM: targetVM,
		})
		if err != nil {
			return err
		}
		return emitReport("plan-ai-application-control", withFields(result, map[string]any{
			"command":      "plan-ai-application-control",
			"requirements": requirementsPath,
			"vm_snapshot":  resolveInputPath(serverConfig, *snapshot),
		}), *saveResultDir)
	case "plan-llm-automation-action":
		flags := flag.NewFlagSet("plan-llm-automation-action", flag.ContinueOnError)
		requirements := flags.String("requirements", "config/vm_workload_requirements.json", "VM workload requirements JSON path")
		snapshot := flags.String("vm-snapshot", "docs/evidence/artifacts/vm_20260707_resource_snapshot.json", "Collected VM resource snapshot JSON path")
		candidates := flags.String("candidates", "config/ops_llm_eval_candidates.json", "OpenAI-compatible LLM candidate JSON path")
		candidateID := flags.String("candidate-id", "", "Enabled LLM candidate ID")
		workload := flags.String("workload", "", "Inference workload ID")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		if *candidateID == "" {
			return fmt.Errorf("--candidate-id is required")
		}
		requirementsPath := resolveInputPath(serverConfig, *requirements)
		snapshotPath := resolveInputPath(serverConfig, *snapshot)
		candidatesPath := resolveInputPath(serverConfig, *candidates)
		targetVM, err := loadVMResourceSnapshot(snapshotPath)
		if err != nil {
			return err
		}
		result, err := service.PlanLLMAutomationActionFromPaths(ctx, requirementsPath, candidatesPath, api.LLMAutomationActionRequest{
			Workload:    *workload,
			TargetVM:    targetVM,
			CandidateID: *candidateID,
		})
		if err != nil {
			return err
		}
		return emitReport("plan-llm-automation-action", withFields(result, map[string]any{
			"command":      "plan-llm-automation-action",
			"requirements": requirementsPath,
			"vm_snapshot":  snapshotPath,
			"candidates":   candidatesPath,
		}), *saveResultDir)
	case "run-appdeploy-planner":
		flags := flag.NewFlagSet("run-appdeploy-planner", flag.ContinueOnError)
		naturalLanguageRequest := flags.String("request", "", "Natural-language application deployment requirement")
		appVersionID := flags.String("app-version-id", "", "AppDeploy application version ID")
		candidateID := flags.String("candidate-id", "", "Enabled LLM candidate ID")
		candidates := flags.String("candidates", "config/ops_llm_eval_candidates.json", "OpenAI-compatible LLM candidate JSON path")
		appDeployBaseURL := flags.String("appdeploy-base-url", "", "AppDeploy API base URL ending in /api/v1")
		targetProfileID := flags.String("target-profile-id", "", "Optional AppDeploy target profile hint")
		requestedBy := flags.String("requested-by", "ai-ops-geon-planner", "Planner requester identifier")
		pollIntervalMS := flags.Int("poll-interval-ms", 1000, "Deployment status polling interval in milliseconds")
		maxPollAttempts := flags.Int("max-poll-attempts", 60, "Maximum number of deployment status polls")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if strings.TrimSpace(*naturalLanguageRequest) == "" {
			return fmt.Errorf("--request is required")
		}
		if strings.TrimSpace(*appVersionID) == "" {
			return fmt.Errorf("--app-version-id is required")
		}
		if strings.TrimSpace(*candidateID) == "" {
			return fmt.Errorf("--candidate-id is required")
		}
		if strings.TrimSpace(*appDeployBaseURL) == "" {
			return fmt.Errorf("--appdeploy-base-url is required")
		}
		result, err := service.RunAppDeployPlannerWithConfig(ctx, api.AppDeployPlannerRequest{
			NaturalLanguageRequest: *naturalLanguageRequest,
			AppVersionID:           *appVersionID,
			CandidateID:            *candidateID,
			TargetProfileID:        *targetProfileID,
			RequestedBy:            *requestedBy,
			PollIntervalMS:         *pollIntervalMS,
			MaxPollAttempts:        *maxPollAttempts,
		}, resolveInputPath(serverConfig, *candidates), *appDeployBaseURL)
		if err != nil {
			return err
		}
		return emitReport("run-appdeploy-planner", result, *saveResultDir)
	case "run-service-operations":
		flags := flag.NewFlagSet("run-service-operations", flag.ContinueOnError)
		llmConfig := flags.String("llm-config", "config/ops_llm_benchmark.json", "Ops LLM benchmark JSON path")
		llmCandidates := flags.String("llm-candidates", "config/ops_llm_eval_candidates.json", "OpenAI-compatible LLM candidate JSON path")
		llmCandidateID := flags.String("llm-candidate-id", "", "Optional enabled LLM candidate ID for actual Action planning")
		llmPolicy := flags.String("llm-policy", "quality_first", "Ops LLM selection policy")
		vmRequirements := flags.String("vm-requirements", "config/vm_workload_requirements.json", "VM workload requirements JSON path")
		vmSnapshot := flags.String("vm-snapshot", "docs/evidence/artifacts/vm_20260707_resource_snapshot.json", "Collected VM resource snapshot JSON path")
		workload := flags.String("workload", "", "Inference workload ID")
		operationService := flags.String("operation-service", "", "AI service for bounded operation validation")
		operationResource := flags.String("operation-resource", "", "CPU/GPU VM resource for bounded operation validation")
		mode := flags.String("mode", "plan_only", "Execution mode")
		guardBackend := flags.String("guard-backend", "go", "Guard backend")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		targetVM, err := loadVMResourceSnapshot(resolveInputPath(serverConfig, *vmSnapshot))
		if err != nil {
			return err
		}
		result, err := service.RunServiceOperations(ctx, api.ServiceOperationsRequest{
			LLMConfigPath:     resolveInputPath(serverConfig, *llmConfig),
			LLMCandidatesPath: resolveInputPath(serverConfig, *llmCandidates),
			LLMCandidateID:    *llmCandidateID,
			VMRequirements:    resolveInputPath(serverConfig, *vmRequirements),
			LLMPolicy:         *llmPolicy,
			Workload:          *workload,
			TargetVM:          targetVM,
			OperationService:  *operationService,
			OperationResource: *operationResource,
			Mode:              *mode,
			GuardBackend:      *guardBackend,
		})
		if err != nil {
			return err
		}
		return emitReport("run-service-operations", result, *saveResultDir)
	case "run-ops-llm-benchmark":
		flags := flag.NewFlagSet("run-ops-llm-benchmark", flag.ContinueOnError)
		scenarios := flags.String("scenarios", "data/ops_llm_eval_scenarios.jsonl", "Ops LLM evaluation scenarios JSONL path")
		candidates := flags.String("candidates", "config/ops_llm_eval_candidates.json", "Ops LLM evaluation candidates JSON path")
		outputDir := flags.String("output-dir", "", "Directory where benchmark output JSONL is saved")
		dryRun := flags.Bool("dry-run", false, "Generate benchmark prompts and output rows without provider API calls")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		benchmarkOutputDir := *outputDir
		if benchmarkOutputDir == "" {
			benchmarkOutputDir = filepath.Join(
				serverConfig.RepoRoot,
				"runs",
				"ops-llm-evaluation-"+time.Now().Format("20060102-150405"),
			)
		}
		result, err := benchmark.RunOpsLLMBenchmark(benchmark.RunOptions{
			ScenariosPath:  resolveInputPath(serverConfig, *scenarios),
			CandidatesPath: resolveInputPath(serverConfig, *candidates),
			OutputDir:      benchmarkOutputDir,
			DryRun:         *dryRun,
		})
		if err != nil {
			return err
		}
		return emitReport("run-ops-llm-benchmark", result, *saveResultDir)
	case "evaluate-ops-llm-outputs":
		flags := flag.NewFlagSet("evaluate-ops-llm-outputs", flag.ContinueOnError)
		scenarios := flags.String("scenarios", "data/ops_llm_eval_scenarios.jsonl", "Ops LLM evaluation scenarios JSONL path")
		outputs := flags.String("outputs", "", "Model output JSONL path produced by run-ops-llm-benchmark")
		summary := flags.String("summary", "", "Evaluation summary JSON path")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *outputs == "" {
			return fmt.Errorf("--outputs is required")
		}
		summaryPath := *summary
		if summaryPath != "" && !filepath.IsAbs(summaryPath) {
			absolute, err := filepath.Abs(summaryPath)
			if err != nil {
				return err
			}
			summaryPath = absolute
		}
		result, err := benchmark.EvaluateOpsLLMOutputs(benchmark.EvaluateOptions{
			ScenariosPath: resolveInputPath(serverConfig, *scenarios),
			OutputsPath:   resolveInputPath(serverConfig, *outputs),
			SummaryPath:   summaryPath,
		})
		if err != nil {
			return err
		}
		return emitReport("evaluate-ops-llm-outputs", result, *saveResultDir)
	case "api-integration-validation":
		flags := flag.NewFlagSet("api-integration-validation", flag.ContinueOnError)
		outputDir := flags.String("output-dir", "", "Directory where API integration validation evidence is saved")
		port := flags.Int("port", 18080, "Local API server port; use 0 for an ephemeral port")
		baseURL := flags.String("base-url", "", "Optional existing service-control API base URL")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		result, err := runAPIIntegrationValidation(serverConfig, apiIntegrationValidationOptions{
			OutputDir: *outputDir,
			Port:      *port,
			BaseURL:   *baseURL,
		})
		if err != nil {
			return err
		}
		return emitReport("api-integration-validation", result, *saveResultDir)
	case "validate-system":
		flags := flag.NewFlagSet("validate-system", flag.ContinueOnError)
		target := flags.String("target", "local", "Validation target: local or vm")
		outputDir := flags.String("output-dir", "", "Directory where validation evidence is saved")
		skipGoTests := flags.Bool("skip-go-tests", false, "Skip module go test steps")
		skipTeamValidation := flags.Bool("skip-team-validation", false, "Skip team-validation step")
		runLLMBenchmark := flags.Bool("run-llm-benchmark", false, "Run Ops LLM benchmark and evaluator as part of system validation")
		llmScenarios := flags.String("llm-scenarios", "data/ops_llm_eval_scenarios.jsonl", "Ops LLM evaluation scenarios JSONL path")
		llmCandidates := flags.String("llm-candidates", "config/ops_llm_eval_candidates.json", "Ops LLM evaluation candidates JSON path")
		llmDryRun := flags.Bool("llm-dry-run", false, "Run the Ops LLM benchmark in dry-run mode")
		runLLMDecision := flags.Bool("run-llm-decision", false, "Run an actual LLM automation Action decision")
		llmDecisionCandidates := flags.String("llm-decision-candidates", "config/ops_llm_eval_candidates.json", "LLM automation decision candidates JSON path")
		llmDecisionCandidateID := flags.String("llm-decision-candidate-id", "", "Enabled candidate ID for the LLM automation decision")
		runAPIIntegration := flags.Bool("run-api-integration", false, "Run local API integration validation as part of system validation")
		apiPort := flags.Int("api-port", 18080, "Local API server port for API integration validation; use 0 for an ephemeral port")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *runLLMDecision && *llmDecisionCandidateID == "" {
			return fmt.Errorf("--llm-decision-candidate-id is required with --run-llm-decision")
		}
		result, err := runSystemValidation(ctx, service, serverConfig, systemValidationOptions{
			Target:                    *target,
			OutputDir:                 *outputDir,
			SkipGoTests:               *skipGoTests,
			SkipTeamValidation:        *skipTeamValidation,
			RunLLMBenchmark:           *runLLMBenchmark,
			LLMScenariosPath:          resolveInputPath(serverConfig, *llmScenarios),
			LLMCandidatesPath:         resolveInputPath(serverConfig, *llmCandidates),
			LLMDryRun:                 *llmDryRun,
			RunLLMDecision:            *runLLMDecision,
			LLMDecisionCandidatesPath: resolveInputPath(serverConfig, *llmDecisionCandidates),
			LLMDecisionCandidateID:    *llmDecisionCandidateID,
			RunAPIIntegration:         *runAPIIntegration,
			APIPort:                   *apiPort,
		})
		if err != nil {
			return err
		}
		return emit(result)
	case "team-validation":
		flags := flag.NewFlagSet("team-validation", flag.ContinueOnError)
		outputDir := flags.String("output-dir", "", "Directory where validation JSON reports are saved")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		result, err := runTeamValidation(ctx, service, serverConfig, *outputDir)
		if err != nil {
			return err
		}
		return emit(result)
	default:
		return fmt.Errorf("unknown command: %s", args[0])
	}
}

func addSaveResultDirFlag(flags *flag.FlagSet) *string {
	return flags.String("save-result-dir", "", "Optional directory where the final JSON report is saved")
}

func resolveInputPath(config api.ServerConfig, path string) string {
	if path == "" || filepath.IsAbs(path) {
		return path
	}
	if _, err := os.Stat(path); err == nil {
		absolute, absErr := filepath.Abs(path)
		if absErr == nil {
			return absolute
		}
		return path
	}
	return filepath.Join(config.RepoRoot, path)
}

func loadVMResourceSnapshot(path string) (api.VMResourceSnapshot, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return api.VMResourceSnapshot{}, err
	}
	var snapshot api.VMResourceSnapshot
	if err := json.Unmarshal(content, &snapshot); err != nil {
		return api.VMResourceSnapshot{}, err
	}
	if snapshot.ID == "" || snapshot.Source == "" || snapshot.Accelerator == "" {
		return api.VMResourceSnapshot{}, fmt.Errorf("VM resource snapshot is missing id, source, or accelerator")
	}
	return snapshot, nil
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func withFields(value any, fields map[string]any) map[string]any {
	bytes, err := json.Marshal(value)
	if err != nil {
		fields["valid"] = false
		fields["error"] = err.Error()
		return fields
	}
	result := map[string]any{}
	if err := json.Unmarshal(bytes, &result); err != nil {
		fields["valid"] = false
		fields["error"] = err.Error()
		return fields
	}
	for key, value := range fields {
		result[key] = value
	}
	return result
}

func emit(value any) error {
	bytes, err := marshalReport(value)
	if err != nil {
		return err
	}
	_, err = os.Stdout.Write(append(bytes, '\n'))
	return err
}

func emitReport(command string, value any, saveResultDir string) error {
	bytes, err := marshalReport(value)
	if err != nil {
		return err
	}
	if _, err := os.Stdout.Write(append(bytes, '\n')); err != nil {
		return err
	}
	if saveResultDir == "" {
		return nil
	}
	if err := os.MkdirAll(saveResultDir, 0o755); err != nil {
		return err
	}
	now := time.Now()
	timestamp := fmt.Sprintf("%s-%06d", now.Format("20060102-150405"), now.Nanosecond()/1000)
	commandName := strings.ReplaceAll(command, "-", "_")
	path := filepath.Join(saveResultDir, fmt.Sprintf("%s_%s_report.json", timestamp, commandName))
	return os.WriteFile(path, append(bytes, '\n'), 0o644)
}

func runTeamValidation(ctx context.Context, service api.Service, config api.ServerConfig, outputDir string) (map[string]any, error) {
	vmSnapshotPath := filepath.Join(config.RepoRoot, "docs", "evidence", "artifacts", "vm_20260707_resource_snapshot.json")
	return runTeamValidationWithSnapshot(ctx, service, config, outputDir, vmSnapshotPath)
}

func runTeamValidationWithSnapshot(ctx context.Context, service api.Service, config api.ServerConfig, outputDir string, vmSnapshotPath string) (map[string]any, error) {
	if outputDir == "" {
		outputDir = filepath.Join(config.RepoRoot, "runs", "team-validation", time.Now().Format("20060102-150405"))
	}
	outputDirAbs, err := filepath.Abs(outputDir)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(outputDirAbs, 0o755); err != nil {
		return nil, err
	}

	llmConfig := filepath.Join(config.RepoRoot, "config", "ops_llm_benchmark.json")
	agentRegistry := filepath.Join(config.RepoRoot, "config", "agent_registry.json")
	vmRequirements := filepath.Join(config.RepoRoot, "config", "vm_workload_requirements.json")
	targetVM, err := loadVMResourceSnapshot(vmSnapshotPath)
	if err != nil {
		return nil, err
	}

	valid := true
	writeErrors := []string{}
	steps := []map[string]any{}
	addStep := func(name string, value any, stepValid bool, stepErr error) {
		reportValue := value
		if stepErr != nil {
			stepValid = false
			reportValue = map[string]any{
				"valid": false,
				"error": stepErr.Error(),
			}
		}
		bytes, marshalErr := marshalReport(reportValue)
		fileName := fmt.Sprintf("%02d_%s.json", len(steps)+1, strings.ReplaceAll(name, "-", "_"))
		path := filepath.Join(outputDirAbs, fileName)
		if marshalErr != nil {
			stepValid = false
			writeErrors = append(writeErrors, fmt.Sprintf("%s marshal failed: %s", name, marshalErr.Error()))
		} else if writeErr := os.WriteFile(path, append(bytes, '\n'), 0o644); writeErr != nil {
			stepValid = false
			writeErrors = append(writeErrors, fmt.Sprintf("%s write failed: %s", name, writeErr.Error()))
		}
		steps = append(steps, map[string]any{
			"name":        name,
			"valid":       stepValid,
			"output_path": path,
		})
		if !stepValid {
			valid = false
		}
	}

	llmSelection, err := service.SelectOpsLLMFromPath(ctx, llmConfig, "quality_first")
	addStep("select-ops-llm", llmSelection, err == nil && llmSelection.Valid && llmSelection.SelectedModel != "", err)

	agents, err := service.ListAgentsFromPath(ctx, agentRegistry)
	addStep("list-agents", agents, err == nil, err)

	actionValid, err := service.ValidateAgentActionFromPath(
		ctx,
		agentRegistry,
		"AIApplicationAutomationAgent",
		"observe_status",
	)
	addStep("validate-agent-action", map[string]any{
		"command": "validate-agent-action",
		"valid":   actionValid,
		"agent":   "AIApplicationAutomationAgent",
		"action":  "observe_status",
	}, err == nil && actionValid, err)

	compatibility, err := service.ValidateVMSuitabilityFromPath(ctx, vmRequirements, api.VMCompatibilityRequest{
		Workload: "llm-chat-inference",
		TargetVM: targetVM,
	})
	addStep("validate-vm-suitability", compatibility, err == nil && compatibility.Valid && compatibility.ResourceChecksPassed, err)

	deploymentPlan, err := service.BuildDeploymentPlanFromPath(ctx, vmRequirements, api.VMCompatibilityRequest{
		Workload: "llm-chat-inference",
		TargetVM: targetVM,
	})
	addStep("plan-ai-application-control", deploymentPlan, err == nil && deploymentPlan.Valid, err)

	serviceOperations, err := service.RunServiceOperations(ctx, api.ServiceOperationsRequest{
		LLMConfigPath:     llmConfig,
		VMRequirements:    vmRequirements,
		LLMPolicy:         "quality_first",
		Workload:          "llm-chat-inference",
		TargetVM:          targetVM,
		OperationService:  "llm-chat-inference",
		OperationResource: targetVM.ID,
		Mode:              "plan_only",
		GuardBackend:      "go",
	})
	addStep("run-service-operations", serviceOperations, err == nil && serviceOperations.Valid, err)

	if len(writeErrors) > 0 {
		valid = false
	}
	summary := map[string]any{
		"command":              "team-validation",
		"valid":                valid,
		"development_language": "go",
		"output_dir":           outputDirAbs,
		"steps":                steps,
		"scope": []string{
			"ops_llm_selection",
			"agent_registry_management",
			"actual_vm_workload_compatibility",
			"ai_application_deployment_control_plan",
			"service_operations_readiness",
		},
	}
	if len(writeErrors) > 0 {
		summary["write_errors"] = writeErrors
	}
	bytes, err := marshalReport(summary)
	if err != nil {
		return nil, err
	}
	summaryPath := filepath.Join(outputDirAbs, "00_team_validation_summary.json")
	if err := os.WriteFile(summaryPath, append(bytes, '\n'), 0o644); err != nil {
		return nil, err
	}
	summary["summary_path"] = summaryPath
	return summary, nil
}

func marshalReport(value any) ([]byte, error) {
	bytes, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return nil, err
	}
	bytes = []byte(strings.ReplaceAll(string(bytes), "\\u003c", "<"))
	bytes = []byte(strings.ReplaceAll(string(bytes), "\\u003e", ">"))
	bytes = []byte(strings.ReplaceAll(string(bytes), "\\u0026", "&"))
	return bytes, nil
}
