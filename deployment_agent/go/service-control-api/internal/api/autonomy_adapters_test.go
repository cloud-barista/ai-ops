package api

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/automation"
	"kyunghee-aiops/service-control-api/internal/autonomy"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

func TestAutonomyDecisionPlannerUsesBoundedQwenContext(t *testing.T) {
	temp := t.TempDir()
	candidates := filepath.Join(temp, "candidates.json")
	if err := os.WriteFile(candidates, []byte(`{"candidates":[{"candidate_id":"qwen3.5-ops-planner","provider":"test","actual_model":"qwen3.5:4b","endpoint":"http://127.0.0.1:1/v1/chat/completions","enabled":true}]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	completion := &capturingCompletion{content: `{"action":"restart_application","reason":"latency is above the SLO","confidence":0.91,"required_capability":"ai_application_deployment_control","target_vm_id":"target-primary","parameters":{}}`}
	planner := newAutonomyDecisionPlanner(ServerConfig{LLMCandidatesPath: candidates}, automation.NewPlanner(completion))
	input := autonomy.DecisionInput{Deployment: executorLikeDeployment(), Evaluation: autonomy.Evaluation{Status: autonomy.EvaluationViolated, EvidenceFresh: true, ConsecutiveViolations: 2, Violations: []autonomy.Violation{{Code: "latency_slo_exceeded", Observed: 900, Threshold: 500}}}, Config: autonomy.DefaultConfig()}

	decision, err := planner.Plan(context.Background(), input)
	if err != nil {
		t.Fatalf("plan: %v", err)
	}
	if decision.Action != autonomy.ActionRestart || decision.Confidence != 0.91 {
		t.Fatalf("unexpected decision: %#v", decision)
	}
	for _, required := range []string{"dep-1", "target-primary", "latency_slo_exceeded", "restart_application", "scale_out_application", "rollback_application"} {
		if !strings.Contains(completion.userPrompt, required) {
			t.Fatalf("planner context is missing %q: %s", required, completion.userPrompt)
		}
	}
	if strings.Contains(strings.ToLower(completion.userPrompt), "password") || strings.Contains(strings.ToLower(completion.userPrompt), "private_key") {
		t.Fatalf("planner prompt leaked a secret-shaped field: %s", completion.userPrompt)
	}
}

func TestAutonomyActionAuthorizerRejectsOperationActionsForAutomationAgent(t *testing.T) {
	authorizer := newAutonomyActionAuthorizer(NewServerConfig())
	approved, _, err := authorizer.Validate(context.Background(), string(autonomy.ActionRollback))
	if err != nil {
		t.Fatalf("authorize operation action: %v", err)
	}
	if approved {
		t.Fatal("expected rollback_application to be denied for the automation Agent")
	}
	approved, _, err = authorizer.Validate(context.Background(), "delete_infrastructure")
	if err != nil {
		t.Fatalf("reject unknown action: %v", err)
	}
	if approved {
		t.Fatal("unknown Action must be rejected")
	}
}

type capturingCompletion struct {
	content    string
	userPrompt string
}

func (completion *capturingCompletion) Complete(_ context.Context, candidate llmclient.Candidate, _ string, userPrompt string) (llmclient.Completion, error) {
	completion.userPrompt = userPrompt
	return llmclient.Completion{Status: "executed", Content: completion.content, CandidateID: candidate.CandidateID, ActualModel: candidate.ActualModel, Provider: candidate.Provider}, nil
}

func executorLikeDeployment() appdeploy.DeploymentResponse {
	return appdeploy.DeploymentResponse{
		DeploymentID: "dep-1", AppVersionID: "appver-current", TargetProfileID: "target-primary", Status: "RUNNING",
		Manifest: appdeploy.DeploymentManifest{Spec: appdeploy.DeploymentSpec{AppVersionID: "appver-current", TargetProfileID: "target-primary", Accelerator: "gpu", Resources: appdeploy.ResourceRequirements{CPU: "4", Memory: "16Gi", GPU: "1", Storage: "20Gi"}}},
	}
}
