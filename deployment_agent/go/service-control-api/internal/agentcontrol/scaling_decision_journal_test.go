package agentcontrol

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func journalTestService(t *testing.T, directory string, result OperationOptimizationResult) (*Service, *recordingOperationOptimizationRuntime) {
	t.Helper()
	runtime := &recordingOperationOptimizationRuntime{result: result}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	service.SetScalingDecisionJournal(FileScalingDecisionJournal{Directory: directory})
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatal(err)
	}
	return service, runtime
}

func journalFiles(t *testing.T, directory string) []string {
	t.Helper()
	files, err := filepath.Glob(filepath.Join(directory, "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func readJournalSnapshot(t *testing.T, path string) (ScalingDecisionSnapshot, []byte) {
	t.Helper()
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var snapshot ScalingDecisionSnapshot
	if err := json.Unmarshal(encoded, &snapshot); err != nil {
		t.Fatal(err)
	}
	return snapshot, encoded
}

func TestScalingDecisionJournalPersistsKeepAndScaleWithoutExecutionClaims(t *testing.T) {
	for _, test := range []struct {
		name   string
		result OperationOptimizationResult
		want   string
	}{
		{name: "scale_out", result: approvedOperationOptimizationResult(), want: ScalingActionScaleOut},
		{name: "keep", result: func() OperationOptimizationResult {
			result := approvedOperationOptimizationResult()
			result.Decision.Action = ScalingActionKeep
			result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
			result.Decision.Evidence = nil
			return result
		}(), want: ScalingActionKeep},
	} {
		t.Run(test.name, func(t *testing.T) {
			directory := t.TempDir()
			service, runtime := journalTestService(t, directory, test.result)
			feedback := validOptimizationFeedbackEnvelope()
			if test.want == ScalingActionScaleOut {
				feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
			}
			flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
			if err != nil || flow.ScalingDecision == nil || flow.ScalingDecision.Action != test.want {
				t.Fatalf("decision: %+v, %v", flow.ScalingDecision, err)
			}
			files := journalFiles(t, directory)
			if len(files) != 1 {
				t.Fatalf("journal files = %d, want one", len(files))
			}
			stored, encoded := readJournalSnapshot(t, files[0])
			if stored.RecordType != "KHU_SCALING_DECISION_SNAPSHOT" ||
				stored.DecisionID != flow.ScalingDecision.DecisionID || stored.Action != test.want ||
				stored.PlannedCurrentReplicas != 1 || stored.PlannedDesiredReplicas != flow.ScalingDecision.DesiredReplicas ||
				stored.InputManifestRevision != 1 || stored.FeedbackMessageID != feedback.MessageID ||
				stored.FeedbackAcceptedAt == "" || stored.ApprovalRecordedAt == "" || stored.ApplicationContextSHA256 == "" ||
				stored.ResourceRecommendationSHA256 == "" {
				t.Fatalf("incomplete decision trace: %+v", stored)
			}
			if test.want == ScalingActionScaleOut && stored.OutputManifestRevision != 2 {
				t.Fatalf("scale-out output revision = %d", stored.OutputManifestRevision)
			}
			if test.want == ScalingActionKeep && stored.OutputManifestRevision != 0 {
				t.Fatalf("KEEP created output revision %d", stored.OutputManifestRevision)
			}
			for _, forbidden := range []string{"action_id", "ready_replicas", "user_request", "applied_replicas", "runtime_configuration", "assumptions"} {
				if strings.Contains(string(encoded), `"`+forbidden+`"`) {
					t.Fatalf("journal contains unproven or raw field %q", forbidden)
				}
			}
			recordWithSensitiveText := flow.ScalingDecisionHistory[0]
			recordWithSensitiveText.ApplicationContext.Data.ApplicationProfile.Analysis.Assumptions = []string{"SENSITIVE_PROFILE_TEXT"}
			recordWithSensitiveText.Decision.Reason = "SENSITIVE_AGENT_TEXT"
			projection, err := scalingDecisionSnapshot(recordWithSensitiveText, *flow.OperationAgentExecution, flow.CorrelationID)
			if err != nil {
				t.Fatal(err)
			}
			projectedBytes, err := json.Marshal(projection)
			if err != nil || strings.Contains(string(projectedBytes), "SENSITIVE_PROFILE_TEXT") ||
				strings.Contains(string(projectedBytes), "SENSITIVE_AGENT_TEXT") {
				t.Fatalf("decision trace included raw sensitive text: %s, %v", projectedBytes, err)
			}
			replay, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
			if err != nil || replay.ScalingDecision.DecisionID != stored.DecisionID ||
				len(journalFiles(t, directory)) != 1 || runtime.calls != 1 {
				t.Fatalf("exact replay changed journal or runtime: %v, calls=%d", err, runtime.calls)
			}
		})
	}
}

func TestScalingDecisionJournalOutlivesHistoryDeletionAndServiceRestart(t *testing.T) {
	directory := t.TempDir()
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
	result.Decision.Evidence = nil
	service, _ := journalTestService(t, directory, result)
	base := time.Date(2026, 7, 29, 3, 30, 0, 0, time.UTC)
	for index := 0; index < scalingDecisionHistoryLimit+2; index++ {
		feedback := validOptimizationFeedbackEnvelope()
		feedback.MessageID = fmt.Sprintf("journal-feedback-%d", index)
		feedback.Data.OptimizationFeedback.FeedbackID = fmt.Sprintf("journal-feedback-%d", index)
		feedback.OccurredAt = base.Add(time.Duration(index) * time.Second).Format(time.RFC3339)
		feedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = feedback.OccurredAt
		feedback.Data.OptimizationFeedback.CreatedAt = feedback.OccurredAt
		if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
			t.Fatalf("feedback %d: %v", index, err)
		}
	}
	flow, ok := service.GetFlow("flow-001")
	if !ok || len(flow.ScalingDecisionHistory) != scalingDecisionHistoryLimit || flow.ScalingDecisionHistoryDropped != 2 {
		t.Fatalf("in-memory history was not bounded: %+v", flow)
	}
	if files := journalFiles(t, directory); len(files) != scalingDecisionHistoryLimit+2 {
		t.Fatalf("journal files before flow deletion = %d", len(files))
	}
	service.DeleteFlow("flow-001")
	restarted := NewServiceWithRuntimes(nil, nil, nil, nil)
	restarted.SetScalingDecisionJournal(FileScalingDecisionJournal{Directory: directory})
	if files := journalFiles(t, directory); len(files) != scalingDecisionHistoryLimit+2 {
		t.Fatalf("flow deletion/restart lost journal files: %d", len(files))
	}
}

func TestScalingDecisionJournalRestartConflictsOnSameIDWithDifferentLocalTimes(t *testing.T) {
	directory := t.TempDir()
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
	result.Decision.Evidence = nil
	first, _ := journalTestService(t, directory, result)
	feedback := validOptimizationFeedbackEnvelope()
	if _, err := first.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
		t.Fatal(err)
	}
	restarted, _ := journalTestService(t, directory, result)
	restarted.now = func() time.Time { return time.Date(2026, 7, 29, 4, 0, 0, 0, time.UTC) }
	if _, err := restarted.ReceiveOptimizationFeedback(context.Background(), feedback); err == nil {
		t.Fatal("restart silently reused an existing decision_id with changed local acceptance times")
	}
	flow, ok := restarted.GetFlow("flow-001")
	if !ok || flow.ScalingDecision != nil || len(flow.ScalingDecisionHistory) != 0 || len(journalFiles(t, directory)) != 1 {
		t.Fatalf("restart ID conflict changed approved state or journal: %+v", flow)
	}
}

func TestScalingDecisionJournalWriteFailureCannotApproveOrReplay(t *testing.T) {
	root := t.TempDir()
	blocker := filepath.Join(root, "not-a-directory")
	if err := os.WriteFile(blocker, []byte("blocker"), 0600); err != nil {
		t.Fatal(err)
	}
	service, runtime := journalTestService(t, blocker, approvedOperationOptimizationResult())
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err == nil {
		t.Fatal("journal write failure was reported as an approved decision")
	}
	flow, ok := service.GetFlow("flow-001")
	if !ok || flow.ScalingDecision != nil || len(flow.ScalingDecisionHistory) != 0 ||
		len(flow.ManifestRevisions) != 1 || flow.OperationAgentExecution == nil ||
		flow.OperationAgentExecution.ScalingGuard.Status != GuardRejected ||
		flow.OperationAgentExecution.Decision.Action != "" {
		t.Fatalf("journal write failure committed a decision: %+v", flow)
	}
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err == nil || runtime.calls != 1 {
		t.Fatalf("same-ID replay looked successful or reran Agent: err=%v calls=%d", err, runtime.calls)
	}
}

func TestScalingDecisionJournalRejectsConflictingOrCorruptExistingFile(t *testing.T) {
	directory := t.TempDir()
	journal := FileScalingDecisionJournal{Directory: directory}
	snapshot := ScalingDecisionSnapshot{SchemaVersion: "0.1.0", RecordType: "KHU_SCALING_DECISION_SNAPSHOT", DecisionID: "decision-1", Action: ScalingActionKeep}
	if err := journal.SaveScalingDecision(snapshot); err != nil {
		t.Fatal(err)
	}
	if err := journal.SaveScalingDecision(snapshot); err != nil {
		t.Fatalf("exact same snapshot was not idempotent: %v", err)
	}
	snapshot.Action = ScalingActionScaleOut
	if err := journal.SaveScalingDecision(snapshot); err == nil {
		t.Fatal("same decision_id accepted different content")
	}
	files := journalFiles(t, directory)
	if len(files) != 1 {
		t.Fatalf("journal files = %d, want one", len(files))
	}
	if err := os.WriteFile(files[0], []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	snapshot.Action = ScalingActionKeep
	if err := journal.SaveScalingDecision(snapshot); err == nil {
		t.Fatal("corrupt journal content was accepted")
	}
}
