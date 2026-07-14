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

type profileDeleteRepository interface {
	ProfileRepository
	DeploymentRepository
	MetricRepository
}

type profileDeleteRepositoryFactory struct {
	name string
	open func(t *testing.T) (profileDeleteRepository, func() profileDeleteRepository)
}

func profileDeleteRepositoryFactories() []profileDeleteRepositoryFactory {
	return []profileDeleteRepositoryFactory{
		{
			name: "memory",
			open: func(t *testing.T) (profileDeleteRepository, func() profileDeleteRepository) {
				t.Helper()
				repo := NewMemory()
				return repo, func() profileDeleteRepository { return repo }
			},
		},
		{
			name: "file",
			open: func(t *testing.T) (profileDeleteRepository, func() profileDeleteRepository) {
				t.Helper()
				path := filepath.Join(t.TempDir(), "store.json")
				repo, err := NewFile(path)
				if err != nil {
					t.Fatal(err)
				}
				return repo, func() profileDeleteRepository {
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

func TestProfileDeleteAllowsStoppedHistoryAndPreservesEvidence(t *testing.T) {
	for _, factory := range profileDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			runtimeProfile, targetProfile := profilesForDeleteTest("stopped")
			if err := repo.CreateRuntimeProfile(ctx, runtimeProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.CreateTargetProfile(ctx, targetProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.SaveInventory(ctx, inventoryForDeleteTest(targetProfile.TargetProfileID)); err != nil {
				t.Fatal(err)
			}

			deployment := deploymentForProfileDeleteTest("dep-profile-stopped", runtimeProfile.RuntimeProfileID, targetProfile.TargetProfileID, model.StatusStopped)
			if err := repo.CreateDeployment(ctx, deployment); err != nil {
				t.Fatal(err)
			}
			event := model.DeploymentEvent{
				EventID:      "event-profile-stopped",
				DeploymentID: deployment.DeploymentID,
				Stage:        model.StatusStopped,
				Message:      "stopped",
				Timestamp:    time.Now().UTC(),
			}
			if err := repo.AddEvent(ctx, event); err != nil {
				t.Fatal(err)
			}
			metric := model.InferenceMetricRecord{
				MetricID:     "metric-profile-stopped",
				DeploymentID: deployment.DeploymentID,
				Timestamp:    time.Now().UTC(),
				LatencyMS:    12.5,
			}
			if err := repo.AddMetric(ctx, metric); err != nil {
				t.Fatal(err)
			}

			deletedRuntime, err := repo.DeleteRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID)
			if err != nil {
				t.Fatal(err)
			}
			if deletedRuntime.RuntimeProfileID != runtimeProfile.RuntimeProfileID || deletedRuntime.Name != runtimeProfile.Name {
				t.Fatalf("deleted runtime profile = %+v", deletedRuntime)
			}
			deletedTarget, inventoryDeleted, err := repo.DeleteTargetProfile(ctx, targetProfile.TargetProfileID)
			if err != nil {
				t.Fatal(err)
			}
			if deletedTarget.TargetProfileID != targetProfile.TargetProfileID || deletedTarget.Name != targetProfile.Name || !inventoryDeleted {
				t.Fatalf("deleted target profile = %+v inventory_deleted=%v", deletedTarget, inventoryDeleted)
			}

			persisted := reopen()
			_, err = persisted.GetRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
			_, err = persisted.GetTargetProfile(ctx, targetProfile.TargetProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
			assertInventoryMissing(t, persisted, targetProfile.TargetProfileID)

			gotDeployment, err := persisted.GetDeployment(ctx, deployment.DeploymentID)
			if err != nil {
				t.Fatalf("STOPPED deployment was removed: %v", err)
			}
			if gotDeployment.Status != model.StatusStopped {
				t.Fatalf("deployment status = %s, want %s", gotDeployment.Status, model.StatusStopped)
			}
			events, err := persisted.ListEvents(ctx, deployment.DeploymentID, "")
			if err != nil || len(events) != 1 || events[0].EventID != event.EventID {
				t.Fatalf("events were not preserved: items=%+v err=%v", events, err)
			}
			metrics, err := persisted.ListMetrics(ctx, deployment.DeploymentID)
			if err != nil || len(metrics) != 1 || metrics[0].MetricID != metric.MetricID {
				t.Fatalf("metrics were not preserved: items=%+v err=%v", metrics, err)
			}

			_, err = persisted.DeleteRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
			_, _, err = persisted.DeleteTargetProfile(ctx, targetProfile.TargetProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
		})
	}
}

func TestProfileDeleteReportsInventoryPresence(t *testing.T) {
	for _, factory := range profileDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			_, targetWithoutInventory := profilesForDeleteTest("without-inventory")
			if err := repo.CreateTargetProfile(ctx, targetWithoutInventory); err != nil {
				t.Fatal(err)
			}
			_, inventoryDeleted, err := repo.DeleteTargetProfile(ctx, targetWithoutInventory.TargetProfileID)
			if err != nil {
				t.Fatal(err)
			}
			if inventoryDeleted {
				t.Fatal("inventory_deleted = true without a current snapshot")
			}

			persisted := reopen()
			_, err = persisted.GetTargetProfile(ctx, targetWithoutInventory.TargetProfileID)
			assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
		})
	}
}

func TestProfileDeleteRejectsAnyNonStoppedDeployment(t *testing.T) {
	for _, factory := range profileDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			runtimeProfile, targetProfile := profilesForDeleteTest("blocked")
			if err := repo.CreateRuntimeProfile(ctx, runtimeProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.CreateTargetProfile(ctx, targetProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.SaveInventory(ctx, inventoryForDeleteTest(targetProfile.TargetProfileID)); err != nil {
				t.Fatal(err)
			}

			statuses := []string{model.StatusStopped, model.StatusRunning, model.StatusValidationFailed}
			for index, status := range statuses {
				deployment := deploymentForProfileDeleteTest(
					"dep-profile-blocked-"+string(rune('a'+index)),
					runtimeProfile.RuntimeProfileID,
					targetProfile.TargetProfileID,
					status,
				)
				if err := repo.CreateDeployment(ctx, deployment); err != nil {
					t.Fatal(err)
				}
			}

			_, err := repo.DeleteRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID)
			assertAppStoreError(t, err, model.ErrRuntimeProfileInvalid, http.StatusConflict)
			assertDeploymentCountDetail(t, err, 2)
			_, _, err = repo.DeleteTargetProfile(ctx, targetProfile.TargetProfileID)
			assertAppStoreError(t, err, model.ErrTargetProfileInvalid, http.StatusConflict)
			assertDeploymentCountDetail(t, err, 2)

			persisted := reopen()
			if _, err := persisted.GetRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID); err != nil {
				t.Fatalf("blocked runtime profile was removed: %v", err)
			}
			if _, err := persisted.GetTargetProfile(ctx, targetProfile.TargetProfileID); err != nil {
				t.Fatalf("blocked target profile was removed: %v", err)
			}
			assertInventoryPresent(t, persisted, targetProfile.TargetProfileID)
		})
	}
}

func TestProfileDeleteHonorsCanceledContext(t *testing.T) {
	for _, factory := range profileDeleteRepositoryFactories() {
		factory := factory
		t.Run(factory.name, func(t *testing.T) {
			ctx := context.Background()
			repo, reopen := factory.open(t)
			runtimeProfile, targetProfile := profilesForDeleteTest("canceled")
			if err := repo.CreateRuntimeProfile(ctx, runtimeProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.CreateTargetProfile(ctx, targetProfile); err != nil {
				t.Fatal(err)
			}
			if err := repo.SaveInventory(ctx, inventoryForDeleteTest(targetProfile.TargetProfileID)); err != nil {
				t.Fatal(err)
			}

			canceled, cancel := context.WithCancel(ctx)
			cancel()
			if _, err := repo.DeleteRuntimeProfile(canceled, runtimeProfile.RuntimeProfileID); !errors.Is(err, context.Canceled) {
				t.Fatalf("runtime delete error = %v, want context.Canceled", err)
			}
			if _, _, err := repo.DeleteTargetProfile(canceled, targetProfile.TargetProfileID); !errors.Is(err, context.Canceled) {
				t.Fatalf("target delete error = %v, want context.Canceled", err)
			}

			persisted := reopen()
			if _, err := persisted.GetRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID); err != nil {
				t.Fatalf("runtime profile changed after canceled delete: %v", err)
			}
			if _, err := persisted.GetTargetProfile(ctx, targetProfile.TargetProfileID); err != nil {
				t.Fatalf("target profile changed after canceled delete: %v", err)
			}
			assertInventoryPresent(t, persisted, targetProfile.TargetProfileID)
		})
	}
}

func TestFileProfileDeleteRollsBackWhenPersistenceFails(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	storePath := filepath.Join(dir, "store.json")
	repo, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	runtimeProfile, targetProfile := profilesForDeleteTest("rollback")
	if err := repo.CreateRuntimeProfile(ctx, runtimeProfile); err != nil {
		t.Fatal(err)
	}
	if err := repo.CreateTargetProfile(ctx, targetProfile); err != nil {
		t.Fatal(err)
	}
	if err := repo.SaveInventory(ctx, inventoryForDeleteTest(targetProfile.TargetProfileID)); err != nil {
		t.Fatal(err)
	}

	blocker := filepath.Join(dir, "not-a-directory")
	if err := os.WriteFile(blocker, []byte("block"), 0o600); err != nil {
		t.Fatal(err)
	}
	repo.path = filepath.Join(blocker, "store.json")

	if _, err := repo.DeleteRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID); err == nil {
		t.Fatal("runtime delete unexpectedly succeeded when persistence failed")
	}
	if _, _, err := repo.DeleteTargetProfile(ctx, targetProfile.TargetProfileID); err == nil {
		t.Fatal("target delete unexpectedly succeeded when persistence failed")
	}
	if _, err := repo.GetRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID); err != nil {
		t.Fatalf("runtime profile was not rolled back: %v", err)
	}
	if _, err := repo.GetTargetProfile(ctx, targetProfile.TargetProfileID); err != nil {
		t.Fatalf("target profile was not rolled back: %v", err)
	}
	assertInventoryPresent(t, repo, targetProfile.TargetProfileID)

	persisted, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := persisted.GetRuntimeProfile(ctx, runtimeProfile.RuntimeProfileID); err != nil {
		t.Fatalf("persisted runtime profile changed after rollback: %v", err)
	}
	if _, err := persisted.GetTargetProfile(ctx, targetProfile.TargetProfileID); err != nil {
		t.Fatalf("persisted target profile changed after rollback: %v", err)
	}
	assertInventoryPresent(t, persisted, targetProfile.TargetProfileID)
}

func profilesForDeleteTest(suffix string) (model.RuntimeProfile, model.TargetProfile) {
	runtimeID := "runtime-delete-" + suffix
	targetID := "target-delete-" + suffix
	return model.RuntimeProfile{
			RuntimeProfileID: runtimeID,
			Name:             "runtime " + suffix,
			RuntimeType:      "mock",
			AdapterType:      "mock",
			OperatingMode:    "local_mock",
		}, model.TargetProfile{
			TargetProfileID: targetID,
			Name:            "target " + suffix,
			CSP:             "mock",
			Runtime:         model.TargetRuntime{RuntimeType: "mock", OperatingMode: "local_mock"},
		}
}

func deploymentForProfileDeleteTest(id, runtimeID, targetID, status string) model.DeploymentResponse {
	now := time.Now().UTC()
	return model.DeploymentResponse{
		DeploymentID:     id,
		AppVersionID:     "appver-profile-delete",
		RuntimeProfileID: runtimeID,
		TargetProfileID:  targetID,
		Status:           status,
		CreatedAt:        now,
		UpdatedAt:        now,
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
