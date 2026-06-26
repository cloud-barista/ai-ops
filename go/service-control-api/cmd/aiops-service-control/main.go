package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/api"
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
	switch args[0] {
	case "list-agents":
		flags := flag.NewFlagSet("list-agents", flag.ContinueOnError)
		registry := flags.String("registry", "config/agent_registry.json", "Agent registry JSON path")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		registryPath := resolveInputPath(serverConfig, *registry)
		result, err := service.ListAgentsFromPath(registryPath)
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
		agent, err := service.ShowAgentFromPath(registryPath, *agentName)
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
		valid, err := service.ValidateAgentActionFromPath(registryPath, *agentName, *action)
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
		result, err := service.SelectOpsLLMFromPath(configPath, *policy)
		if err != nil {
			return err
		}
		return emitReport("select-ops-llm", withFields(result, map[string]any{
			"command": "select-ops-llm",
			"config":  configPath,
		}), *saveResultDir)
	case "recommend-inference-placement":
		flags := flag.NewFlagSet("recommend-inference-placement", flag.ContinueOnError)
		config := flags.String("config", "config/inference_optimization.json", "Inference optimization JSON path")
		workload := flags.String("workload", "", "Inference workload ID")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		configPath := resolveInputPath(serverConfig, *config)
		result, err := service.RecommendPlacementFromPath(configPath, *workload)
		if err != nil {
			return err
		}
		return emitReport("recommend-inference-placement", withFields(result, map[string]any{
			"command": "recommend-inference-placement",
			"config":  configPath,
		}), *saveResultDir)
	case "plan-inference-deployment":
		flags := flag.NewFlagSet("plan-inference-deployment", flag.ContinueOnError)
		config := flags.String("config", "config/inference_optimization.json", "Inference optimization JSON path")
		workload := flags.String("workload", "", "Inference workload ID")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		configPath := resolveInputPath(serverConfig, *config)
		result, err := service.BuildDeploymentPlanFromPath(configPath, *workload)
		if err != nil {
			return err
		}
		return emitReport("plan-inference-deployment", withFields(result, map[string]any{
			"command": "plan-inference-deployment",
			"config":  configPath,
		}), *saveResultDir)
	case "run-service-operations":
		flags := flag.NewFlagSet("run-service-operations", flag.ContinueOnError)
		llmConfig := flags.String("llm-config", "config/ops_llm_benchmark.json", "Ops LLM benchmark JSON path")
		llmPolicy := flags.String("llm-policy", "quality_first", "Ops LLM selection policy")
		inferenceConfig := flags.String("inference-config", "config/inference_optimization.json", "Inference optimization JSON path")
		workload := flags.String("workload", "", "Inference workload ID")
		recoveryNamespace := flags.String("recovery-namespace", "", "Kubernetes namespace for recovery/operation context")
		recoveryDeployment := flags.String("recovery-deployment", "", "Kubernetes deployment for recovery/operation context")
		namespace := flags.String("namespace", "", "Deprecated alias for --recovery-namespace")
		deployment := flags.String("deployment", "", "Deprecated alias for --recovery-deployment")
		mode := flags.String("mode", "mock", "Execution mode")
		guardBackend := flags.String("guard-backend", "go", "Guard backend")
		saveResultDir := addSaveResultDirFlag(flags)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if *workload == "" {
			return fmt.Errorf("--workload is required")
		}
		normalizedNamespace := firstNonEmpty(*recoveryNamespace, *namespace)
		normalizedDeployment := firstNonEmpty(*recoveryDeployment, *deployment)
		if normalizedNamespace == "" {
			return fmt.Errorf("--recovery-namespace is required")
		}
		if normalizedDeployment == "" {
			return fmt.Errorf("--recovery-deployment is required")
		}
		result, err := service.RunServiceOperations(api.ServiceOperationsRequest{
			LLMConfigPath:      resolveInputPath(serverConfig, *llmConfig),
			InferenceConfig:    resolveInputPath(serverConfig, *inferenceConfig),
			LLMPolicy:          *llmPolicy,
			Workload:           *workload,
			RecoveryNamespace:  normalizedNamespace,
			RecoveryDeployment: normalizedDeployment,
			Namespace:          *namespace,
			Deployment:         *deployment,
			Mode:               *mode,
			GuardBackend:       *guardBackend,
		})
		if err != nil {
			return err
		}
		return emitReport("run-service-operations", result, *saveResultDir)
	case "team-validation":
		flags := flag.NewFlagSet("team-validation", flag.ContinueOnError)
		outputDir := flags.String("output-dir", "", "Directory where validation JSON reports are saved")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		result, err := runTeamValidation(service, serverConfig, *outputDir)
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

func runTeamValidation(service api.Service, config api.ServerConfig, outputDir string) (map[string]any, error) {
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
	inferenceConfig := filepath.Join(config.RepoRoot, "config", "inference_optimization.json")

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

	llmSelection, err := service.SelectOpsLLMFromPath(llmConfig, "quality_first")
	addStep("select-ops-llm", llmSelection, err == nil && llmSelection.Valid && llmSelection.SelectedModel != "", err)

	agents, err := service.ListAgentsFromPath(agentRegistry)
	addStep("list-agents", agents, err == nil, err)

	actionValid, err := service.ValidateAgentActionFromPath(
		agentRegistry,
		"AIApplicationManagementAgent",
		"app_scale_deployment",
	)
	addStep("validate-agent-action", map[string]any{
		"command": "validate-agent-action",
		"valid":   actionValid,
		"agent":   "AIApplicationManagementAgent",
		"action":  "app_scale_deployment",
	}, err == nil && actionValid, err)

	placement, err := service.RecommendPlacementFromPath(inferenceConfig, "llm-chat-inference")
	addStep("recommend-inference-placement", placement, err == nil && placement.Valid, err)

	deploymentPlan, err := service.BuildDeploymentPlanFromPath(inferenceConfig, "llm-chat-inference")
	addStep("plan-inference-deployment", deploymentPlan, err == nil && deploymentPlan.Valid, err)

	serviceOperations, err := service.RunServiceOperations(api.ServiceOperationsRequest{
		LLMConfigPath:      llmConfig,
		InferenceConfig:    inferenceConfig,
		LLMPolicy:          "quality_first",
		Workload:           "llm-chat-inference",
		RecoveryNamespace:  "aiops-demo",
		RecoveryDeployment: "aiops-service",
		Mode:               "mock",
		GuardBackend:       "go",
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
			"cpu_gpu_vm_inference_placement",
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
