package store

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestFileStorePersistsData(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "aiapp-store.json")

	first, err := NewFile(path)
	if err != nil {
		t.Fatal(err)
	}
	app := model.AppResponse{
		AppID:        "app-001",
		AppVersionID: "appver-001",
		Name:         "sample-app",
		Version:      "0.1.0",
		AppSpec: model.AppSpec{
			SchemaVersion: "appspec.khu.ai/v1alpha1",
			Kind:          "AIApp",
			Metadata:      model.Metadata{Name: "sample-app", Version: "0.1.0"},
			Artifact:      model.Artifact{Type: "script", URI: "file:///tmp/run.sh"},
			Entrypoint:    model.Entrypoint{Command: "sh"},
			Runtime:       model.AppRuntime{Type: "cpu", Accelerator: "none"},
			Resources:     model.Resources{CPU: "1", Memory: "1Gi", GPU: "0", Storage: "1Gi"},
		},
		CreatedAt: time.Now().UTC(),
	}
	if err := first.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	target := model.TargetProfile{
		TargetProfileID: "target-gpu-001",
		CSP:             "local",
		VM:              model.VMProfile{Host: "127.0.0.1", CredentialRef: "cred://local/gpu-001"},
		Runtime:         model.TargetRuntime{RuntimeType: "gpu", Accelerator: "nvidia", OperatingMode: "vm_process"},
		GPU:             &model.GPUProfile{Vendor: "nvidia", Count: 1, DriverRequired: true},
		Capacity:        model.ResourceCapacity{CPUCores: 4, MemoryBytes: 8 << 30, GPUCount: 1, StorageBytes: 20 << 30},
	}
	if err := first.CreateTargetProfile(ctx, target); err != nil {
		t.Fatal(err)
	}
	deployment := model.DeploymentResponse{
		DeploymentID:    "dep-001",
		AppVersionID:    "appver-001",
		TargetProfileID: "target-cpu-001",
		Status:          model.StatusRunning,
		CreatedAt:       time.Now().UTC(),
		UpdatedAt:       time.Now().UTC(),
	}
	if err := first.CreateDeployment(ctx, deployment); err != nil {
		t.Fatal(err)
	}
	if err := first.AddEvent(ctx, model.DeploymentEvent{
		EventID:      "evt-001",
		DeploymentID: "dep-001",
		Stage:        model.StatusRunning,
		Message:      "running",
		Timestamp:    time.Now().UTC(),
	}); err != nil {
		t.Fatal(err)
	}
	if err := first.AddMetric(ctx, model.InferenceMetricRecord{
		MetricID:      "metric-001",
		DeploymentID:  "dep-001",
		Timestamp:     time.Now().UTC(),
		LatencyMS:     42.5,
		ThroughputRPS: 12.25,
		QualityScore:  0.97,
		RequestCount:  100,
		ErrorCount:    1,
	}); err != nil {
		t.Fatal(err)
	}

	second, err := NewFile(path)
	if err != nil {
		t.Fatal(err)
	}
	gotApp, err := second.GetAppByVersionID(ctx, "appver-001")
	if err != nil {
		t.Fatal(err)
	}
	if gotApp.Name != "sample-app" {
		t.Fatalf("app name = %s", gotApp.Name)
	}
	gotTarget, err := second.GetTargetProfile(ctx, target.TargetProfileID)
	if err != nil {
		t.Fatal(err)
	}
	if gotTarget.Runtime.RuntimeType != "gpu" || gotTarget.GPU == nil || gotTarget.GPU.Count != 1 {
		t.Fatalf("target profile = %+v", gotTarget)
	}
	gotDeployment, err := second.GetDeployment(ctx, "dep-001")
	if err != nil {
		t.Fatal(err)
	}
	if gotDeployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %s", gotDeployment.Status)
	}
	events, err := second.ListEvents(ctx, "dep-001", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 {
		t.Fatalf("events = %d, want 1", len(events))
	}
	metrics, err := second.ListMetrics(ctx, "dep-001")
	if err != nil {
		t.Fatal(err)
	}
	if len(metrics) != 1 || metrics[0].MetricID != "metric-001" {
		t.Fatalf("metrics = %+v, want metric-001", metrics)
	}
}

func TestFileStoreRejectsDuplicateAppVersion(t *testing.T) {
	ctx := context.Background()
	store, err := NewFile(filepath.Join(t.TempDir(), "aiapp-store.json"))
	if err != nil {
		t.Fatal(err)
	}
	app := model.AppResponse{
		AppID:        "app-001",
		AppVersionID: "appver-001",
		Name:         "sample-app",
		Version:      "0.1.0",
	}
	if err := store.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	app.AppID = "app-002"
	app.AppVersionID = "appver-002"
	if err := store.CreateApp(ctx, app); err == nil {
		t.Fatal("expected duplicate app version error")
	}
}

func TestFileStorePreservesOriginalApplicationBytesAcrossReload(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "aiapp-store.json")
	raw := []byte(`{"kind":"AIApp","schema_version":"appspec.khu.ai/v1alpha1","metadata":{"version":"1","name":"raw-app"},"artifact":{"uri":"file:///tmp/run.sh","type":"script"},"entrypoint":{"command":"sh"},"runtime":{"type":"cpu"},"resources":{"cpu":"1"}}`)
	first, err := NewFile(path)
	if err != nil {
		t.Fatal(err)
	}
	app := model.AppResponse{AppID: "app-raw", AppVersionID: "appver-raw", Name: "raw-app", Version: "1", OriginalApplication: raw}
	if err := first.CreateApp(ctx, app); err != nil {
		t.Fatal(err)
	}
	second, err := NewFile(path)
	if err != nil {
		t.Fatal(err)
	}
	got, err := second.GetApp(ctx, app.AppID)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got.OriginalApplication, raw) {
		t.Fatalf("original application changed after reload: got %q want %q", got.OriginalApplication, raw)
	}
}

func TestFileStoreCreateTargetRollsBackWhenPersistenceFails(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	path := filepath.Join(dir, "store.json")
	store, err := NewFile(path)
	if err != nil {
		t.Fatal(err)
	}
	blocker := filepath.Join(dir, "not-a-directory")
	if err := os.WriteFile(blocker, []byte("block"), 0o600); err != nil {
		t.Fatal(err)
	}
	store.path = filepath.Join(blocker, "store.json")

	err = store.CreateTargetProfile(ctx, model.TargetProfile{
		TargetProfileID: "target-rollback",
		CSP:             "mock",
		Runtime:         model.TargetRuntime{RuntimeType: "mock", OperatingMode: "local_mock"},
	})
	if err == nil {
		t.Fatal("CreateTargetProfile unexpectedly succeeded")
	}
	if _, err := store.GetTargetProfile(ctx, "target-rollback"); err == nil {
		t.Fatal("target profile remained in memory after persistence failure")
	}
}
