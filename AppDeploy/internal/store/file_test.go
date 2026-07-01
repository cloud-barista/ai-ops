package store

import (
	"context"
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
	if err := first.CreateRuntimeProfile(ctx, model.RuntimeProfile{
		RuntimeProfileID: "rt-cpu-001",
		RuntimeType:      "cpu",
		Accelerator:      "none",
		AdapterType:      "cpu_vm",
		OperatingMode:    "vm_process",
	}); err != nil {
		t.Fatal(err)
	}
	deployment := model.DeploymentResponse{
		DeploymentID:     "dep-001",
		AppVersionID:     "appver-001",
		RuntimeProfileID: "rt-cpu-001",
		TargetProfileID:  "target-cpu-001",
		Status:           model.StatusRunning,
		CreatedAt:        time.Now().UTC(),
		UpdatedAt:        time.Now().UTC(),
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
