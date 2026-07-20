package main

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildVMResourceSnapshotUsesLiveEvidence(t *testing.T) {
	snapshot := buildVMResourceSnapshot(
		awsMetadataEvidence{
			Valid: true,
			Metadata: map[string]string{
				"instance_type":     "g6.xlarge",
				"region":            "us-west-2",
				"availability_zone": "us-west-2a",
			},
		},
		4,
		"MemTotal:       16384000 kB\n",
		"NVIDIA L4, 23034, 595.71.05",
		"NVIDIA-SMI 595.71.05 Driver Version: 595.71.05 CUDA Version: 13.2",
		"2026-07-16T01:02:03Z",
	)

	if snapshot.Source != "live_vm_system_validation" || snapshot.EvidenceStatus != "collected" {
		t.Fatalf("expected live collected evidence, got %#v", snapshot)
	}
	if snapshot.InstanceType != "g6.xlarge" || snapshot.CPUCores != 4 {
		t.Fatalf("expected actual instance and CPU data, got %#v", snapshot)
	}
	if math.Abs(snapshot.MemoryGB-15.625) > 0.001 {
		t.Fatalf("expected parsed memory, got %f", snapshot.MemoryGB)
	}
	if snapshot.Accelerator != "gpu" || snapshot.GPUModel != "NVIDIA L4" || snapshot.GPUMemoryMiB != 23034 {
		t.Fatalf("expected parsed GPU evidence, got %#v", snapshot)
	}
	if snapshot.DriverVersion != "595.71.05" || snapshot.CUDAVersion != "13.2" {
		t.Fatalf("expected driver and CUDA evidence, got %#v", snapshot)
	}
	if snapshot.Performance.Status != "not_measured" {
		t.Fatalf("hardware collection must not invent performance data: %#v", snapshot.Performance)
	}
	if strings.Contains(snapshot.ID, "i-") {
		t.Fatalf("snapshot id must not expose the cloud instance id: %s", snapshot.ID)
	}
}

func TestBuildVMResourceSnapshotSupportsCPUOnlyVM(t *testing.T) {
	snapshot := buildVMResourceSnapshot(
		awsMetadataEvidence{
			Valid: true,
			Metadata: map[string]string{
				"instance_type":     "c7i.xlarge",
				"region":            "ap-northeast-2",
				"availability_zone": "ap-northeast-2a",
			},
		},
		4,
		"MemTotal:       8192000 kB\n",
		"",
		"",
		"2026-07-16T01:02:03Z",
	)

	if snapshot.Accelerator != "cpu" {
		t.Fatalf("expected CPU-only snapshot, got %#v", snapshot)
	}
	if snapshot.EvidenceStatus != "collected" {
		t.Fatalf("expected collected CPU VM evidence, got %#v", snapshot)
	}
}

func TestRunOpsLLMBenchmarkCommandCreatesDryRunOutputs(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"run-ops-llm-benchmark",
		"--scenarios", filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl"),
		"--candidates", filepath.Join("..", "..", "..", "..", "config", "ops_llm_eval_candidates.json"),
		"--output-dir", outputDir,
		"--dry-run",
	})
	if err != nil {
		t.Fatalf("run returned error: %v", err)
	}
	if _, err := os.Stat(filepath.Join(outputDir, "model_outputs.jsonl")); err != nil {
		t.Fatalf("expected dry-run model outputs: %v", err)
	}
}

func TestEvaluateOpsLLMOutputsCommandCreatesSummary(t *testing.T) {
	outputDir := t.TempDir()
	scenarios := filepath.Join("..", "..", "..", "..", "data", "ops_llm_eval_scenarios.jsonl")
	candidates := filepath.Join("..", "..", "..", "..", "config", "ops_llm_eval_candidates.json")

	if err := run([]string{
		"run-ops-llm-benchmark",
		"--scenarios", scenarios,
		"--candidates", candidates,
		"--output-dir", outputDir,
		"--dry-run",
	}); err != nil {
		t.Fatalf("run benchmark returned error: %v", err)
	}

	summaryPath := filepath.Join(outputDir, "evaluation_summary.json")
	err := run([]string{
		"evaluate-ops-llm-outputs",
		"--scenarios", scenarios,
		"--outputs", filepath.Join(outputDir, "model_outputs.jsonl"),
		"--summary", summaryPath,
	})
	if err != nil {
		t.Fatalf("evaluate returned error: %v", err)
	}
	if _, err := os.Stat(summaryPath); err != nil {
		t.Fatalf("expected evaluation summary: %v", err)
	}
}

func TestPlanLLMAutomationActionCommandExecutesConfiguredProvider(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		content := `{
  "action":"observe_status",
  "reason":"Performance evidence is not measured.",
  "confidence":0.82,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`
		writer.Header().Set("content-type", "application/json")
		_, _ = fmt.Fprintf(writer, `{"choices":[{"message":{"content":%q}}]}`, content)
	}))
	defer provider.Close()

	candidatesPath := filepath.Join(t.TempDir(), "candidates.json")
	writeMainTestFile(t, candidatesPath, fmt.Sprintf(`{
  "version":"1",
  "candidates":[{
    "candidate_id":"decision-model",
    "role_label":"primary-ops-llm",
    "provider":"test-provider",
    "actual_model":"test-model",
    "endpoint":%q,
    "enabled":true
  }]
}`, provider.URL))
	reportsDir := t.TempDir()
	err := run([]string{
		"plan-llm-automation-action",
		"--requirements", filepath.Join("..", "..", "..", "..", "config", "vm_workload_requirements.json"),
		"--vm-snapshot", filepath.Join("..", "..", "..", "..", "docs", "evidence", "artifacts", "vm_20260707_resource_snapshot.json"),
		"--candidates", candidatesPath,
		"--candidate-id", "decision-model",
		"--workload", "llm-chat-inference",
		"--save-result-dir", reportsDir,
	})
	if err != nil {
		t.Fatalf("run returned error: %v", err)
	}
	reports, err := filepath.Glob(filepath.Join(reportsDir, "*_plan_llm_automation_action_report.json"))
	if err != nil || len(reports) != 1 {
		t.Fatalf("expected one report, got %v err=%v", reports, err)
	}
	content, err := os.ReadFile(reports[0])
	if err != nil {
		t.Fatalf("read report: %v", err)
	}
	var report map[string]any
	if err := json.Unmarshal(content, &report); err != nil {
		t.Fatalf("decode report: %v", err)
	}
	decision, ok := report["decision"].(map[string]any)
	if !ok {
		t.Fatalf("missing decision: %#v", report)
	}
	if decision["decision_execution_status"] != "executed" || decision["actual_model"] != "test-model" {
		t.Fatalf("expected actual executed LLM decision: %#v", decision)
	}
}

func TestRunAppDeployPlannerCommandExecutesEndToEndContract(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("content-type", "application/json")
		switch {
		case request.Method == http.MethodPost && request.URL.Path == "/llm":
			content := `{"schema_version":"deployment.khu.ai/v1alpha1","kind":"DeploymentManifest","spec":{"app_version_id":"appver-cli-v1","accelerator":"nvidia","resources":{"cpu":"4","memory":"16Gi","gpu":"1","storage":"20Gi"}}}`
			_, _ = fmt.Fprintf(writer, `{"choices":[{"message":{"content":%q}}]}`, content)
		case request.Method == http.MethodPost && request.URL.Path == "/api/v1/deployments":
			writer.WriteHeader(http.StatusAccepted)
			_, _ = writer.Write([]byte(`{"deployment_id":"dep-cli-1","status":"REQUESTED"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v1/deployments/dep-cli-1":
			_, _ = writer.Write([]byte(`{"deployment_id":"dep-cli-1","status":"RUNNING","target_profile_id":"target-selected-by-appdeploy"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v1/deployments/dep-cli-1/logs":
			_, _ = writer.Write([]byte(`{"deployment_id":"dep-cli-1","items":[{"stage":"RUNNING","message":"ready"}]}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	defer upstream.Close()

	candidatesPath := filepath.Join(t.TempDir(), "candidates.json")
	writeMainTestFile(t, candidatesPath, fmt.Sprintf(`{"version":"1","candidates":[{"candidate_id":"planner-model","provider":"test-provider","actual_model":"test-model","endpoint":%q,"enabled":true}]}`, upstream.URL+"/llm"))
	reportsDir := t.TempDir()
	err := run([]string{
		"run-appdeploy-planner",
		"--request", "GPU 추론 앱을 배포해 주세요.",
		"--app-version-id", "appver-cli-v1",
		"--candidate-id", "planner-model",
		"--candidates", candidatesPath,
		"--appdeploy-base-url", upstream.URL + "/api/v1",
		"--poll-interval-ms", "1",
		"--max-poll-attempts", "3",
		"--save-result-dir", reportsDir,
	})
	if err != nil {
		t.Fatalf("run returned error: %v", err)
	}
	reports, err := filepath.Glob(filepath.Join(reportsDir, "*_run_appdeploy_planner_report.json"))
	if err != nil || len(reports) != 1 {
		t.Fatalf("expected planner report: reports=%v err=%v", reports, err)
	}
	content, err := os.ReadFile(reports[0])
	if err != nil {
		t.Fatalf("read planner report: %v", err)
	}
	if !strings.Contains(string(content), `"status": "RUNNING"`) || !strings.Contains(string(content), `"target_profile_id": "target-selected-by-appdeploy"`) {
		t.Fatalf("unexpected planner report: %s", content)
	}
}

func TestRunServiceOperationsCommandCanExecuteLLMDecision(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		content := `{
  "action":"observe_status",
  "reason":"Observe the provisionally compatible VM.",
  "confidence":0.8,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`
		writer.Header().Set("content-type", "application/json")
		_, _ = fmt.Fprintf(writer, `{"choices":[{"message":{"content":%q}}]}`, content)
	}))
	defer provider.Close()
	candidatesPath := filepath.Join(t.TempDir(), "candidates.json")
	writeMainTestFile(t, candidatesPath, fmt.Sprintf(`{"version":"1","candidates":[{"candidate_id":"decision-model","provider":"test-provider","actual_model":"test-model","endpoint":%q,"enabled":true}]}`, provider.URL))
	reportsDir := t.TempDir()

	err := run([]string{
		"run-service-operations",
		"--workload", "llm-chat-inference",
		"--llm-candidates", candidatesPath,
		"--llm-candidate-id", "decision-model",
		"--save-result-dir", reportsDir,
	})
	if err != nil {
		t.Fatalf("run returned error: %v", err)
	}
	reports, err := filepath.Glob(filepath.Join(reportsDir, "*_run_service_operations_report.json"))
	if err != nil || len(reports) != 1 {
		t.Fatalf("expected service operations report: reports=%v err=%v", reports, err)
	}
	content, _ := os.ReadFile(reports[0])
	if !strings.Contains(string(content), `"decision_execution_status": "executed"`) {
		t.Fatalf("expected executed LLM decision: %s", content)
	}
}

func TestValidateSystemLocalCommandCreatesSummaryAndEnvironmentEvidence(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}

	summaryPath := filepath.Join(outputDir, "00_system_validation_summary.json")
	environmentPath := filepath.Join(outputDir, "01_environment.json")
	if _, err := os.Stat(summaryPath); err != nil {
		t.Fatalf("expected system validation summary: %v", err)
	}
	if _, err := os.Stat(environmentPath); err != nil {
		t.Fatalf("expected environment evidence: %v", err)
	}

	var summary map[string]any
	bytes, err := os.ReadFile(summaryPath)
	if err != nil {
		t.Fatalf("failed to read summary: %v", err)
	}
	if err := json.Unmarshal(bytes, &summary); err != nil {
		t.Fatalf("failed to parse summary: %v", err)
	}
	if summary["command"] != "validate-system" {
		t.Fatalf("expected validate-system command, got %#v", summary["command"])
	}
	if summary["target"] != "local" {
		t.Fatalf("expected local target, got %#v", summary["target"])
	}
	steps, ok := summary["steps"].([]any)
	if !ok {
		t.Fatalf("expected steps array in summary: %#v", summary["steps"])
	}
	assertStepSkipped(t, steps, "ops-llm-evaluation", "--run-llm-benchmark was not enabled")
	assertStepSkipped(t, steps, "llm-automation-decision", "--run-llm-decision was not enabled")
	assertStepSkipped(t, steps, "api-integration-validation", "--run-api-integration was not enabled")
}

func TestValidateSystemRejectsUnknownTarget(t *testing.T) {
	err := run([]string{
		"validate-system",
		"--target", "edge",
		"--output-dir", t.TempDir(),
		"--skip-go-tests",
	})
	if err == nil {
		t.Fatal("expected invalid target error")
	}
}

func TestValidateSystemCanRunExecutedLLMBenchmark(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("content-type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"scale_instances\",\"reason\":\"latency SLO violation\",\"confidence\":0.91}"}}]}`))
	}))
	defer server.Close()

	dir := t.TempDir()
	scenariosPath := filepath.Join(dir, "scenarios.jsonl")
	candidatesPath := filepath.Join(dir, "candidates.json")
	writeMainTestFile(t, scenariosPath, `{"id":"ops-test","scenario":"Scale the service.","allowed_actions":["scale_instances","monitor_latency"],"expected_action":"scale_instances","required_output_fields":["action","reason","confidence"]}`+"\n")
	writeMainTestFile(t, candidatesPath, `{"version":"1","candidates":[{"candidate_id":"local-provider","role_label":"primary-ops-llm","provider":"local-openai-compatible","actual_model":"test-model","endpoint":"`+server.URL+`/v1/chat/completions","enabled":true}]}`)

	outputDir := filepath.Join(dir, "validation")
	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
		"--skip-team-validation",
		"--run-llm-benchmark",
		"--llm-scenarios", scenariosPath,
		"--llm-candidates", candidatesPath,
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}

	summaryBytes, err := os.ReadFile(filepath.Join(outputDir, "00_system_validation_summary.json"))
	if err != nil {
		t.Fatalf("failed to read system validation summary: %v", err)
	}
	summary := string(summaryBytes)
	if !strings.Contains(summary, "ops-llm-evaluation") {
		t.Fatalf("expected LLM benchmark step in summary: %s", summary)
	}

	benchmarkSummaryPath := filepath.Join(outputDir, "ops-llm-benchmark", "evaluation_summary.json")
	benchmarkSummaryBytes, err := os.ReadFile(benchmarkSummaryPath)
	if err != nil {
		t.Fatalf("expected executed LLM benchmark summary: %v", err)
	}
	if !strings.Contains(string(benchmarkSummaryBytes), `"benchmark_status": "executed"`) {
		t.Fatalf("expected executed benchmark summary: %s", string(benchmarkSummaryBytes))
	}
}

func TestValidateSystemCanRunExecutedLLMAutomationDecision(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		content := `{
  "action":"observe_status",
  "reason":"Performance evidence is not measured.",
  "confidence":0.82,
  "required_capability":"ai_application_deployment_control",
  "target_vm_id":"aws-us-west-2-g6-xlarge-l4-20260707"
}`
		writer.Header().Set("content-type", "application/json")
		_, _ = fmt.Fprintf(writer, `{"choices":[{"message":{"content":%q}}]}`, content)
	}))
	defer provider.Close()

	dir := t.TempDir()
	candidatesPath := filepath.Join(dir, "candidates.json")
	writeMainTestFile(t, candidatesPath, fmt.Sprintf(`{
  "version":"1",
  "candidates":[{
    "candidate_id":"decision-model",
    "provider":"test-provider",
    "actual_model":"test-model",
    "endpoint":%q,
    "enabled":true
  }]
}`, provider.URL))
	outputDir := filepath.Join(dir, "validation")
	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
		"--skip-team-validation",
		"--run-llm-decision",
		"--llm-decision-candidates", candidatesPath,
		"--llm-decision-candidate-id", "decision-model",
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}
	summaryBytes, err := os.ReadFile(filepath.Join(outputDir, "00_system_validation_summary.json"))
	if err != nil {
		t.Fatalf("read summary: %v", err)
	}
	var summary map[string]any
	if err := json.Unmarshal(summaryBytes, &summary); err != nil {
		t.Fatalf("decode summary: %v", err)
	}
	step := findStep(t, summary["steps"].([]any), "llm-automation-decision")
	if step["valid"] != true || step["skipped"] == true {
		t.Fatalf("expected executed LLM decision step: %#v", step)
	}
	decisionBytes, err := os.ReadFile(filepath.Join(outputDir, "07_llm_automation_action.json"))
	if err != nil {
		t.Fatalf("read decision evidence: %v", err)
	}
	if !strings.Contains(string(decisionBytes), `"decision_execution_status": "executed"`) {
		t.Fatalf("expected executed decision evidence: %s", decisionBytes)
	}
}

func TestValidateSystemMarksFailedLLMDecisionWithoutFallback(t *testing.T) {
	dir := t.TempDir()
	candidatesPath := filepath.Join(dir, "candidates.json")
	writeMainTestFile(t, candidatesPath, `{"version":"1","candidates":[{"candidate_id":"decision-model","provider":"test-provider","actual_model":"test-model","endpoint":"http://127.0.0.1:1/v1/chat/completions","enabled":true,"timeout_seconds":1}]}`)
	outputDir := filepath.Join(dir, "validation")
	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
		"--skip-team-validation",
		"--run-llm-decision",
		"--llm-decision-candidates", candidatesPath,
		"--llm-decision-candidate-id", "decision-model",
	})
	if err != nil {
		t.Fatalf("validate-system should record failed step instead of losing evidence: %v", err)
	}
	decisionBytes, err := os.ReadFile(filepath.Join(outputDir, "07_llm_automation_action.json"))
	if err != nil {
		t.Fatalf("read failed decision evidence: %v", err)
	}
	if !strings.Contains(string(decisionBytes), `"decision_execution_status": "llm_failed"`) {
		t.Fatalf("expected explicit failed decision without fallback: %s", decisionBytes)
	}
}

func TestAPIIntegrationValidationCommandCreatesSummary(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"api-integration-validation",
		"--output-dir", outputDir,
		"--port", "0",
	})
	if err != nil {
		t.Fatalf("api-integration-validation returned error: %v", err)
	}

	summaryPath := filepath.Join(outputDir, "api-integration-validation-summary.json")
	bytes, err := os.ReadFile(summaryPath)
	if err != nil {
		t.Fatalf("expected API integration summary: %v", err)
	}
	var summary map[string]any
	if err := json.Unmarshal(bytes, &summary); err != nil {
		t.Fatalf("failed to parse API integration summary: %v", err)
	}
	if summary["command"] != "api-integration-validation" {
		t.Fatalf("expected api-integration-validation command, got %#v", summary["command"])
	}
	if summary["valid"] != true {
		t.Fatalf("expected valid API integration validation, got %s", string(bytes))
	}
	if int(summary["endpoint_count"].(float64)) != 10 {
		t.Fatalf("expected ten endpoints including LLM Action and feedback, got %#v", summary["endpoint_count"])
	}
	if int(summary["valid_endpoint_count"].(float64)) != 10 {
		t.Fatalf("expected ten valid endpoints, got %#v", summary["valid_endpoint_count"])
	}
	if summary["production_level_validation"] != false {
		t.Fatalf("local API integration must not claim production validation")
	}
}

func TestValidateSystemCanRunAPIIntegrationValidation(t *testing.T) {
	outputDir := t.TempDir()

	err := run([]string{
		"validate-system",
		"--target", "local",
		"--output-dir", outputDir,
		"--skip-go-tests",
		"--skip-team-validation",
		"--run-api-integration",
		"--api-port", "0",
	})
	if err != nil {
		t.Fatalf("validate-system returned error: %v", err)
	}

	summaryBytes, err := os.ReadFile(filepath.Join(outputDir, "00_system_validation_summary.json"))
	if err != nil {
		t.Fatalf("failed to read system validation summary: %v", err)
	}
	var summary map[string]any
	if err := json.Unmarshal(summaryBytes, &summary); err != nil {
		t.Fatalf("failed to parse summary: %v", err)
	}
	steps := summary["steps"].([]any)
	step := findStep(t, steps, "api-integration-validation")
	if step["skipped"] == true {
		t.Fatalf("api integration step should have run: %#v", step)
	}
	if step["valid"] != true {
		t.Fatalf("api integration step should be valid: %#v", step)
	}
	if step["output_path"] == "" {
		t.Fatalf("api integration step should record output path: %#v", step)
	}
}

func findStep(t *testing.T, steps []any, name string) map[string]any {
	t.Helper()
	for _, raw := range steps {
		step, ok := raw.(map[string]any)
		if !ok {
			t.Fatalf("step is not object: %#v", raw)
		}
		if step["name"] == name {
			return step
		}
	}
	t.Fatalf("step %s not found in %#v", name, steps)
	return nil
}

func assertStepSkipped(t *testing.T, steps []any, name string, reason string) {
	t.Helper()
	step := findStep(t, steps, name)
	if step["valid"] != true {
		t.Fatalf("skipped step should be valid: %#v", step)
	}
	if step["skipped"] != true {
		t.Fatalf("expected step %s to be skipped: %#v", name, step)
	}
	if step["reason"] != reason {
		t.Fatalf("expected skip reason %q, got %#v", reason, step["reason"])
	}
}

func writeMainTestFile(t *testing.T, path string, value string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(value), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}
}
