package controlrun

import (
	"encoding/json"
	"fmt"
	"sync"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestStoreApplicationEvidenceReturnsIsolatedCopies(t *testing.T) {
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
		manifestRequirements := &appdeploy.DeploymentRequirements{
			Runtime:    "cpu",
			CostPolicy: "min_cost",
			SLO:        map[string]any{"latency": map[string]any{"p99_ms": 250}},
			Labels:     map[string]string{"team": "aiops"},
		}
		run.Manifest = appdeploy.DeploymentManifest{
			SchemaVersion: appdeploy.ManifestSchemaVersion,
			Kind:          appdeploy.ManifestKind,
			Spec: appdeploy.DeploymentSpec{
				AppVersionID: "appver-001",
				Parameters:   map[string]any{"labels": map[string]any{"team": "aiops"}},
				Requirements: manifestRequirements,
			},
		}
		generationRequirements := &appdeploy.DeploymentRequirements{
			Runtime:    "gpu",
			CostPolicy: "min_cost",
			SLO:        map[string]any{"throughput": map[string]any{"rps": 20}},
			Labels:     map[string]string{"source": "generation"},
		}
		run.Generation.Manifest = appdeploy.DeploymentManifest{
			SchemaVersion: appdeploy.ManifestSchemaVersion,
			Kind:          appdeploy.ManifestKind,
			Spec: appdeploy.DeploymentSpec{
				AppVersionID: "appver-001",
				Requirements: generationRequirements,
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
		run.Application = &ApplicationEvidence{
			Package: &appdeploy.PackageBuildResponse{
				ArtifactURI: "file:///packages/app.tar.gz",
				ArchiveName: "app.tar.gz",
				Checksum:    "sha256:package",
				AppSpec:     json.RawMessage(`{"kind":"AIApp","source":"package"}`),
			},
			Registration: &appdeploy.AppRegistrationResponse{
				AppID:        "app-001",
				AppVersionID: "appver-001",
				AppSpec:      json.RawMessage(`{"kind":"AIApp","source":"registration"}`),
			},
			AppSpec: json.RawMessage(`{"kind":"AIApp","source":"workflow"}`),
		}
		run.PartialResult = &PartialResult{
			ArtifactURI:  "file:///packages/app.tar.gz",
			ArchiveName:  "app.tar.gz",
			Checksum:     "sha256:package",
			AppID:        "app-001",
			AppVersionID: "appver-001",
		}
		return nil
	})
	if err != nil {
		t.Fatalf("update run: %v", err)
	}

	updated.Manifest.Spec.Parameters["labels"].(map[string]any)["team"] = "mutated"
	updated.Manifest.Spec.Requirements.CostPolicy = "mutated"
	updated.Manifest.Spec.Requirements.SLO["latency"].(map[string]any)["p99_ms"] = 999
	updated.Manifest.Spec.Requirements.Labels["team"] = "mutated"
	updated.Generation.Manifest.Spec.Requirements.CostPolicy = "mutated"
	updated.Generation.Manifest.Spec.Requirements.SLO["throughput"].(map[string]any)["rps"] = 999
	updated.Generation.Manifest.Spec.Requirements.Labels["source"] = "mutated"
	updated.CorrelationIDs[0] = "mutated"
	updated.Stages[0].Status = "mutated"
	updated.Execution.Proposal["parameters"].(map[string]any)["decision"] = "mutated"
	updated.Execution.Result["review"].(map[string]any)["status"] = "mutated"
	updated.Execution.Evidence["source"] = "mutated"
	updated.Application.Package.ArtifactURI = "mutated"
	updated.Application.Package.AppSpec[0] = 'x'
	updated.Application.Registration.AppID = "mutated"
	updated.Application.Registration.AppSpec[0] = 'x'
	updated.Application.AppSpec[0] = 'x'
	updated.PartialResult.AppVersionID = "mutated"

	loaded, ok := store.Get(created.RunID)
	if !ok {
		t.Fatal("expected created run")
	}
	if got := loaded.Manifest.Spec.Parameters["labels"].(map[string]any)["team"]; got != "aiops" {
		t.Fatalf("manifest parameters leaked mutable state: %v", got)
	}
	assertStoredRequirementsAreIsolated(t, loaded)
	listed := store.List()
	if len(listed) != 1 {
		t.Fatalf("expected one listed Run, got %#v", listed)
	}
	assertStoredRequirementsAreIsolated(t, listed[0])
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
	if loaded.Application.Package.ArtifactURI != "file:///packages/app.tar.gz" ||
		string(loaded.Application.Package.AppSpec) != `{"kind":"AIApp","source":"package"}` ||
		loaded.Application.Registration.AppID != "app-001" ||
		string(loaded.Application.Registration.AppSpec) != `{"kind":"AIApp","source":"registration"}` ||
		string(loaded.Application.AppSpec) != `{"kind":"AIApp","source":"workflow"}` {
		t.Fatalf("application evidence leaked mutable state: %#v", loaded.Application)
	}
	if loaded.PartialResult.AppVersionID != "appver-001" {
		t.Fatalf("partial result leaked mutable state: %#v", loaded.PartialResult)
	}
}

func assertStoredRequirementsAreIsolated(t *testing.T, run Run) {
	t.Helper()
	manifest := run.Manifest.Spec.Requirements
	if manifest == nil ||
		manifest.CostPolicy != "min_cost" ||
		manifest.SLO["latency"].(map[string]any)["p99_ms"] != 250 ||
		manifest.Labels["team"] != "aiops" {
		t.Fatalf("Run Manifest requirements leaked mutable state: %#v", manifest)
	}
	generation := run.Generation.Manifest.Spec.Requirements
	if generation == nil ||
		generation.CostPolicy != "min_cost" ||
		generation.SLO["throughput"].(map[string]any)["rps"] != 20 ||
		generation.Labels["source"] != "generation" {
		t.Fatalf("Generation Manifest requirements leaked mutable state: %#v", generation)
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
