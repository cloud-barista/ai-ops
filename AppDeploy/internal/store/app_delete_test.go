package store

import (
	"context"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

func TestMemoryDeleteAppRemovesIndexesAndAllowsReregistration(t *testing.T) {
	ctx := context.Background()
	repo := NewMemory()
	artifactPath := filepath.Join(t.TempDir(), "run.sh")
	if err := os.WriteFile(artifactPath, []byte("#!/usr/bin/env sh\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	app := appForDeleteTest("app-memory-delete", "appver-memory-delete", artifactPath)
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}

	deleted, err := repo.DeleteApp(ctx, app.AppID)
	if err != nil {
		t.Fatal(err)
	}
	if deleted.AppID != app.AppID || deleted.AppVersionID != app.AppVersionID {
		t.Fatalf("deleted app = %+v, want app_id=%s app_version_id=%s", deleted, app.AppID, app.AppVersionID)
	}
	if _, ok := repo.appsByID[app.AppID]; ok {
		t.Fatalf("appsByID still contains %s", app.AppID)
	}
	if _, ok := repo.appsByVersionID[app.AppVersionID]; ok {
		t.Fatalf("appsByVersionID still contains %s", app.AppVersionID)
	}
	if _, ok := repo.appNameVersion[app.Name+":"+app.Version]; ok {
		t.Fatalf("appNameVersion still contains %s:%s", app.Name, app.Version)
	}
	assertAppStoreError(t, mustGetAppError(repo.GetApp(ctx, app.AppID)), "NOT_FOUND", http.StatusNotFound)
	assertAppStoreError(t, mustGetAppError(repo.GetAppByVersionID(ctx, app.AppVersionID)), "NOT_FOUND", http.StatusNotFound)
	exists, err := repo.ExistsNameVersion(ctx, app.Name, app.Version)
	if err != nil {
		t.Fatal(err)
	}
	if exists {
		t.Fatalf("name/version %s:%s still exists", app.Name, app.Version)
	}
	if _, err := os.Stat(artifactPath); err != nil {
		t.Fatalf("artifact should be retained: %v", err)
	}

	replacement := app
	replacement.AppID = "app-memory-replacement"
	replacement.AppVersionID = "appver-memory-replacement"
	if err := repo.CreateApp(ctx, replacement); err != nil {
		t.Fatalf("re-register same name/version: %v", err)
	}
}

func TestMemoryDeleteAppMissingReturnsNotFound(t *testing.T) {
	_, err := NewMemory().DeleteApp(context.Background(), "app-missing")
	assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
}

func TestMemoryDeleteAppAllowsStoppedDeploymentHistory(t *testing.T) {
	ctx := context.Background()
	repo := NewMemory()
	app := appForDeleteTest("app-memory-stopped", "appver-memory-stopped", "")
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"dep-memory-stopped-1", "dep-memory-stopped-2"} {
		if err := repo.CreateDeployment(ctx, deploymentForDeleteTest(id, app, model.StatusStopped)); err != nil {
			t.Fatal(err)
		}
	}

	if _, err := repo.DeleteApp(ctx, app.AppID); err != nil {
		t.Fatalf("delete app with only STOPPED history: %v", err)
	}
	assertAppStoreError(t, mustGetAppError(repo.GetApp(ctx, app.AppID)), "NOT_FOUND", http.StatusNotFound)
	if _, ok := repo.appsByVersionID[app.AppVersionID]; ok {
		t.Fatalf("appsByVersionID still contains %s", app.AppVersionID)
	}
	if _, ok := repo.appNameVersion[app.Name+":"+app.Version]; ok {
		t.Fatal("name/version index remains after deletion")
	}
	for _, id := range []string{"dep-memory-stopped-1", "dep-memory-stopped-2"} {
		deployment, err := repo.GetDeployment(ctx, id)
		if err != nil {
			t.Fatalf("STOPPED deployment %s was removed: %v", id, err)
		}
		if deployment.Status != model.StatusStopped {
			t.Fatalf("deployment %s status = %s, want %s", id, deployment.Status, model.StatusStopped)
		}
	}
}

func TestMemoryDeleteAppRejectsNonStoppedDeploymentHistory(t *testing.T) {
	for _, status := range []string{model.StatusRunning, model.StatusRequested, model.StatusValidationFailed} {
		t.Run(status, func(t *testing.T) {
			ctx := context.Background()
			repo := NewMemory()
			app := appForDeleteTest("app-memory-"+status, "appver-memory-"+status, "")
			if err := repo.CreateApp(ctx, app); err != nil {
				t.Fatal(err)
			}
			deployment := deploymentForDeleteTest("dep-memory-"+status, app, status)
			if err := repo.CreateDeployment(ctx, deployment); err != nil {
				t.Fatal(err)
			}

			_, err := repo.DeleteApp(ctx, app.AppID)
			assertAppStoreError(t, err, model.ErrAppSpecInvalid, http.StatusConflict)
			assertDeploymentCountDetail(t, err, 1)

			if _, err := repo.GetApp(ctx, app.AppID); err != nil {
				t.Fatalf("referenced app was removed: %v", err)
			}
			gotDeployment, err := repo.GetDeployment(ctx, deployment.DeploymentID)
			if err != nil {
				t.Fatalf("deployment history was removed: %v", err)
			}
			if gotDeployment.Status != status {
				t.Fatalf("deployment status = %s, want %s", gotDeployment.Status, status)
			}
			if repo.appNameVersion[app.Name+":"+app.Version] != app.AppVersionID {
				t.Fatal("name/version index changed after rejected deletion")
			}
		})
	}
}

func TestMemoryDeleteAppRejectsMixedStoppedAndRunningHistory(t *testing.T) {
	ctx := context.Background()
	repo := NewMemory()
	app := appForDeleteTest("app-memory-mixed", "appver-memory-mixed", "")
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	stopped := deploymentForDeleteTest("dep-memory-mixed-stopped", app, model.StatusStopped)
	running := deploymentForDeleteTest("dep-memory-mixed-running", app, model.StatusRunning)
	if err := repo.CreateDeployment(ctx, stopped); err != nil {
		t.Fatal(err)
	}
	if err := repo.CreateDeployment(ctx, running); err != nil {
		t.Fatal(err)
	}

	_, err := repo.DeleteApp(ctx, app.AppID)
	assertAppStoreError(t, err, model.ErrAppSpecInvalid, http.StatusConflict)
	assertDeploymentCountDetail(t, err, 1)
	if _, err := repo.GetApp(ctx, app.AppID); err != nil {
		t.Fatalf("app was removed despite RUNNING reference: %v", err)
	}
	for _, id := range []string{stopped.DeploymentID, running.DeploymentID} {
		if _, err := repo.GetDeployment(ctx, id); err != nil {
			t.Fatalf("deployment %s was removed after rejected deletion: %v", id, err)
		}
	}
}

func TestFileDeleteAppPersistsAndRetainsArtifact(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	storePath := filepath.Join(dir, "store.json")
	artifactPath := filepath.Join(dir, "package.tar.gz")
	artifactContent := []byte("package-content")
	if err := os.WriteFile(artifactPath, artifactContent, 0o600); err != nil {
		t.Fatal(err)
	}

	first, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	app := appForDeleteTest("app-file-delete", "appver-file-delete", artifactPath)
	if err := first.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	deleted, err := first.DeleteApp(ctx, app.AppID)
	if err != nil {
		t.Fatal(err)
	}
	if deleted.AppID != app.AppID || deleted.AppVersionID != app.AppVersionID {
		t.Fatalf("deleted app = %+v", deleted)
	}

	second, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := second.data.AppsByID[app.AppID]; ok {
		t.Fatalf("persisted AppsByID still contains %s", app.AppID)
	}
	if _, ok := second.data.AppsByVersionID[app.AppVersionID]; ok {
		t.Fatalf("persisted AppsByVersionID still contains %s", app.AppVersionID)
	}
	if _, ok := second.data.AppNameVersion[app.Name+":"+app.Version]; ok {
		t.Fatalf("persisted AppNameVersion still contains %s:%s", app.Name, app.Version)
	}
	assertAppStoreError(t, mustGetAppError(second.GetApp(ctx, app.AppID)), "NOT_FOUND", http.StatusNotFound)
	assertAppStoreError(t, mustGetAppError(second.GetAppByVersionID(ctx, app.AppVersionID)), "NOT_FOUND", http.StatusNotFound)

	gotArtifact, err := os.ReadFile(artifactPath)
	if err != nil {
		t.Fatalf("artifact should be retained: %v", err)
	}
	if string(gotArtifact) != string(artifactContent) {
		t.Fatalf("artifact content = %q, want %q", gotArtifact, artifactContent)
	}

	replacement := app
	replacement.AppID = "app-file-replacement"
	replacement.AppVersionID = "appver-file-replacement"
	if err := second.CreateApp(ctx, replacement); err != nil {
		t.Fatalf("re-register same name/version: %v", err)
	}
	third, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	gotReplacement, err := third.GetApp(ctx, replacement.AppID)
	if err != nil {
		t.Fatalf("re-registered app was not persisted: %v", err)
	}
	if gotReplacement.AppVersionID != replacement.AppVersionID {
		t.Fatalf("app_version_id = %s, want %s", gotReplacement.AppVersionID, replacement.AppVersionID)
	}
}

func TestFileDeleteAppMissingReturnsNotFound(t *testing.T) {
	repo, err := NewFile(filepath.Join(t.TempDir(), "store.json"))
	if err != nil {
		t.Fatal(err)
	}
	_, err = repo.DeleteApp(context.Background(), "app-missing")
	assertAppStoreError(t, err, "NOT_FOUND", http.StatusNotFound)
}

func TestFileDeleteAppAllowsStoppedDeploymentHistoryAndPreservesRecords(t *testing.T) {
	ctx := context.Background()
	storePath := filepath.Join(t.TempDir(), "store.json")
	repo, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	app := appForDeleteTest("app-file-stopped", "appver-file-stopped", "")
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"dep-file-stopped-1", "dep-file-stopped-2"} {
		if err := repo.CreateDeployment(ctx, deploymentForDeleteTest(id, app, model.StatusStopped)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := repo.DeleteApp(ctx, app.AppID); err != nil {
		t.Fatalf("delete app with only STOPPED history: %v", err)
	}

	reopened, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	assertAppStoreError(t, mustGetAppError(reopened.GetApp(ctx, app.AppID)), "NOT_FOUND", http.StatusNotFound)
	if _, ok := reopened.data.AppsByVersionID[app.AppVersionID]; ok {
		t.Fatalf("persisted AppsByVersionID still contains %s", app.AppVersionID)
	}
	if _, ok := reopened.data.AppNameVersion[app.Name+":"+app.Version]; ok {
		t.Fatal("persisted name/version index remains after deletion")
	}
	for _, id := range []string{"dep-file-stopped-1", "dep-file-stopped-2"} {
		deployment, err := reopened.GetDeployment(ctx, id)
		if err != nil {
			t.Fatalf("STOPPED deployment %s was not persisted: %v", id, err)
		}
		if deployment.Status != model.StatusStopped {
			t.Fatalf("deployment %s status = %s, want %s", id, deployment.Status, model.StatusStopped)
		}
	}
}

func TestFileDeleteAppRejectsNonStoppedDeploymentHistoryAndPreservesRecords(t *testing.T) {
	for _, status := range []string{model.StatusRunning, model.StatusRequested, model.StatusDeploymentFailed} {
		t.Run(status, func(t *testing.T) {
			ctx := context.Background()
			storePath := filepath.Join(t.TempDir(), "store.json")
			repo, err := NewFile(storePath)
			if err != nil {
				t.Fatal(err)
			}
			app := appForDeleteTest("app-file-"+status, "appver-file-"+status, "")
			if err := repo.CreateApp(ctx, app); err != nil {
				t.Fatal(err)
			}
			deployment := deploymentForDeleteTest("dep-file-"+status, app, status)
			if err := repo.CreateDeployment(ctx, deployment); err != nil {
				t.Fatal(err)
			}

			_, err = repo.DeleteApp(ctx, app.AppID)
			assertAppStoreError(t, err, model.ErrAppSpecInvalid, http.StatusConflict)
			assertDeploymentCountDetail(t, err, 1)

			reopened, err := NewFile(storePath)
			if err != nil {
				t.Fatal(err)
			}
			gotApp, err := reopened.GetApp(ctx, app.AppID)
			if err != nil {
				t.Fatalf("referenced app was removed: %v", err)
			}
			if gotApp.AppVersionID != app.AppVersionID {
				t.Fatalf("app_version_id = %s, want %s", gotApp.AppVersionID, app.AppVersionID)
			}
			gotDeployment, err := reopened.GetDeployment(ctx, deployment.DeploymentID)
			if err != nil {
				t.Fatalf("deployment history was removed: %v", err)
			}
			if gotDeployment.Status != status {
				t.Fatalf("deployment status = %s, want %s", gotDeployment.Status, status)
			}
			if reopened.data.AppNameVersion[app.Name+":"+app.Version] != app.AppVersionID {
				t.Fatal("persisted name/version index changed after rejected deletion")
			}
		})
	}
}

func TestFileDeleteAppRejectsMixedStoppedAndRunningHistory(t *testing.T) {
	ctx := context.Background()
	storePath := filepath.Join(t.TempDir(), "store.json")
	repo, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	app := appForDeleteTest("app-file-mixed", "appver-file-mixed", "")
	if err := repo.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	stopped := deploymentForDeleteTest("dep-file-mixed-stopped", app, model.StatusStopped)
	running := deploymentForDeleteTest("dep-file-mixed-running", app, model.StatusRunning)
	if err := repo.CreateDeployment(ctx, stopped); err != nil {
		t.Fatal(err)
	}
	if err := repo.CreateDeployment(ctx, running); err != nil {
		t.Fatal(err)
	}

	_, err = repo.DeleteApp(ctx, app.AppID)
	assertAppStoreError(t, err, model.ErrAppSpecInvalid, http.StatusConflict)
	assertDeploymentCountDetail(t, err, 1)

	reopened, err := NewFile(storePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := reopened.GetApp(ctx, app.AppID); err != nil {
		t.Fatalf("app was removed despite RUNNING reference: %v", err)
	}
	for _, id := range []string{stopped.DeploymentID, running.DeploymentID} {
		if _, err := reopened.GetDeployment(ctx, id); err != nil {
			t.Fatalf("deployment %s was not preserved: %v", id, err)
		}
	}
}

func appForDeleteTest(appID, appVersionID, artifactPath string) model.AppResponse {
	artifactURI := "file:///tmp/app-delete-test.sh"
	if artifactPath != "" {
		artifactURI = "file://" + filepath.ToSlash(artifactPath)
	}
	return model.AppResponse{
		AppID:        appID,
		AppVersionID: appVersionID,
		Name:         "delete-test-app",
		Version:      "1.0.0",
		AppSpec: model.AppSpec{
			SchemaVersion: "appspec.khu.ai/v1alpha1",
			Kind:          "AIApp",
			Metadata:      model.Metadata{Name: "delete-test-app", Version: "1.0.0"},
			Artifact:      model.Artifact{Type: "script", URI: artifactURI},
			Entrypoint:    model.Entrypoint{Command: "sh"},
			Runtime:       model.AppRuntime{Type: "cpu", Accelerator: "none"},
			Resources:     model.Resources{CPU: "1", Memory: "1Gi", GPU: "0", Storage: "1Gi"},
		},
		CreatedAt: time.Now().UTC(),
	}
}

func deploymentForDeleteTest(deploymentID string, app model.AppResponse, status string) model.DeploymentResponse {
	now := time.Now().UTC()
	return model.DeploymentResponse{
		DeploymentID: deploymentID,
		AppID:        app.AppID,
		AppVersionID: app.AppVersionID,
		Status:       status,
		CreatedAt:    now,
		UpdatedAt:    now,
	}
}

func mustGetAppError(_ model.AppResponse, err error) error {
	return err
}

func assertAppStoreError(t *testing.T, err error, code string, status int) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected %s error", code)
	}
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		t.Fatalf("error type = %T, want *errors.AppError: %v", err, err)
	}
	if appErr.Code != code || appErr.HTTPStatus != status {
		t.Fatalf("error = code %s status %d, want code %s status %d", appErr.Code, appErr.HTTPStatus, code, status)
	}
}

func assertDeploymentCountDetail(t *testing.T, err error, want int) {
	t.Helper()
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		t.Fatalf("error type = %T, want *errors.AppError: %v", err, err)
	}
	count, ok := appErr.Details["deployment_count"].(int)
	if !ok || count != want {
		t.Fatalf("deployment_count = %#v, want %d", appErr.Details["deployment_count"], want)
	}
}
