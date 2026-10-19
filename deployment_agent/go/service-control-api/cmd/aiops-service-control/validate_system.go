package main

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/api"
	"kyunghee-aiops/service-control-api/internal/benchmark"
)

type systemValidationOptions struct {
	Target                    string
	OutputDir                 string
	SkipGoTests               bool
	SkipTeamValidation        bool
	RunLLMBenchmark           bool
	LLMScenariosPath          string
	LLMCandidatesPath         string
	LLMDryRun                 bool
	RunLLMDecision            bool
	LLMDecisionCandidatesPath string
	LLMDecisionCandidateID    string
	RunAPIIntegration         bool
	APIPort                   int
}

type systemValidationStep struct {
	Name       string `json:"name"`
	Valid      bool   `json:"valid"`
	Skipped    bool   `json:"skipped,omitempty"`
	Reason     string `json:"reason,omitempty"`
	OutputPath string `json:"output_path,omitempty"`
	Error      string `json:"error,omitempty"`
}

type systemEnvironmentEvidence struct {
	Target           string `json:"target"`
	GeneratedAt      string `json:"generated_at"`
	Hostname         string `json:"hostname"`
	User             string `json:"user"`
	OS               string `json:"os"`
	Architecture     string `json:"architecture"`
	WorkingDirectory string `json:"working_directory"`
	RepoRoot         string `json:"repo_root"`
	GoVersion        string `json:"go_version"`
	GitBranch        string `json:"git_branch"`
	GitCommit        string `json:"git_commit"`
	GitStatus        string `json:"git_status"`
}

type awsMetadataEvidence struct {
	Valid        bool              `json:"valid"`
	CollectedAt  string            `json:"collected_at"`
	Metadata     map[string]string `json:"metadata"`
	Error        string            `json:"error,omitempty"`
	MetadataNote string            `json:"metadata_note,omitempty"`
}

func runSystemValidation(ctx context.Context, service api.Service, config api.ServerConfig, options systemValidationOptions) (map[string]any, error) {
	target := strings.ToLower(strings.TrimSpace(options.Target))
	if target == "" {
		target = "local"
	}
	if target != "local" && target != "vm" {
		return nil, fmt.Errorf("--target must be local or vm")
	}

	outputDir := options.OutputDir
	if outputDir == "" {
		outputDir = filepath.Join(config.RepoRoot, "runs", "system-validation-"+target+"-"+time.Now().Format("20060102-150405"))
	}
	outputDirAbs, err := filepath.Abs(outputDir)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(outputDirAbs, 0o755); err != nil {
		return nil, err
	}

	valid := true
	steps := []systemValidationStep{}
	addStep := func(step systemValidationStep) {
		steps = append(steps, step)
		if !step.Valid {
			valid = false
		}
	}

	environmentPath := filepath.Join(outputDirAbs, "01_environment.json")
	environment := collectSystemEnvironment(target, config.RepoRoot)
	addStep(writeJSONStep("collect-environment", environmentPath, environment))

	goBinary := findGoBinary()
	if options.SkipGoTests {
		addStep(systemValidationStep{Name: "go-test-aiops-guard", Valid: true, Skipped: true, Reason: "--skip-go-tests was enabled"})
		addStep(systemValidationStep{Name: "go-test-service-control-api", Valid: true, Skipped: true, Reason: "--skip-go-tests was enabled"})
	} else {
		addStep(runCommandStep(
			"go-test-aiops-guard",
			filepath.Join(config.RepoRoot, "go", "aiops-guard"),
			filepath.Join(outputDirAbs, "02_go_test_aiops_guard.txt"),
			goBinary,
			"test",
			"./...",
		))
		addStep(runCommandStep(
			"go-test-service-control-api",
			filepath.Join(config.RepoRoot, "go", "service-control-api"),
			filepath.Join(outputDirAbs, "03_go_test_service_control_api.txt"),
			goBinary,
			"test",
			"./...",
		))
	}

	teamSnapshotPath := ""
	if target == "vm" {
		nvidiaOutputPath := filepath.Join(outputDirAbs, "04_vm_nvidia_smi.txt")
		nvidiaStep := runCommandStep(
			"vm-gpu-nvidia-smi",
			config.RepoRoot,
			nvidiaOutputPath,
			"nvidia-smi",
		)
		addStep(nvidiaStep)

		metadataPath := filepath.Join(outputDirAbs, "05_vm_aws_metadata.json")
		metadata := collectAWSMetadata()
		addStep(writeJSONStep("vm-aws-metadata", metadataPath, metadata))
		if !metadata.Valid {
			valid = false
		}

		memInfo, _ := os.ReadFile("/proc/meminfo")
		nvidiaQuery := commandOutput(
			config.RepoRoot,
			"nvidia-smi",
			"--query-gpu=name,memory.total,driver_version",
			"--format=csv,noheader,nounits",
		)
		nvidiaOutput := commandOutput(config.RepoRoot, "nvidia-smi")
		snapshot := buildVMResourceSnapshot(
			metadata,
			runtime.NumCPU(),
			string(memInfo),
			nvidiaQuery,
			nvidiaOutput,
			time.Now().UTC().Format(time.RFC3339),
		)
		teamSnapshotPath = filepath.Join(outputDirAbs, "06_vm_resource_snapshot.json")
		snapshotStep := writeJSONStep("vm-resource-snapshot", teamSnapshotPath, snapshot)
		if snapshot.EvidenceStatus != "collected" {
			snapshotStep.Valid = false
			snapshotStep.Reason = "live VM resource evidence is incomplete"
		}
		addStep(snapshotStep)
	}

	if options.SkipTeamValidation {
		addStep(systemValidationStep{Name: "team-validation", Valid: true, Skipped: true, Reason: "--skip-team-validation was enabled"})
	} else {
		teamValidationDir := filepath.Join(outputDirAbs, "team-validation")
		var teamValidation map[string]any
		var err error
		if teamSnapshotPath != "" {
			teamValidation, err = runTeamValidationWithSnapshot(ctx, service, config, teamValidationDir, teamSnapshotPath)
		} else {
			teamValidation, err = runTeamValidation(ctx, service, config, teamValidationDir)
		}
		teamStep := systemValidationStep{
			Name:       "team-validation",
			Valid:      false,
			OutputPath: filepath.Join(teamValidationDir, "00_team_validation_summary.json"),
		}
		if err != nil {
			teamStep.Error = err.Error()
		} else if teamValid, ok := teamValidation["valid"].(bool); ok {
			teamStep.Valid = teamValid
		}
		addStep(teamStep)
	}

	if options.RunLLMBenchmark {
		benchmarkDir := filepath.Join(outputDirAbs, "ops-llm-benchmark")
		runResult, err := benchmark.RunOpsLLMBenchmark(benchmark.RunOptions{
			ScenariosPath:  options.LLMScenariosPath,
			CandidatesPath: options.LLMCandidatesPath,
			OutputDir:      benchmarkDir,
			DryRun:         options.LLMDryRun,
		})
		benchmarkStep := systemValidationStep{
			Name:       "ops-llm-evaluation",
			Valid:      err == nil && runResult.Valid,
			OutputPath: runResult.OutputsPath,
		}
		if err != nil {
			benchmarkStep.Error = err.Error()
		}
		addStep(benchmarkStep)

		if err == nil {
			evaluationSummaryPath := filepath.Join(benchmarkDir, "evaluation_summary.json")
			evaluation, evalErr := benchmark.EvaluateOpsLLMOutputs(benchmark.EvaluateOptions{
				ScenariosPath: options.LLMScenariosPath,
				OutputsPath:   runResult.OutputsPath,
				SummaryPath:   evaluationSummaryPath,
			})
			evaluationStep := systemValidationStep{
				Name:       "ops-llm-evaluator",
				Valid:      evalErr == nil && evaluation.Valid,
				OutputPath: evaluationSummaryPath,
			}
			if evalErr != nil {
				evaluationStep.Error = evalErr.Error()
			}
			addStep(evaluationStep)
		}
	} else {
		addStep(systemValidationStep{
			Name:    "ops-llm-evaluation",
			Valid:   true,
			Skipped: true,
			Reason:  "--run-llm-benchmark was not enabled",
		})
	}

	if options.RunLLMDecision {
		decisionSnapshotPath := teamSnapshotPath
		if decisionSnapshotPath == "" {
			decisionSnapshotPath = filepath.Join(config.RepoRoot, "docs", "evidence", "artifacts", "vm_20260707_resource_snapshot.json")
		}
		decisionOutputPath := filepath.Join(outputDirAbs, "07_llm_automation_action.json")
		decisionStep := systemValidationStep{
			Name:       "llm-automation-decision",
			Valid:      false,
			OutputPath: decisionOutputPath,
		}
		snapshot, loadErr := loadVMResourceSnapshot(decisionSnapshotPath)
		if loadErr != nil {
			decisionStep.Error = loadErr.Error()
			addStep(decisionStep)
		} else {
			decision, decisionErr := service.PlanLLMAutomationActionFromPaths(
				ctx,
				filepath.Join(config.RepoRoot, "config", "vm_workload_requirements.json"),
				options.LLMDecisionCandidatesPath,
				api.LLMAutomationActionRequest{
					Workload:    "llm-chat-inference",
					TargetVM:    snapshot,
					CandidateID: options.LLMDecisionCandidateID,
				},
			)
			if writeErr := writeJSONFile(decisionOutputPath, decision); writeErr != nil {
				decisionStep.Error = writeErr.Error()
			} else if decisionErr != nil {
				decisionStep.Error = decisionErr.Error()
			} else {
				decisionStep.Valid = decision.Decision.DecisionExecutionStatus == "executed" && decision.Guard.Valid
				if !decision.Valid && decision.Status == "pending_executor" {
					decisionStep.Reason = "LLM decision and Go Guard passed; no external executor is registered in this validation process"
				}
			}
			addStep(decisionStep)
		}
	} else {
		addStep(systemValidationStep{
			Name:    "llm-automation-decision",
			Valid:   true,
			Skipped: true,
			Reason:  "--run-llm-decision was not enabled",
		})
	}

	if options.RunAPIIntegration {
		apiOutputDir := filepath.Join(outputDirAbs, "api-integration-validation")
		apiSummary, err := runAPIIntegrationValidation(config, apiIntegrationValidationOptions{
			OutputDir: apiOutputDir,
			Port:      options.APIPort,
		})
		apiStep := systemValidationStep{
			Name:       "api-integration-validation",
			Valid:      err == nil && apiSummary.Valid,
			OutputPath: filepath.Join(apiOutputDir, "api-integration-validation-summary.json"),
		}
		if err != nil {
			apiStep.Error = err.Error()
		}
		addStep(apiStep)
	} else {
		addStep(systemValidationStep{
			Name:    "api-integration-validation",
			Valid:   true,
			Skipped: true,
			Reason:  "--run-api-integration was not enabled",
		})
	}

	summary := map[string]any{
		"command":              "validate-system",
		"valid":                valid,
		"target":               target,
		"development_language": "go",
		"output_dir":           outputDirAbs,
		"environment_path":     environmentPath,
		"steps":                steps,
		"scope": []string{
			"go_module_tests",
			"team_validation",
			"ops_llm_benchmark",
			"llm_automation_decision",
			"api_integration_validation",
			"service_operations_readiness",
			"target_environment_evidence",
		},
	}
	if target == "vm" {
		summary["vm_scope"] = []string{
			"nvidia_smi",
			"gpu_driver_cuda_visibility",
			"aws_instance_metadata",
			"live_vm_resource_snapshot",
		}
	}

	summaryPath := filepath.Join(outputDirAbs, "00_system_validation_summary.json")
	if err := writeJSONFile(summaryPath, summary); err != nil {
		return nil, err
	}
	summary["summary_path"] = summaryPath
	return summary, nil
}

var cudaVersionPattern = regexp.MustCompile(`CUDA Version:\s*([0-9.]+)`)

func buildVMResourceSnapshot(
	metadata awsMetadataEvidence,
	cpuCores int,
	memInfo string,
	nvidiaQuery string,
	nvidiaOutput string,
	collectedAt string,
) api.VMResourceSnapshot {
	provider := ""
	if metadata.Valid {
		provider = "aws"
	}
	accelerator := "cpu"
	gpuModel, gpuMemoryMiB, driverVersion := parseNVIDIAQuery(nvidiaQuery)
	if gpuModel != "" {
		accelerator = "gpu"
	}
	cudaVersion := ""
	if matches := cudaVersionPattern.FindStringSubmatch(nvidiaOutput); len(matches) == 2 {
		cudaVersion = matches[1]
	}
	memoryGB := parseMemoryGB(memInfo)
	evidenceStatus := "incomplete"
	if metadata.Valid && cpuCores > 0 && memoryGB > 0 {
		evidenceStatus = "collected"
	}
	instanceType := metadata.Metadata["instance_type"]
	region := metadata.Metadata["region"]
	snapshotID := buildVMSnapshotID(provider, region, instanceType, accelerator, collectedAt)

	return api.VMResourceSnapshot{
		ID:               snapshotID,
		Source:           "live_vm_system_validation",
		EvidenceStatus:   evidenceStatus,
		Provider:         provider,
		Region:           region,
		AvailabilityZone: metadata.Metadata["availability_zone"],
		InstanceType:     instanceType,
		Accelerator:      accelerator,
		CPUCores:         cpuCores,
		MemoryGB:         memoryGB,
		GPUModel:         gpuModel,
		GPUMemoryMiB:     gpuMemoryMiB,
		DriverVersion:    driverVersion,
		CUDAVersion:      cudaVersion,
		CollectedAt:      collectedAt,
		Performance: api.VMPerformanceEvidence{
			Status: "not_measured",
		},
	}
}

func parseNVIDIAQuery(value string) (string, int, string) {
	line := strings.TrimSpace(strings.Split(value, "\n")[0])
	if line == "" || strings.Contains(strings.ToLower(line), "not found") {
		return "", 0, ""
	}
	parts := strings.Split(line, ",")
	if len(parts) < 3 {
		return "", 0, ""
	}
	memoryMiB, err := strconv.Atoi(strings.TrimSpace(parts[1]))
	if err != nil {
		return "", 0, ""
	}
	return strings.TrimSpace(parts[0]), memoryMiB, strings.TrimSpace(parts[2])
}

func parseMemoryGB(memInfo string) float64 {
	for _, line := range strings.Split(memInfo, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 || fields[0] != "MemTotal:" {
			continue
		}
		memoryKiB, err := strconv.ParseFloat(fields[1], 64)
		if err != nil {
			return 0
		}
		return memoryKiB / 1024 / 1024
	}
	return 0
}

func buildVMSnapshotID(provider string, region string, instanceType string, accelerator string, collectedAt string) string {
	values := []string{provider, region, instanceType, accelerator, collectedAt}
	for index, value := range values {
		value = strings.ToLower(strings.TrimSpace(value))
		replacer := strings.NewReplacer(" ", "-", ".", "-", "/", "-", ":", "", "+", "-")
		values[index] = replacer.Replace(value)
	}
	return strings.Trim(strings.Join(values, "-"), "-")
}

func collectSystemEnvironment(target string, repoRoot string) systemEnvironmentEvidence {
	hostname, _ := os.Hostname()
	workingDirectory, _ := os.Getwd()
	return systemEnvironmentEvidence{
		Target:           target,
		GeneratedAt:      time.Now().UTC().Format(time.RFC3339),
		Hostname:         hostname,
		User:             firstNonEmpty(os.Getenv("USER"), os.Getenv("USERNAME")),
		OS:               runtime.GOOS,
		Architecture:     runtime.GOARCH,
		WorkingDirectory: workingDirectory,
		RepoRoot:         repoRoot,
		GoVersion:        strings.TrimSpace(commandOutput(repoRoot, findGoBinary(), "version")),
		GitBranch:        strings.TrimSpace(commandOutput(repoRoot, "git", "branch", "--show-current")),
		GitCommit:        strings.TrimSpace(commandOutput(repoRoot, "git", "rev-parse", "--short", "HEAD")),
		GitStatus:        strings.TrimSpace(commandOutput(repoRoot, "git", "status", "--short", "--branch")),
	}
}

func runCommandStep(name string, workingDir string, outputPath string, command string, args ...string) systemValidationStep {
	step := systemValidationStep{Name: name, OutputPath: outputPath}
	if command == "" {
		step.Error = "command is empty"
		return step
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Dir = workingDir
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()

	content := bytes.Buffer{}
	content.WriteString("$ " + command + " " + strings.Join(args, " ") + "\n")
	if workingDir != "" {
		content.WriteString("cwd=" + workingDir + "\n")
	}
	content.WriteString("\n[stdout]\n")
	content.Write(stdout.Bytes())
	content.WriteString("\n[stderr]\n")
	content.Write(stderr.Bytes())
	if err != nil {
		content.WriteString("\n[error]\n" + err.Error() + "\n")
		step.Error = err.Error()
	}
	if ctx.Err() == context.DeadlineExceeded {
		step.Error = "command timed out"
	}
	if writeErr := os.WriteFile(outputPath, content.Bytes(), 0o644); writeErr != nil {
		step.Error = writeErr.Error()
		return step
	}
	step.Valid = err == nil && ctx.Err() == nil
	return step
}

func writeJSONStep(name string, outputPath string, value any) systemValidationStep {
	step := systemValidationStep{Name: name, OutputPath: outputPath}
	if err := writeJSONFile(outputPath, value); err != nil {
		step.Error = err.Error()
		return step
	}
	step.Valid = true
	return step
}

func writeJSONFile(path string, value any) error {
	bytes, err := marshalReport(value)
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(bytes, '\n'), 0o644)
}

func findGoBinary() string {
	if path, err := exec.LookPath("go"); err == nil {
		return path
	}
	if _, err := os.Stat("/usr/local/go/bin/go"); err == nil {
		return "/usr/local/go/bin/go"
	}
	return "go"
}

func commandOutput(workingDir string, command string, args ...string) string {
	if command == "" {
		return ""
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Dir = workingDir
	output, err := cmd.CombinedOutput()
	if err != nil {
		return strings.TrimSpace(string(output))
	}
	return strings.TrimSpace(string(output))
}

func collectAWSMetadata() awsMetadataEvidence {
	evidence := awsMetadataEvidence{
		CollectedAt: time.Now().UTC().Format(time.RFC3339),
		Metadata:    map[string]string{},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	client := &http.Client{Timeout: 2 * time.Second}
	token := requestIMDSToken(ctx, client)
	keys := map[string]string{
		"instance_id":       "instance-id",
		"instance_type":     "instance-type",
		"ami_id":            "ami-id",
		"local_ipv4":        "local-ipv4",
		"public_ipv4":       "public-ipv4",
		"availability_zone": "placement/availability-zone",
		"region":            "placement/region",
	}
	var errors []string
	for outputKey, metadataPath := range keys {
		value, err := requestIMDSValue(ctx, client, token, metadataPath)
		if err != nil {
			errors = append(errors, outputKey+": "+err.Error())
			continue
		}
		evidence.Metadata[outputKey] = value
	}
	evidence.Valid = evidence.Metadata["instance_id"] != "" && evidence.Metadata["instance_type"] != ""
	if len(errors) > 0 {
		evidence.Error = strings.Join(errors, "; ")
	}
	if !evidence.Valid {
		evidence.MetadataNote = "AWS instance metadata is expected only when validate-system runs inside an AWS VM."
	}
	return evidence
}

func requestIMDSToken(ctx context.Context, client *http.Client) string {
	request, err := http.NewRequestWithContext(ctx, http.MethodPut, "http://169.254.169.254/latest/api/token", nil)
	if err != nil {
		return ""
	}
	request.Header.Set("X-aws-ec2-metadata-token-ttl-seconds", "21600")
	response, err := client.Do(request)
	if err != nil {
		return ""
	}
	defer func() {
		_ = response.Body.Close()
	}()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return ""
	}
	var body bytes.Buffer
	_, _ = body.ReadFrom(response.Body)
	return strings.TrimSpace(body.String())
}

func requestIMDSValue(ctx context.Context, client *http.Client, token string, metadataPath string) (string, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://169.254.169.254/latest/meta-data/"+metadataPath, nil)
	if err != nil {
		return "", err
	}
	if token != "" {
		request.Header.Set("X-aws-ec2-metadata-token", token)
	}
	response, err := client.Do(request)
	if err != nil {
		return "", err
	}
	defer func() {
		_ = response.Body.Close()
	}()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("metadata status %d", response.StatusCode)
	}
	var body bytes.Buffer
	if _, err := body.ReadFrom(response.Body); err != nil {
		return "", err
	}
	return strings.TrimSpace(body.String()), nil
}
