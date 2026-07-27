package controlrun

import (
	"fmt"
	"sync"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestStoreLifecycleReturnsIsolatedCopies(t *testing.T) {
	store := NewStore()
	created := store.Create(CreateInput{
		RunID: "run-001",
		Request: SafeRequest{
			NaturalLanguageRequest: "GPU inference deployment",
			AppVersionID:           "appver-001",
		},
	})

	updated, err := store.Update(created.RunID, func(run *Run) error {
		run.Status = StatusManifestApproved
		run.Manifest = appdeploy.DeploymentManifest{
			SchemaVersion: appdeploy.ManifestSchemaVersion,
			Kind:          appdeploy.ManifestKind,
			Spec: appdeploy.DeploymentSpec{
				AppVersionID: "appver-001",
				Parameters:   map[string]any{"labels": map[string]any{"team": "aiops"}},
			},
		}
		run.CorrelationIDs = append(run.CorrelationIDs, "corr-001")
		run.Stages = append(run.Stages, Stage{Name: "manifest_guard", Status: "approved"})
		run.Execution = &AgentExecution{
			Status:      "completed",
			LatencyMS:   25,
			Proposal:    map[string]any{"action": "review_deployment_plan", "parameters": map[string]any{"decision": "approved"}},
			Result:      map[string]any{"review": map[string]any{"status": "approved"}},
			Evidence:    map[string]any{"source": "external-test"},
			GuardStatus: "approved",
		}
		return nil
	})
	if err != nil {
		t.Fatalf("update run: %v", err)
	}

	updated.Manifest.Spec.Parameters["labels"].(map[string]any)["team"] = "mutated"
	updated.CorrelationIDs[0] = "mutated"
	updated.Stages[0].Status = "mutated"
	updated.Execution.Proposal["parameters"].(map[string]any)["decision"] = "mutated"
	updated.Execution.Result["review"].(map[string]any)["status"] = "mutated"
	updated.Execution.Evidence["source"] = "mutated"

	loaded, ok := store.Get(created.RunID)
	if !ok {
		t.Fatal("expected created run")
	}
	if got := loaded.Manifest.Spec.Parameters["labels"].(map[string]any)["team"]; got != "aiops" {
		t.Fatalf("manifest parameters leaked mutable state: %v", got)
	}
	if loaded.CorrelationIDs[0] != "corr-001" {
		t.Fatalf("correlation IDs leaked mutable state: %#v", loaded.CorrelationIDs)
	}
	if loaded.Stages[0].Status != "approved" {
		t.Fatalf("stages leaked mutable state: %#v", loaded.Stages)
	}
	if got := loaded.Execution.Proposal["parameters"].(map[string]any)["decision"]; got != "approved" {
		t.Fatalf("Agent proposal leaked mutable state: %v", got)
	}
	if got := loaded.Execution.Result["review"].(map[string]any)["status"]; got != "approved" {
		t.Fatalf("Agent result leaked mutable state: %v", got)
	}
	if got := loaded.Execution.Evidence["source"]; got != "external-test" {
		t.Fatalf("Agent evidence leaked mutable state: %v", got)
	}
}

func TestStoreListsNewestRunFirst(t *testing.T) {
	store := NewStore()
	store.Create(CreateInput{RunID: "run-001"})
	store.Create(CreateInput{RunID: "run-002"})

	runs := store.List()
	if len(runs) != 2 || runs[0].RunID != "run-002" || runs[1].RunID != "run-001" {
		t.Fatalf("unexpected run order: %#v", runs)
	}
}

func TestStoreDeleteAndClear(t *testing.T) {
	store := NewStore()
	store.Create(CreateInput{RunID: "run-001"})
	store.Create(CreateInput{RunID: "run-002"})

	deleted, ok := store.Delete("run-001")
	if !ok || deleted.RunID != "run-001" {
		t.Fatalf("unexpected delete result: %#v, %v", deleted, ok)
	}
	if _, ok := store.Get("run-001"); ok {
		t.Fatal("deleted run remained in store")
	}
	if count := store.Clear(); count != 1 {
		t.Fatalf("expected one cleared run, got %d", count)
	}
	if runs := store.List(); len(runs) != 0 {
		t.Fatalf("expected empty store, got %#v", runs)
	}
}

func TestStoreUpdateMissingRun(t *testing.T) {
	store := NewStore()
	if _, err := store.Update("run-missing", func(*Run) error { return nil }); err == nil {
		t.Fatal("expected missing run update to fail")
	}
}

func TestStoreSerializesConcurrentUpdates(t *testing.T) {
	store := NewStore()
	store.Create(CreateInput{RunID: "run-001"})

	const updates = 40
	var group sync.WaitGroup
	group.Add(updates)
	for index := 0; index < updates; index++ {
		index := index
		go func() {
			defer group.Done()
			_, err := store.Update("run-001", func(run *Run) error {
				run.CorrelationIDs = append(run.CorrelationIDs, fmt.Sprintf("corr-%02d", index))
				return nil
			})
			if err != nil {
				t.Errorf("concurrent update: %v", err)
			}
		}()
	}
	group.Wait()

	run, ok := store.Get("run-001")
	if !ok || len(run.CorrelationIDs) != updates {
		t.Fatalf("lost concurrent updates: %#v", run.CorrelationIDs)
	}
}
