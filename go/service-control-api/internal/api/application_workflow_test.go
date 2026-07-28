package api

import (
	"context"
	"encoding/json"
	"errors"
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
		run.Manifest.Spec.AppVersionID != "appver-issued" ||
		generator.input.AppVersionID != "appver-issued" ||
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
	return result
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
