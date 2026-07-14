package runtime_test

import (
	"context"
	"errors"
	"net/http"
	"testing"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/runtime"
	"github.com/khu/ai-app-deployer/internal/runtime/cpuvm"
	"github.com/khu/ai-app-deployer/internal/runtime/gpuvm"
)

type recordingVMRunner struct {
	commands []cpuvm.Command
	outputs  map[string]string
	failures map[string]error
}

func (r *recordingVMRunner) Run(_ context.Context, _ model.TargetProfile, command cpuvm.Command) (cpuvm.Result, error) {
	r.commands = append(r.commands, command)
	if err := r.failures[command.Name]; err != nil {
		return cpuvm.Result{}, err
	}
	return cpuvm.Result{Output: r.outputs[command.Name]}, nil
}

type vmAdapterScenario struct {
	name            string
	runtimeType     string
	displayName     string
	component       string
	runtimeIDPrefix string
	newAdapter      func(cpuvm.Runner) runtime.Adapter
	target          model.TargetProfile
}

func TestVMAdapterLifecycleCharacterization(t *testing.T) {
	for _, scenario := range vmAdapterScenarios() {
		t.Run(scenario.name, func(t *testing.T) {
			runner := &recordingVMRunner{outputs: map[string]string{
				"prepare-artifact": "prepared",
				"serve":            "token=private accepted",
				"stop-process":     "stopped",
			}, failures: map[string]error{}}
			adapter := scenario.newAdapter(runner)
			ctx := context.Background()
			app := vmApp(scenario.runtimeType)

			unknown, err := adapter.GetStatus(ctx, "dep-missing")
			if err != nil {
				t.Fatal(err)
			}
			if unknown.Status != model.StatusUnknown || unknown.Message != scenario.displayName+" status checked" {
				t.Fatalf("unknown status = %+v", unknown)
			}

			prepared, err := adapter.Prepare(ctx, app, scenario.target)
			if err != nil {
				t.Fatal(err)
			}
			wantWorkingDir := "/opt/aiapp/artifacts/vm-characterization/1.0.0"
			if prepared.ArtifactPath != wantWorkingDir || prepared.Message != scenario.displayName+" artifact preparation completed" {
				t.Fatalf("prepare result = %+v", prepared)
			}
			assertVMCommand(t, runner.commands[0], model.StatusDeploying, "prepare-artifact", "", []string{app.AppSpec.Artifact.URI, wantWorkingDir})

			plan := runtime.DeploymentPlan{
				DeploymentID: "dep-characterization",
				RequestID:    "req-characterization",
				App:          app,
				Runtime:      model.RuntimeProfile{RuntimeType: scenario.runtimeType},
				Target:       scenario.target,
			}
			deployed, err := adapter.Deploy(ctx, plan)
			if err != nil {
				t.Fatal(err)
			}
			if deployed.RuntimeID != scenario.runtimeIDPrefix+plan.DeploymentID || deployed.Message != scenario.displayName+" deployment is running" {
				t.Fatalf("deploy result = %+v", deployed)
			}
			assertVMCommand(t, runner.commands[1], model.StatusDeploying, "serve", wantWorkingDir, []string{"--port", "18080"})

			status, err := adapter.GetStatus(ctx, plan.DeploymentID)
			if err != nil {
				t.Fatal(err)
			}
			if status.Status != model.StatusRunning || status.Message != scenario.displayName+" status checked" {
				t.Fatalf("running status = %+v", status)
			}

			deployLogs, err := adapter.GetLogs(ctx, plan.DeploymentID, runtime.LogQuery{Stage: model.StatusDeploying})
			if err != nil {
				t.Fatal(err)
			}
			if len(deployLogs) != 1 {
				t.Fatalf("deploy logs = %+v", deployLogs)
			}
			assertVMLog(t, deployLogs[0], plan, scenario.component, model.StatusDeploying, scenario.displayName+" command accepted: [masked]=private accepted")

			stopPlan := runtime.StopPlan{
				DeploymentID: plan.DeploymentID,
				RequestID:    plan.RequestID,
				App:          app,
				Runtime:      plan.Runtime,
				Target:       scenario.target,
			}
			if err := adapter.Stop(ctx, stopPlan); err != nil {
				t.Fatal(err)
			}
			assertVMCommand(t, runner.commands[2], model.StatusStopping, "stop-process", "", []string{wantWorkingDir})

			status, err = adapter.GetStatus(ctx, plan.DeploymentID)
			if err != nil {
				t.Fatal(err)
			}
			if status.Status != model.StatusStopped || status.Message != scenario.displayName+" status checked" {
				t.Fatalf("stopped status = %+v", status)
			}
			stopLogs, err := adapter.GetLogs(ctx, plan.DeploymentID, runtime.LogQuery{Stage: model.StatusStopped})
			if err != nil {
				t.Fatal(err)
			}
			if len(stopLogs) != 1 {
				t.Fatalf("stop logs = %+v", stopLogs)
			}
			assertVMLog(t, stopLogs[0], plan, scenario.component, model.StatusStopped, scenario.displayName+" process stop requested: stopped")
			allLogs, err := adapter.GetLogs(ctx, plan.DeploymentID, runtime.LogQuery{})
			if err != nil {
				t.Fatal(err)
			}
			if len(allLogs) != 2 {
				t.Fatalf("all logs = %+v", allLogs)
			}
		})
	}
}

func TestVMAdapterErrorCharacterization(t *testing.T) {
	for _, scenario := range vmAdapterScenarios() {
		t.Run(scenario.name, func(t *testing.T) {
			ctx := context.Background()
			app := vmApp(scenario.runtimeType)

			wrongApp := app
			wrongApp.AppSpec.Runtime.Type = "other"
			adapter := scenario.newAdapter(&recordingVMRunner{outputs: map[string]string{}, failures: map[string]error{}})
			_, err := adapter.Deploy(ctx, runtime.DeploymentPlan{App: wrongApp, Target: scenario.target})
			assertVMAppError(t, err, model.ErrRuntimeProfileInvalid, scenario.displayName+" adapter can deploy only "+scenario.runtimeType+" apps", http.StatusBadRequest, false)

			prepareRunner := &recordingVMRunner{outputs: map[string]string{}, failures: map[string]error{"prepare-artifact": errors.New("prepare failed")}}
			_, err = scenario.newAdapter(prepareRunner).Prepare(ctx, app, scenario.target)
			assertVMAppError(t, err, model.ErrAppArtifactNotFound, scenario.displayName+" artifact preparation failed", http.StatusBadRequest, false)

			deployRunner := &recordingVMRunner{outputs: map[string]string{}, failures: map[string]error{"serve": errors.New("deploy failed")}}
			_, err = scenario.newAdapter(deployRunner).Deploy(ctx, runtime.DeploymentPlan{DeploymentID: "dep-fail", App: app, Target: scenario.target})
			assertVMAppError(t, err, model.ErrDeploymentFailed, scenario.displayName+" deployment command failed", http.StatusBadRequest, false)

			stopRunner := &recordingVMRunner{outputs: map[string]string{}, failures: map[string]error{"stop-process": errors.New("stop failed")}}
			err = scenario.newAdapter(stopRunner).Stop(ctx, runtime.StopPlan{DeploymentID: "dep-fail", App: app, Target: scenario.target})
			assertVMAppError(t, err, model.ErrRuntimeFailed, scenario.displayName+" stop command failed", http.StatusBadRequest, true)
		})
	}
}

func vmAdapterScenarios() []vmAdapterScenario {
	storage := &model.Storage{ArtifactDir: "/opt/aiapp/artifacts", LogDir: "/var/log/aiapp"}
	return []vmAdapterScenario{
		{
			name:            "cpu",
			runtimeType:     "cpu",
			displayName:     "cpu vm",
			component:       "cpu-vm-adapter",
			runtimeIDPrefix: "cpuvm-",
			newAdapter: func(runner cpuvm.Runner) runtime.Adapter {
				return cpuvm.New(runner)
			},
			target: model.TargetProfile{
				TargetProfileID: "target-cpu",
				VM:              model.VMProfile{Host: "cpu.example", CredentialRef: "cred://cpu"},
				Runtime:         model.TargetRuntime{RuntimeType: "cpu", Accelerator: "none"},
				Storage:         storage,
			},
		},
		{
			name:            "gpu",
			runtimeType:     "gpu",
			displayName:     "gpu vm",
			component:       "gpu-vm-adapter",
			runtimeIDPrefix: "gpuvm-",
			newAdapter: func(runner cpuvm.Runner) runtime.Adapter {
				return gpuvm.New(runner)
			},
			target: model.TargetProfile{
				TargetProfileID: "target-gpu",
				VM:              model.VMProfile{Host: "gpu.example", CredentialRef: "cred://gpu"},
				Runtime:         model.TargetRuntime{RuntimeType: "gpu", Accelerator: "nvidia"},
				GPU:             &model.GPUProfile{Vendor: "nvidia", Count: 1},
				Storage:         storage,
			},
		},
	}
}

func vmApp(runtimeType string) model.AppResponse {
	return model.AppResponse{
		AppID:        "app-characterization",
		AppVersionID: "appver-characterization",
		Name:         "vm-characterization",
		Version:      "1.0.0",
		AppSpec: model.AppSpec{
			Artifact:   model.Artifact{Type: "script", URI: "file:///tmp/serve.sh"},
			Entrypoint: model.Entrypoint{Command: "serve", Args: []string{"--port", "18080"}},
			Runtime:    model.AppRuntime{Type: runtimeType},
		},
	}
}

func assertVMCommand(t *testing.T, got cpuvm.Command, stage, name, workingDir string, args []string) {
	t.Helper()
	if got.Stage != stage || got.Name != name || got.WorkingDir != workingDir || len(got.Args) != len(args) {
		t.Fatalf("command = %+v, want stage=%s name=%s working_dir=%s args=%v", got, stage, name, workingDir, args)
	}
	for index := range args {
		if got.Args[index] != args[index] {
			t.Fatalf("command args = %v, want %v", got.Args, args)
		}
	}
}

func assertVMLog(t *testing.T, got model.DeploymentLog, plan runtime.DeploymentPlan, component, stage, message string) {
	t.Helper()
	if got.Timestamp.IsZero() || got.Level != "INFO" || got.RequestID != plan.RequestID || got.DeploymentID != plan.DeploymentID || got.Component != component || got.Stage != stage || got.Message != message {
		t.Fatalf("log = %+v, want component=%s stage=%s message=%q", got, component, stage, message)
	}
}

func assertVMAppError(t *testing.T, err error, code, message string, status int, retryable bool) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected %s", code)
	}
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		t.Fatalf("error type = %T, want *errors.AppError: %v", err, err)
	}
	if appErr.Code != code || appErr.Message != message || appErr.HTTPStatus != status || appErr.Retryable != retryable {
		t.Fatalf("error = %+v, want code=%s message=%q status=%d retryable=%t", appErr, code, message, status, retryable)
	}
}
