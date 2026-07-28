package api

import (
	"context"
	"encoding/json"
	"fmt"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
	"kyunghee-aiops/service-control-api/internal/deploymentplanner"
	"kyunghee-aiops/service-control-api/internal/llmclient"
)

type applicationProvisioner interface {
	BuildPackage(context.Context, appdeploy.PackageUpload) (appdeploy.PackageBuildResponse, error)
	RegisterApp(context.Context, json.RawMessage) (appdeploy.AppRegistrationResponse, error)
}

type CreatePackageControlRunInput struct {
	Package      appdeploy.PackageUpload
	Planner      CreateControlRunRequest
	Requirements appdeploy.DeploymentRequirements
}

func (service Service) CreateControlRunFromPackage(
	ctx context.Context,
	input CreatePackageControlRunInput,
) (controlrun.Run, error) {
	provisioner, err := appdeploy.NewClient(service.config.AppDeployBaseURL, nil)
	if err != nil {
		return controlrun.Run{}, err
	}
	return service.createControlRunFromPackageWithDependencies(
		ctx,
		input,
		provisioner,
		service.config.LLMCandidatesPath,
		service.config.PlannerGuardPolicyPath,
		deploymentplanner.NewGenerator(llmclient.NewClient(nil)),
	)
}

func (service Service) createControlRunFromPackageWithDependencies(
	ctx context.Context,
	input CreatePackageControlRunInput,
	provisioner applicationProvisioner,
	candidatesPath string,
	guardPolicyPath string,
	generator controlRunManifestGenerator,
) (controlrun.Run, error) {
	if err := ensureContext(ctx); err != nil {
		return controlrun.Run{}, err
	}
	if provisioner == nil {
		return controlrun.Run{}, fmt.Errorf("application provisioner is required")
	}
	if generator == nil {
		return controlrun.Run{}, fmt.Errorf("deployment Manifest generator is required")
	}

	requirements := input.Requirements
	input.Planner.Requirements = &requirements
	input.Planner = normalizeCreateControlRunRequest(input.Planner)
	run, err := service.createEmptyControlRun(input.Planner)
	if err != nil {
		return controlrun.Run{}, err
	}

	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.Status = controlrun.StatusPackaging
		run.Stages = append(run.Stages, completedControlRunStage(
			"app_upload",
			"approved",
			"Application source accepted for AppDeploy packaging",
			map[string]any{
				"package_type": input.Package.PackageType,
				"app_name":     input.Package.AppName,
				"app_version":  input.Package.AppVersion,
				"runtime_type": input.Package.RuntimeType,
			},
		))
		return nil
	})
	if err != nil {
		return run, err
	}

	packageResult, packageErr := provisioner.BuildPackage(ctx, input.Package)
	if packageErr != nil {
		run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusPackageFailed
			run.Stages = append(run.Stages, completedControlRunStage(
				"package_build",
				"rejected",
				packageErr.Error(),
				nil,
			))
			return nil
		})
		if err != nil {
			return run, err
		}
		return run, fmt.Errorf("build application package: %w", packageErr)
	}

	packageEvidence := clonePackageBuildResponse(packageResult)
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.Status = controlrun.StatusRegisteringApp
		run.Application = &controlrun.ApplicationEvidence{
			Package: &packageEvidence,
			AppSpec: append(json.RawMessage(nil), packageEvidence.AppSpec...),
		}
		run.PartialResult = &controlrun.PartialResult{
			ArtifactURI: packageEvidence.ArtifactURI,
			ArchiveName: packageEvidence.ArchiveName,
			Checksum:    packageEvidence.Checksum,
		}
		run.Stages = append(run.Stages, completedControlRunStage(
			"package_build",
			"approved",
			"AppDeploy built the application package",
			map[string]any{
				"artifact_uri": packageEvidence.ArtifactURI,
				"archive_name": packageEvidence.ArchiveName,
				"checksum":     packageEvidence.Checksum,
			},
		))
		return nil
	})
	if err != nil {
		return run, err
	}

	registration, registrationErr := provisioner.RegisterApp(ctx, packageEvidence.AppSpec)
	if registrationErr != nil {
		run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusAppRegistrationFailed
			run.Stages = append(run.Stages, completedControlRunStage(
				"app_registration",
				"rejected",
				registrationErr.Error(),
				nil,
			))
			return nil
		})
		if err != nil {
			return run, err
		}
		return run, fmt.Errorf("register application: %w", registrationErr)
	}

	registrationEvidence := cloneAppRegistrationResponse(registration)
	input.Planner.AppVersionID = registrationEvidence.AppVersionID
	run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
		run.Status = controlrun.StatusReceived
		run.Request.AppVersionID = registrationEvidence.AppVersionID
		run.Application.Registration = &registrationEvidence
		run.PartialResult.AppID = registrationEvidence.AppID
		run.PartialResult.AppVersionID = registrationEvidence.AppVersionID
		run.Stages = append(run.Stages, completedControlRunStage(
			"app_registration",
			"approved",
			"AppDeploy registered the application version",
			map[string]any{
				"app_id":         registrationEvidence.AppID,
				"app_version_id": registrationEvidence.AppVersionID,
			},
		))
		return nil
	})
	if err != nil {
		return run, err
	}

	return service.continueGuardedManifestPlanning(
		ctx,
		run.RunID,
		input.Planner,
		candidatesPath,
		guardPolicyPath,
		generator,
	)
}

func clonePackageBuildResponse(source appdeploy.PackageBuildResponse) appdeploy.PackageBuildResponse {
	result := source
	result.AppSpec = append(json.RawMessage(nil), source.AppSpec...)
	return result
}

func cloneAppRegistrationResponse(source appdeploy.AppRegistrationResponse) appdeploy.AppRegistrationResponse {
	result := source
	result.AppSpec = append(json.RawMessage(nil), source.AppSpec...)
	return result
}
