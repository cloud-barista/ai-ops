package api

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
)

type fakeApplicationProvisioner struct {
	packageCalls  int
	registerCalls int
	registered    json.RawMessage
	packageResult appdeploy.PackageBuildResponse
	appResult     appdeploy.AppRegistrationResponse
	packageErr    error
	appErr        error
	registerHook  func()
}

func (fake *fakeApplicationProvisioner) BuildPackage(
	context.Context,
	appdeploy.PackageUpload,
) (appdeploy.PackageBuildResponse, error) {
	fake.packageCalls++
	return fake.packageResult, fake.packageErr
}

func (fake *fakeApplicationProvisioner) RegisterApp(
	_ context.Context,
	appSpec json.RawMessage,
) (appdeploy.AppRegistrationResponse, error) {
	fake.registerCalls++
	fake.registered = append(json.RawMessage(nil), appSpec...)
	if fake.registerHook != nil {
		fake.registerHook()
	}
	return fake.appResult, fake.appErr
}

func TestCreateControlRunFromPackageConnectsIssuedAppVersionToGuardedManifest(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	appSpec := json.RawMessage(`{"kind":"AIApp","metadata":{"name":"demo"}}`)
	provisioner := &fakeApplicationProvisioner{
		packageResult: appdeploy.PackageBuildResponse{
			PackageType: "script",
			ArtifactURI: "file:///packages/demo.tar.gz",
			ArchiveName: "demo.tar.gz",
			Checksum:    "sha256:package",
			AppSpec:     appSpec,
		},
		appResult: appdeploy.AppRegistrationResponse{
			AppID:        "app-001",
			AppVersionID: "appver-issued",
			Name:         "demo",
			Version:      "0.1.0",
			AppSpec:      appSpec,
		},
	}
	input := validPackageControlRunInput()
	generator := &fakeControlRunManifestGenerator{
		result: approvedPackageGenerateResult("appver-issued", input.Requirements),
	}

	run, err := service.createControlRunFromPackageWithDependencies(
		context.Background(),
		input,
		provisioner,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		generator,
	)
	if err != nil {
		t.Fatalf("create package ControlRun: %v", err)
	}
	if run.Status != controlrun.StatusManifestApproved {
		t.Fatalf("unexpected Run status: %s", run.Status)
	}
	if runs := service.ListControlRuns(); len(runs) != 1 || runs[0].RunID != run.RunID {
		t.Fatalf("package workflow did not preserve one ControlRun: %#v", runs)
	}
	if run.Request.AppVersionID != "appver-issued" ||
		run.Request.RequestedBy != "ai-agent" ||
		run.Manifest.Spec.AppVersionID != "appver-issued" ||
		run.Manifest.Spec.RequestedBy != "ai-agent" ||
		generator.input.AppVersionID != "appver-issued" ||
		generator.input.RequestedBy != "ai-agent" ||
		!reflect.DeepEqual(generator.input.Requirements, &input.Requirements) {
		t.Fatalf("issued App version was not connected: run=%#v input=%#v", run, generator.input)
	}
	if run.Application == nil ||
		run.Application.Package == nil ||
		run.Application.Registration == nil ||
		run.Application.Registration.AppVersionID != "appver-issued" ||
		!reflect.DeepEqual(run.Application.AppSpec, appSpec) {
		t.Fatalf("application evidence is incomplete: %#v", run.Application)
	}
	if run.PartialResult == nil ||
		run.PartialResult.ArtifactURI != "file:///packages/demo.tar.gz" ||
		run.PartialResult.AppID != "app-001" ||
		run.PartialResult.AppVersionID != "appver-issued" {
		t.Fatalf("partial result is incomplete: %#v", run.PartialResult)
	}
	if provisioner.packageCalls != 1 ||
		provisioner.registerCalls != 1 ||
		!reflect.DeepEqual(provisioner.registered, appSpec) {
		t.Fatalf("unexpected provisioning calls: %#v", provisioner)
	}
	assertControlRunStageNames(t, run, []string{
		"app_upload",
		"package_build",
		"app_registration",
		"request_guard",
		"agent_registry",
		"agent_dispatch",
		"qwen_planner",
		"manifest_guard",
	})
	for _, stage := range run.Stages[:3] {
		if _, found := stage.Details["source"]; found {
			t.Fatalf("stage contains source evidence: %#v", stage)
		}
		if filename, found := stage.Details["filename"]; found &&
			(strings.Contains(strings.ToLower(filename.(string)), `c:\`) ||
				strings.Contains(filename.(string), "/")) {
			t.Fatalf("stage contains a local filename: %#v", stage)
		}
	}
}

func TestCreateControlRunDefaultsBlankRequesterToAIAgent(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	request := validCreateControlRunRequest()
	request.RequestedBy = ""
	result := approvedGenerateResult("appver-001")
	result.Manifest.Spec.RequestedBy = "ai-agent"
	generator := &fakeControlRunManifestGenerator{result: result}

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		request,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		generator,
	)
	if err != nil {
		t.Fatalf("create existing-App ControlRun: %v", err)
	}
	if run.Request.RequestedBy != "ai-agent" ||
		generator.input.RequestedBy != "ai-agent" ||
		run.Manifest.Spec.RequestedBy != "ai-agent" {
		t.Fatalf("blank requester did not default to ai-agent: run=%#v input=%#v", run, generator.input)
	}
}

func TestCreateControlRunFromPackageStopsAfterPackageFailure(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	provisioner := &fakeApplicationProvisioner{packageErr: errors.New("package service unavailable")}

	run, err := service.createControlRunFromPackageWithDependencies(
		context.Background(),
		validPackageControlRunInput(),
		provisioner,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		&fakeControlRunManifestGenerator{result: approvedGenerateResult("unused")},
	)
	if err == nil {
		t.Fatal("expected package failure")
	}
	if run.Status != controlrun.StatusPackageFailed || provisioner.registerCalls != 0 {
		t.Fatalf("package failure continued to registration: run=%#v calls=%d", run, provisioner.registerCalls)
	}
	assertControlRunStageNames(t, run, []string{"app_upload", "package_build"})
	if run.Stages[1].Status != "rejected" {
		t.Fatalf("package rejection was not recorded: %#v", run.Stages[1])
	}
}

func TestCreateControlRunFromPackagePreservesPackageAfterRegistrationFailure(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	appSpec := json.RawMessage(`{"kind":"AIApp"}`)
	provisioner := &fakeApplicationProvisioner{
		packageResult: appdeploy.PackageBuildResponse{
			ArtifactURI: "file:///packages/demo.tar.gz",
			ArchiveName: "demo.tar.gz",
			Checksum:    "sha256:package",
			AppSpec:     appSpec,
		},
		appErr: errors.New("registration service unavailable"),
	}

	run, err := service.createControlRunFromPackageWithDependencies(
		context.Background(),
		validPackageControlRunInput(),
		provisioner,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		&fakeControlRunManifestGenerator{result: approvedGenerateResult("unused")},
	)
	if err == nil {
		t.Fatal("expected registration failure")
	}
	if run.Status != controlrun.StatusAppRegistrationFailed ||
		run.Application == nil ||
		run.Application.Package == nil ||
		run.Application.Registration != nil {
		t.Fatalf("unexpected registration failure evidence: %#v", run)
	}
	if run.PartialResult == nil ||
		run.PartialResult.ArtifactURI != "file:///packages/demo.tar.gz" ||
		run.PartialResult.ArchiveName != "demo.tar.gz" ||
		run.PartialResult.Checksum != "sha256:package" {
		t.Fatalf("package partial result was lost: %#v", run.PartialResult)
	}
	assertControlRunStageNames(t, run, []string{"app_upload", "package_build", "app_registration"})
}

func TestCreateControlRunFromPackageRejectsUnsafeAppSpecBeforePersistence(t *testing.T) {
	tests := []struct {
		name            string
		packageAppSpec  json.RawMessage
		registrationApp json.RawMessage
		forbidden       []string
		wantStatus      controlrun.Status
		wantCalls       int
	}{
		{
			name:           "nested credential key in package response",
			packageAppSpec: json.RawMessage(`{"kind":"AIApp","metadata":{"credential":{"password":"nested-package-secret"}}}`),
			forbidden:      []string{"nested-package-secret", `"credential"`, `"password"`},
			wantStatus:     controlrun.StatusPackageFailed,
			wantCalls:      0,
		},
		{
			name:            "PEM private key in registration response",
			packageAppSpec:  json.RawMessage(`{"kind":"AIApp","metadata":{"name":"demo"}}`),
			registrationApp: json.RawMessage(`{"kind":"AIApp","metadata":{"description":"-----BEGIN PRIVATE KEY-----\nregistration-pem-secret\n-----END PRIVATE KEY-----"}}`),
			forbidden:       []string{"BEGIN PRIVATE KEY", "registration-pem-secret"},
			wantStatus:      controlrun.StatusAppRegistrationFailed,
			wantCalls:       1,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := NewServerConfig()
			service := NewService(config)
			provisioner := &fakeApplicationProvisioner{
				packageResult: appdeploy.PackageBuildResponse{
					ArtifactURI: "file:///packages/demo.tar.gz",
					ArchiveName: "demo.tar.gz",
					Checksum:    "sha256:package",
					AppSpec:     test.packageAppSpec,
				},
				appResult: appdeploy.AppRegistrationResponse{
					AppID:        "app-001",
					AppVersionID: "appver-issued",
					AppSpec:      test.registrationApp,
				},
			}

			run, err := service.createControlRunFromPackageWithDependencies(
				context.Background(),
				validPackageControlRunInput(),
				provisioner,
				writeAutomationCandidateConfig(t, "http://unused.example.test"),
				config.PlannerGuardPolicyPath,
				&fakeControlRunManifestGenerator{result: approvedGenerateResult("unused")},
			)
			if err == nil {
				t.Fatal("expected unsafe AppSpec rejection")
			}
			if run.Status != test.wantStatus || provisioner.registerCalls != test.wantCalls {
				t.Fatalf("unsafe AppSpec crossed the expected boundary: run=%#v calls=%d", run, provisioner.registerCalls)
			}
			assertSerializedEvidenceExcludes(t, run, test.forbidden...)
			stored, ok := service.GetControlRun(run.RunID)
			if !ok {
				t.Fatalf("unsafe AppSpec Run was not retained: %s", run.RunID)
			}
			assertSerializedEvidenceExcludes(t, stored, test.forbidden...)
			assertSerializedEvidenceExcludes(t, service.ListControlRuns(), test.forbidden...)
		})
	}
}

func TestCreateControlRunFromPackagePersistsSafeFailureReasons(t *testing.T) {
	tests := []struct {
		name       string
		packageErr error
		appErr     error
		forbidden  []string
		wantReason string
		wantCode   string
	}{
		{
			name: "path-bearing package error",
			packageErr: &os.PathError{
				Op:   "open",
				Path: `C:\Users\geonhae\private\run.sh`,
				Err:  errors.New("api_key=package-secret"),
			},
			forbidden:  []string{`C:\\Users\\geonhae\\private\\run.sh`, "package-secret", "api_key"},
			wantReason: "AppDeploy package build failed",
			wantCode:   "PACKAGE_BUILD_FAILED",
		},
		{
			name:       "secret-bearing registration error",
			appErr:     errors.New(`password=registration-secret at C:\private\registration.json`),
			forbidden:  []string{"registration-secret", "password", `C:\\private\\registration.json`},
			wantReason: "AppDeploy app registration failed",
			wantCode:   "APP_REGISTRATION_FAILED",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := NewServerConfig()
			service := NewService(config)
			appSpec := json.RawMessage(`{"kind":"AIApp"}`)
			provisioner := &fakeApplicationProvisioner{
				packageResult: appdeploy.PackageBuildResponse{
					ArtifactURI: "file:///packages/demo.tar.gz",
					ArchiveName: "demo.tar.gz",
					Checksum:    "sha256:package",
					AppSpec:     appSpec,
				},
				packageErr: test.packageErr,
				appErr:     test.appErr,
			}

			run, err := service.createControlRunFromPackageWithDependencies(
				context.Background(),
				validPackageControlRunInput(),
				provisioner,
				writeAutomationCandidateConfig(t, "http://unused.example.test"),
				config.PlannerGuardPolicyPath,
				&fakeControlRunManifestGenerator{result: approvedGenerateResult("unused")},
			)
			if err == nil {
				t.Fatal("expected provisioning failure")
			}
			if !containsAny(err.Error(), test.forbidden) {
				t.Fatalf("method error lost diagnostic context: %v", err)
			}
			failedStage := run.Stages[len(run.Stages)-1]
			if failedStage.Reason != test.wantReason || failedStage.Details["code"] != test.wantCode {
				t.Fatalf("failure stage is not stable and bounded: %#v", failedStage)
			}
			assertSerializedEvidenceExcludes(t, run, test.forbidden...)
			stored, ok := service.GetControlRun(run.RunID)
			if !ok {
				t.Fatalf("failed Run was not stored: %s", run.RunID)
			}
			assertSerializedEvidenceExcludes(t, stored, test.forbidden...)
			assertSerializedEvidenceExcludes(t, service.ListControlRuns(), test.forbidden...)
		})
	}
}

func TestCreateControlRunFromPackageRecordsCancellationAfterRegistration(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	ctx, cancel := context.WithCancel(context.Background())
	appSpec := json.RawMessage(`{"kind":"AIApp"}`)
	provisioner := &fakeApplicationProvisioner{
		packageResult: appdeploy.PackageBuildResponse{
			ArtifactURI: "file:///packages/demo.tar.gz",
			ArchiveName: "demo.tar.gz",
			Checksum:    "sha256:package",
			AppSpec:     appSpec,
		},
		appResult: appdeploy.AppRegistrationResponse{
			AppID:        "app-001",
			AppVersionID: "appver-issued",
			AppSpec:      appSpec,
		},
		registerHook: cancel,
	}
	input := validPackageControlRunInput()

	run, err := service.createControlRunFromPackageWithDependencies(
		ctx,
		input,
		provisioner,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		&fakeControlRunManifestGenerator{result: approvedPackageGenerateResult("appver-issued", input.Requirements)},
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected wrapped context cancellation, got %v", err)
	}
	if run.RunID == "" ||
		run.Status != controlrun.Status("PLANNING_CANCELED") ||
		run.Application == nil ||
		run.Application.Package == nil ||
		run.Application.Registration == nil ||
		run.PartialResult == nil ||
		run.PartialResult.AppVersionID != "appver-issued" {
		t.Fatalf("post-registration cancellation lost Run evidence: %#v", run)
	}
	failedStage := run.Stages[len(run.Stages)-1]
	if failedStage.Name != "guarded_planning" ||
		failedStage.Status != "canceled" ||
		failedStage.Reason != "Guarded Manifest planning canceled" ||
		failedStage.Details["code"] != "PLANNING_CANCELED" {
		t.Fatalf("cancellation stage is not explicit and bounded: %#v", failedStage)
	}
	stored, ok := service.GetControlRun(run.RunID)
	if !ok ||
		stored.RunID != run.RunID ||
		stored.Status != controlrun.Status("PLANNING_CANCELED") ||
		stored.PartialResult == nil ||
		stored.PartialResult.AppVersionID != "appver-issued" {
		t.Fatalf("stored cancellation evidence differs from returned Run: %#v", stored)
	}
}

func TestCreateControlRunFromPackagePreservesApplicationAfterManifestRejection(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)
	appSpec := json.RawMessage(`{"kind":"AIApp"}`)
	provisioner := &fakeApplicationProvisioner{
		packageResult: appdeploy.PackageBuildResponse{
			ArtifactURI: "file:///packages/demo.tar.gz",
			ArchiveName: "demo.tar.gz",
			Checksum:    "sha256:package",
			AppSpec:     appSpec,
		},
		appResult: appdeploy.AppRegistrationResponse{
			AppID:        "app-001",
			AppVersionID: "appver-issued",
			AppSpec:      appSpec,
		},
	}
	input := validPackageControlRunInput()
	result := approvedPackageGenerateResult("appver-issued", input.Requirements)
	result.Manifest.Spec.Accelerator = "none"
	result.Manifest.Spec.Resources.GPU = "1"

	run, err := service.createControlRunFromPackageWithDependencies(
		context.Background(),
		input,
		provisioner,
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		&fakeControlRunManifestGenerator{result: result},
	)
	if err == nil {
		t.Fatal("expected Manifest Guard rejection")
	}
	if run.Status != controlrun.StatusManifestRejected ||
		run.Application == nil ||
		run.Application.Package == nil ||
		run.Application.Registration == nil ||
		run.PartialResult == nil ||
		run.PartialResult.AppVersionID != "appver-issued" {
		t.Fatalf("application evidence was lost after Manifest rejection: %#v", run)
	}
}

func TestCreateControlRunWithoutPackageHasNoApplicationEvidence(t *testing.T) {
	config := NewServerConfig()
	service := NewService(config)

	run, err := service.CreateControlRunWithDependencies(
		context.Background(),
		validCreateControlRunRequest(),
		writeAutomationCandidateConfig(t, "http://unused.example.test"),
		config.PlannerGuardPolicyPath,
		&fakeControlRunManifestGenerator{result: approvedGenerateResult("appver-001")},
	)
	if err != nil {
		t.Fatalf("create existing-App ControlRun: %v", err)
	}
	if run.Status != controlrun.StatusManifestApproved ||
		run.Application != nil ||
		run.PartialResult != nil {
		t.Fatalf("existing JSON ControlRun gained application evidence: %#v", run)
	}
}

func validPackageControlRunInput() CreatePackageControlRunInput {
	return CreatePackageControlRunInput{
		Package: appdeploy.PackageUpload{
			Source:      strings.NewReader("#!/bin/sh\n"),
			Filename:    `C:\local\run.sh`,
			PackageType: "script",
			AppName:     "demo",
			AppVersion:  "0.1.0",
			Entrypoint:  "run.sh",
			RuntimeType: "cpu",
		},
		Planner: CreateControlRunRequest{
			NaturalLanguageRequest: "Deploy this CPU inference application.",
			CandidateID:            "decision-model",
		},
		Requirements: appdeploy.DeploymentRequirements{
			Runtime: "cpu",
			Resources: appdeploy.ResourceRequirements{
				CPU:     "2",
				Memory:  "4Gi",
				GPU:     "0",
				Storage: "10Gi",
			},
			CostPolicy: "min_cost",
		},
	}
}

func approvedPackageGenerateResult(
	appVersionID string,
	requirements appdeploy.DeploymentRequirements,
) deploymentplanner.GenerateResult {
	result := approvedGenerateResult(appVersionID)
	result.Manifest.Spec.Requirements = &requirements
	result.Manifest.Spec.Resources = requirements.Resources
	result.Manifest.Spec.RequestedBy = "ai-agent"
	return result
}

func assertSerializedEvidenceExcludes(t *testing.T, evidence any, forbidden ...string) {
	t.Helper()
	content, err := json.Marshal(evidence)
	if err != nil {
		t.Fatalf("serialize ControlRun: %v", err)
	}
	lowerContent := strings.ToLower(string(content))
	for _, value := range forbidden {
		if strings.Contains(lowerContent, strings.ToLower(value)) {
			t.Fatalf("serialized ControlRun contains forbidden evidence %q: %s", value, content)
		}
	}
}

func containsAny(value string, candidates []string) bool {
	for _, candidate := range candidates {
		unescaped := strings.ReplaceAll(candidate, `\\`, `\`)
		if strings.Contains(value, candidate) || strings.Contains(value, unescaped) {
			return true
		}
	}
	return false
}

func assertControlRunStageNames(t *testing.T, run controlrun.Run, want []string) {
	t.Helper()
	got := make([]string, len(run.Stages))
	for index, stage := range run.Stages {
		got[index] = stage.Name
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("stage names = %#v, want %#v", got, want)
	}
}
