package main

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/api"
	"kyunghee-aiops/service-control-api/internal/benchmark"
)

type systemValidationOptions struct {
	Target             string
	OutputDir          string
	SkipGoTests        bool
	SkipTeamValidation bool
	RunLLMBenchmark    bool
	LLMScenariosPath   string
	LLMCandidatesPath  string
	LLMDryRun          bool
}

type systemValidationStep struct {
	Name       string `json:"name"`
	Valid      bool   `json:"valid"`
	Skipped    bool   `json:"skipped,omitempty"`
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

func runSystemValidation(service api.Service, config api.ServerConfig, options systemValidationOptions) (map[string]any, error) {
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
		addStep(systemValidationStep{Name: "go-test-aiops-guard", Valid: true, Skipped: true})
		addStep(systemValidationStep{Name: "go-test-service-control-api", Valid: true, Skipped: true})
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

	if options.SkipTeamValidation {
		addStep(systemValidationStep{Name: "team-validation", Valid: true, Skipped: true})
	} else {
		teamValidationDir := filepath.Join(outputDirAbs, "team-validation")
		teamValidation, err := runTeamValidation(service, config, teamValidationDir)
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
	}

	if target == "vm" {
		addStep(runCommandStep(
			"vm-gpu-nvidia-smi",
			config.RepoRoot,
			filepath.Join(outputDirAbs, "04_vm_nvidia_smi.txt"),
			"nvidia-smi",
		))
		metadataPath := filepath.Join(outputDirAbs, "05_vm_aws_metadata.json")
		metadata := collectAWSMetadata()
		addStep(writeJSONStep("vm-aws-metadata", metadataPath, metadata))
		if !metadata.Valid {
			valid = false
		}
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
			"service_operations_readiness",
			"target_environment_evidence",
		},
	}
	if target == "vm" {
		summary["vm_scope"] = []string{
			"nvidia_smi",
			"gpu_driver_cuda_visibility",
			"aws_instance_metadata",
		}
	}

	summaryPath := filepath.Join(outputDirAbs, "00_system_validation_summary.json")
	if err := writeJSONFile(summaryPath, summary); err != nil {
		return nil, err
	}
	summary["summary_path"] = summaryPath
	return summary, nil
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
	defer response.Body.Close()
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
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("metadata status %d", response.StatusCode)
	}
	var body bytes.Buffer
	if _, err := body.ReadFrom(response.Body); err != nil {
		return "", err
	}
	return strings.TrimSpace(body.String()), nil
}
