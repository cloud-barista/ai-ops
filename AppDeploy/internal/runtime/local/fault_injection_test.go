package local

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
	"github.com/khu/ai-app-deployer/internal/runtime"
)

func TestFaultInjectorUsesRateSeedAndMaxFaults(t *testing.T) {
	injector := newFaultInjector(FaultInjectionConfig{
		Rate:      1,
		Seed:      42,
		MaxFaults: 2,
		Codes:     []string{FaultCodeGPUOOM, FaultCodeCUDAMismatch},
	})
	other := newFaultInjector(FaultInjectionConfig{
		Rate:      1,
		Seed:      42,
		MaxFaults: 2,
		Codes:     []string{FaultCodeGPUOOM, FaultCodeCUDAMismatch},
	})

	first, ok := injector.next()
	if !ok || first == "" {
		t.Fatalf("first fault = %q, ok=%v", first, ok)
	}
	otherFirst, otherOK := other.next()
	if !otherOK || otherFirst != first {
		t.Fatalf("seeded first faults = %q and %q, want equal", first, otherFirst)
	}
	second, ok := injector.next()
	otherSecond, otherOK := other.next()
	if !ok || second == "" || !otherOK || otherSecond != second {
		t.Fatalf("seeded second faults = %q and %q, want equal", second, otherSecond)
	}
	if third, ok := injector.next(); ok || third != "" {
		t.Fatalf("third fault = %q, ok=%v, want max fault count reached", third, ok)
	}
}

func TestFaultInjectorRateIsSeededAndProbabilistic(t *testing.T) {
	config := FaultInjectionConfig{
		Rate:  0.5,
		Seed:  42,
		Codes: []string{FaultCodeGPUOOM},
	}
	injector := newFaultInjector(config)
	other := newFaultInjector(config)
	var injected, skipped bool

	for i := 0; i < 64; i++ {
		fault, ok := injector.next()
		otherFault, otherOK := other.next()
		if ok != otherOK || fault != otherFault {
			t.Fatalf("seeded outcomes differ at iteration %d: (%q, %v) vs (%q, %v)", i, fault, ok, otherFault, otherOK)
		}
		if ok {
			injected = true
		} else {
			skipped = true
		}
	}
	if !injected || !skipped {
		t.Fatalf("rate 0.5 produced injected=%v skipped=%v; want both outcomes", injected, skipped)
	}
}

func TestFaultInjectorSupportsDeterministicTargetRates(t *testing.T) {
	config := FaultInjectionConfig{
		Seed:          42,
		Codes:         []string{FaultCodeTransientDeployment},
		TargetRates:   map[string]float64{"unstable": 1, "healthy": 0},
		Deterministic: true,
	}
	parameters := map[string]any{"experiment_case_id": "case-001", "experiment_attempt": 1}
	first, ok := newFaultInjector(config).nextFor("unstable", parameters)
	if !ok || first != FaultCodeTransientDeployment {
		t.Fatalf("unstable target fault = %q, ok=%v", first, ok)
	}
	if fault, ok := newFaultInjector(config).nextFor("healthy", parameters); ok || fault != "" {
		t.Fatalf("healthy target fault = %q, ok=%v", fault, ok)
	}
}

func TestLocalFaultErrorResourceInsufficientIsRetryable(t *testing.T) {
	err := localFaultError(FaultCodeResourceInsufficient)
	if err.Code != FaultCodeResourceInsufficient || err.HTTPStatus != http.StatusConflict || !err.Retryable {
		t.Fatalf("fault error = %+v, want retryable %s", err, FaultCodeResourceInsufficient)
	}
}

func TestLocalProcessAdapterFaultInjectionHook(t *testing.T) {
	if os.Getenv("AI_APP_LOCAL_FAULT_HELPER") == "1" {
		time.Sleep(30 * time.Second)
		return
	}

	workDir := t.TempDir()
	adapter := NewWithFaultInjection(workDir, FaultInjectionConfig{
		Rate:      1,
		Seed:      7,
		MaxFaults: 1,
		Codes:     []string{FaultCodeTransientDeployment},
	})
	target := model.TargetProfile{
		TargetProfileID: "local-fault-node",
		CSP:             "local",
		Runtime:         model.TargetRuntime{RuntimeType: "local", OperatingMode: "local_process"},
	}
	app := model.AppResponse{
		Name: "local-fault-app", Version: "test",
		AppSpec: model.AppSpec{
			Artifact:   model.Artifact{Type: "script", URI: filepath.ToSlash(workDir)},
			Entrypoint: model.Entrypoint{Command: os.Args[0], Args: []string{"-test.run=TestLocalProcessAdapterFaultInjectionHook"}},
			Runtime:    model.AppRuntime{Type: "cpu"},
		},
	}
	ctx := context.Background()
	if err := adapter.HealthCheck(ctx, model.RuntimeConfig{RuntimeType: "local", AdapterType: "local_process"}, target); err != nil {
		t.Fatal(err)
	}
	if _, err := adapter.Prepare(ctx, app, target); err != nil {
		t.Fatal(err)
	}

	plan := runtime.DeploymentPlan{
		DeploymentID: "dep-fault-injected",
		RequestID:    "req-fault-injected",
		App:          app,
		Runtime:      model.RuntimeConfig{RuntimeType: "local", AdapterType: "local_process"},
		Target:       target,
	}
	if _, err := adapter.Deploy(ctx, plan); err == nil {
		t.Fatal("fault injection did not reject the first deployment")
	} else {
		var appErr *apperrors.AppError
		if !errors.As(err, &appErr) || appErr.Code != FaultCodeTransientDeployment || !appErr.Retryable {
			t.Fatalf("fault error = %v, want retryable %s", err, FaultCodeTransientDeployment)
		}
	}

	t.Setenv("AI_APP_LOCAL_FAULT_HELPER", "1")
	result, err := adapter.Deploy(ctx, plan)
	if err != nil || result == nil || result.RuntimeID == "" {
		t.Fatalf("deployment after max fault = %+v err=%v", result, err)
	}
	if err := adapter.Stop(ctx, runtime.StopPlan{DeploymentID: plan.DeploymentID, RequestID: plan.RequestID, App: app, Target: target}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		status, _ := adapter.GetStatus(ctx, plan.DeploymentID)
		if status.Status == model.StatusStopped {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("deployment after injected fault did not stop")
}
