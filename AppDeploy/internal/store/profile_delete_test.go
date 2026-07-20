package store

import (
	"context"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/khu/ai-app-deployer/internal/model"
)

type targetDeleteRepository interface {
	ProfileRepository
	DeploymentRepository
	MetricRepository
}

type targetDeleteRepositoryFactory struct {
	name string
	open func(t *testing.T) (targetDeleteRepository, func() targetDeleteRepository)
}

func targetDeleteRepositoryFactories() []targetDeleteRepositoryFactory {
	return []targetDeleteRepositoryFactory{
		{
			name: "memory",
			open: func(t *testing.T) (targetDeleteRepository, func() targetDeleteRepository) {
				t.Helper()
				repo := NewMemory()
				return repo, func() targetDeleteRepository { return repo }
			},
		},
		{
			name: "file",
			open: func(t *testing.T) (targetDeleteRepository, func() targetDeleteRepository) {
				t.Helper()
				path := filepath.Join(t.TempDir(), "store.json")
				repo, err := NewFile(path)
				if err != nil {
					t.Fatal(err)
				}
				return repo, func() targetDeleteRepository {
					reopened, err := NewFile(path)
					if err != nil {
						t.Fatal(err)
					}
					return reopened
				}
			},
		},
	}
}

func TestTargetDeleteAllowsStoppedHistoryAndPreservesEvidence(t *testing.T) {
	for _, factory := range targetDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			targetProfile := targetForDeleteTest("stopped")
			if err := repo.CreateTargetProfile(ctx, targetProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.SaveInventory(ctx, inventoryForDeleteTest(targetProfile.TargetProfileID)); err != nil {
				t.Fatal(err)
			}

			deployment := deploymentForTargetDeleteTest("dep-target-stopped", targetProfile.TargetProfileID, model.StatusStopped)
			if err := repo.CreateDeployment(ctx, deployment); err != nil {
				t.Fatal(err)
			}
			event := model.DeploymentEvent{
				EventID:      "event-target-stopped",
				DeploymentID: deployment.DeploymentID,
				Stage:        model.StatusStopped,
				Message:      "stopped",
				Timestamp:    time.Now().UTC(),
			}
			if err := repo.AddEvent(ctx, event); err != nil {
				t.Fatal(err)
			}
			metric := model.InferenceMetricRecord{
				MetricID:     "metric-target-stopped",
				DeploymentID: deployment.DeploymentID,
				Timestamp:    time.Now().UTC(),
				LatencyMS:    12.5,
			}
			if err := repo.AddMetric(ctx, metric); err != nil {
				t.Fatal(err)
			}

			deletedTarget, inventoryDeleted, err := repo.DeleteTargetProfile(ctx, targetProfile.TargetProfileID)
			if err != nil {
				t.Fatal(err)
			}
			if deletedTarget.TargetProfileID != targetProfile.TargetProfileID || deletedTarget.Name != targetProfile.Name || !inventoryDeleted {
				t.Fatalf("deleted target profile = %+v inventory_deleted=%v", deletedTarget, inventoryDeleted)
			}

			persisted := reopen()
			_, err = persisted.GetTargetProfile(ctx, targetProfile.TargetProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
			assertInventoryMissing(t, persisted, targetProfile.TargetProfileID)

			gotDeployment, err := persisted.GetDeployment(ctx, deployment.DeploymentID)
			if err != nil || gotDeployment.Status != model.StatusStopped {
				t.Fatalf("STOPPED deployment was not preserved: %+v err=%v", gotDeployment, err)
			}
			events, err := persisted.ListEvents(ctx, deployment.DeploymentID, "")
			if err != nil || len(events) != 1 || events[0].EventID != event.EventID {
				t.Fatalf("events were not preserved: items=%+v err=%v", events, err)
			}
			metrics, err := persisted.ListMetrics(ctx, deployment.DeploymentID)
			if err != nil || len(metrics) != 1 || metrics[0].MetricID != metric.MetricID {
				t.Fatalf("metrics were not preserved: items=%+v err=%v", metrics, err)
			}

			_, _, err = persisted.DeleteTargetProfile(ctx, targetProfile.TargetProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
		})
	}
}

func TestTargetDeleteReportsInventoryPresence(t *testing.T) {
	for _, factory := range targetDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			target := targetForDeleteTest("without-inventory")
			if err := repo.CreateTargetProfile(ctx, target); err != nil {
				t.Fatal(err)
			}
			_, inventoryDeleted, err := repo.DeleteTargetProfile(ctx, target.TargetProfileID)
			if err != nil {
				t.Fatal(err)
			}
			if inventoryDeleted {
				t.Fatal("inventory_deleted = true without a current snapshot")
			}

			persisted := reopen()
			_, err = persisted.GetTargetProfile(ctx, target.TargetProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
		})
	}
}

func TestTargetDeleteRejectsAnyNonStoppedDeployment(t *testing.T) {
	for _, factory := range targetDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			target := targetForDeleteTest("blocked")
			if err := repo.CreateTargetProfile(ctx, target); err != nil {
				t.Fatal(err)
			}
			if err := repo.SaveInventory(ctx, inventoryForDeleteTest(target.TargetProfileID)); err != nil {
				t.Fatal(err)
			}

			for index, status := range []string{model.StatusStopped, model.StatusRunning, model.StatusValidationFailed} {
				deployment := deploymentForTargetDeleteTest("dep-target-blocked-"+string(rune('a'+index)), target.TargetProfileID, status)
				if err := repo.CreateDeployment(ctx, deployment); err != nil {
					t.Fatal(err)
				}
			}

			_, _, err := repo.DeleteTargetProfile(ctx, target.TargetProfileID)
			assertAppStoreError(t, err, model.ErrTargetProfileInvalid, http.StatusConflict)
			assertDeploymentCountDetail(t, err, 2)

			persisted := reopen()
			if _, err := persisted.GetTargetProfile(ctx, target.TargetProfileID); err != nil {
				t.Fatalf("blocked target profile was removed: %v", err)
			}
			assertInventoryPresent(t, persisted, target.TargetProfileID)
		})
	}
}

func TestTargetDeleteHonorsCanceledContext(t *testing.T) {
	for _, factory := range targetDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			target := targetForDeleteTest("canceled")
			if err := repo.CreateTargetProfile(ctx, target); err != nil {
				t.Fatal(err)
			}
			if err := repo.SaveInventory(ctx, inventoryForDeleteTest(target.TargetProfileID)); err != nil {
				t.Fatal(err)
			}

			canceled, cancel := context.WithCancel(ctx)
			cancel()
			if _, _, err := repo.DeleteTargetProfile(canceled, target.TargetProfileID); !errors.Is(err, context.Canceled) {
				t.Fatalf("target delete error = %v, want context.Canceled", err)
			}

			persisted := reopen()
			if _, err := persisted.GetTargetProfile(ctx, target.TargetProfileID); err != nil {
				t.Fatalf("target profile changed after canceled delete: %v", err)
			}
			assertInventoryPresent(t, persisted, target.TargetProfileID)
		})
	}
}

func TestFileTargetDeleteRollsBackWhenPersistenceFails(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	storePath := filepath.Join(dir, "store.json")
	repo, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	target := targetForDeleteTest("rollback")
	if err := repo.CreateTargetProfile(ctx, target); err != nil {
		t.Fatal(err)
	}
	if err := repo.SaveInventory(ctx, inventoryForDeleteTest(target.TargetProfileID)); err != nil {
		t.Fatal(err)
	}

	blocker := filepath.Join(dir, "not-a-directory")
	if err := os.WriteFile(blocker, []byte("block"), 0o600); err != nil {
		t.Fatal(err)
	}
	repo.path = filepath.Join(blocker, "store.json")

	if _, _, err := repo.DeleteTargetProfile(ctx, target.TargetProfileID); err == nil {
		t.Fatal("target delete unexpectedly succeeded when persistence failed")
	}
	if _, err := repo.GetTargetProfile(ctx, target.TargetProfileID); err != nil {
		t.Fatalf("target profile was not rolled back: %v", err)
	}
	assertInventoryPresent(t, repo, target.TargetProfileID)

	persisted, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := persisted.GetTargetProfile(ctx, target.TargetProfileID); err != nil {
		t.Fatalf("persisted target profile changed after rollback: %v", err)
	}
	assertInventoryPresent(t, persisted, target.TargetProfileID)
}

func targetForDeleteTest(suffix string) model.TargetProfile {
	return model.TargetProfile{
		TargetProfileID: "target-delete-" + suffix,
		Name:            "target " + suffix,
		CSP:             "mock",
		Runtime:         model.TargetRuntime{RuntimeType: "mock", OperatingMode: "local_mock"},
	}
}

func deploymentForTargetDeleteTest(id, targetID, status string) model.DeploymentResponse {
	now := time.Now().UTC()
	return model.DeploymentResponse{
		DeploymentID:    id,
		AppVersionID:    "appver-profile-delete",
		TargetProfileID: targetID,
		Status:          status,
		CreatedAt:       now,
		UpdatedAt:       now,
	}
}

func inventoryForDeleteTest(targetID string) model.ResourceInventory {
	return model.ResourceInventory{
		TargetProfileID:  targetID,
		CPUAvailable:     true,
		MemoryAvailable:  true,
		StorageAvailable: true,
		RuntimeHealth:    "ok",
		LastCheckedAt:    time.Now().UTC(),
	}
}

func assertInventoryMissing(t *testing.T, repo DeploymentRepository, targetID string) {
	t.Helper()
	items, err := repo.ListInventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range items {
		if item.TargetProfileID == targetID {
			t.Fatalf("inventory for %s was not deleted", targetID)
		}
	}
}

func assertInventoryPresent(t *testing.T, repo DeploymentRepository, targetID string) {
	t.Helper()
	items, err := repo.ListInventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range items {
		if item.TargetProfileID == targetID {
			return
		}
	}
	t.Fatalf("inventory for %s is missing", targetID)
}
