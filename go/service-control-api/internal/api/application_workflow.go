package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

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

type applicationAppSpecEvidence struct {
	SchemaVersion string                      `json:"schema_version,omitempty"`
	Kind          string                      `json:"kind,omitempty"`
	Metadata      *applicationAppSpecMetadata `json:"metadata,omitempty"`
	Artifact      *applicationAppSpecArtifact `json:"artifact,omitempty"`
	Entrypoint    *applicationAppEntrypoint   `json:"entrypoint,omitempty"`
	Runtime       *applicationAppRuntime      `json:"runtime,omitempty"`
	Resources     *applicationAppResources    `json:"resources,omitempty"`
	ModelRefs     []applicationAppModelRef    `json:"model_refs,omitempty"`
	Network       *applicationAppNetwork      `json:"network,omitempty"`
	Healthcheck   *applicationAppHealthcheck  `json:"healthcheck,omitempty"`
}

type applicationAppSpecMetadata struct {
	Name        string `json:"name,omitempty"`
	Version     string `json:"version,omitempty"`
	Description string `json:"description,omitempty"`
}

type applicationAppSpecArtifact struct {
	Type     string `json:"type,omitempty"`
	URI      string `json:"uri,omitempty"`
	Checksum string `json:"checksum,omitempty"`
}

type applicationAppEntrypoint struct {
	Command    string   `json:"command,omitempty"`
	Args       []string `json:"args,omitempty"`
	WorkingDir string   `json:"working_dir,omitempty"`
}

type applicationAppRuntime struct {
	Type        string `json:"type,omitempty"`
	Accelerator string `json:"accelerator,omitempty"`
}

type applicationAppResources struct {
	CPU     string `json:"cpu,omitempty"`
	Memory  string `json:"memory,omitempty"`
	GPU     string `json:"gpu,omitempty"`
	Storage string `json:"storage,omitempty"`
}

type applicationAppModelRef struct {
	Name      string `json:"name,omitempty"`
	Version   string `json:"version,omitempty"`
	URI       string `json:"uri,omitempty"`
	MountPath string `json:"mount_path,omitempty"`
}

type applicationAppNetwork struct {
	Ports []applicationAppPort `json:"ports,omitempty"`
}

type applicationAppPort struct {
	Name     string `json:"name,omitempty"`
	AppPort  int    `json:"app_port,omitempty"`
	Protocol string `json:"protocol,omitempty"`
}

type applicationAppHealthcheck struct {
	Type    string `json:"type,omitempty"`
	Path    string `json:"path,omitempty"`
	Command string `json:"command,omitempty"`
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
				"AppDeploy package build failed",
				map[string]any{"code": "PACKAGE_BUILD_FAILED"},
			))
			return nil
		})
		if err != nil {
			return run, err
		}
		return run, fmt.Errorf("build application package: %w", packageErr)
	}

	safePackageAppSpec, appSpecErr := projectSafeApplicationAppSpec(packageResult.AppSpec)
	packageEvidence := clonePackageBuildResponse(packageResult)
	packageEvidence.AppSpec = safePackageAppSpec
	if appSpecErr != nil {
		run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusPackageFailed
			run.Application = &controlrun.ApplicationEvidence{Package: &packageEvidence}
			run.PartialResult = &controlrun.PartialResult{
				ArtifactURI: packageEvidence.ArtifactURI,
				ArchiveName: packageEvidence.ArchiveName,
				Checksum:    packageEvidence.Checksum,
			}
			run.Stages = append(run.Stages, completedControlRunStage(
				"package_build",
				"rejected",
				"AppDeploy package AppSpec rejected by evidence policy",
				map[string]any{"code": "PACKAGE_APP_SPEC_REJECTED"},
			))
			return nil
		})
		if err != nil {
			return run, err
		}
		return run, fmt.Errorf("validate package AppSpec: %w", appSpecErr)
	}
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

	registration, registrationErr := provisioner.RegisterApp(ctx, packageResult.AppSpec)
	if registrationErr != nil {
		run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusAppRegistrationFailed
			run.Stages = append(run.Stages, completedControlRunStage(
				"app_registration",
				"rejected",
				"AppDeploy app registration failed",
				map[string]any{"code": "APP_REGISTRATION_FAILED"},
			))
			return nil
		})
		if err != nil {
			return run, err
		}
		return run, fmt.Errorf("register application: %w", registrationErr)
	}

	safeRegistrationAppSpec, registrationAppSpecErr := projectOptionalSafeApplicationAppSpec(registration.AppSpec)
	registrationEvidence := cloneAppRegistrationResponse(registration)
	registrationEvidence.AppSpec = safeRegistrationAppSpec
	input.Planner.AppVersionID = registrationEvidence.AppVersionID
	if registrationAppSpecErr != nil {
		run, err = service.controlRuns.Update(run.RunID, func(run *controlrun.Run) error {
			run.Status = controlrun.StatusAppRegistrationFailed
			run.Request.AppVersionID = registrationEvidence.AppVersionID
			run.Application.Registration = &registrationEvidence
			run.PartialResult.AppID = registrationEvidence.AppID
			run.PartialResult.AppVersionID = registrationEvidence.AppVersionID
			run.Stages = append(run.Stages, completedControlRunStage(
				"app_registration",
				"rejected",
				"AppDeploy registration AppSpec rejected by evidence policy",
				map[string]any{"code": "APP_REGISTRATION_APP_SPEC_REJECTED"},
			))
			return nil
		})
		if err != nil {
			return run, err
		}
		return run, fmt.Errorf("validate registration AppSpec: %w", registrationAppSpecErr)
	}
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

func projectOptionalSafeApplicationAppSpec(appSpec json.RawMessage) (json.RawMessage, error) {
	if len(strings.TrimSpace(string(appSpec))) == 0 {
		return nil, nil
	}
	return projectSafeApplicationAppSpec(appSpec)
}

func projectSafeApplicationAppSpec(appSpec json.RawMessage) (json.RawMessage, error) {
	if len(strings.TrimSpace(string(appSpec))) == 0 {
		return nil, fmt.Errorf("app_spec is required")
	}
	decoder := json.NewDecoder(bytes.NewReader(appSpec))
	decoder.DisallowUnknownFields()
	var evidence applicationAppSpecEvidence
	if err := decoder.Decode(&evidence); err != nil {
		return nil, fmt.Errorf("app_spec contains a field outside the evidence allowlist: %w", err)
	}
	if err := ensureJSONDocumentEnded(decoder); err != nil {
		return nil, err
	}
	if containsUnsafeApplicationAppSpecContent(appSpec) {
		return nil, fmt.Errorf("app_spec contains credential-like or private-key content")
	}
	content, err := json.Marshal(evidence)
	if err != nil {
		return nil, fmt.Errorf("encode safe app_spec evidence: %w", err)
	}
	return json.RawMessage(content), nil
}

func ensureJSONDocumentEnded(decoder *json.Decoder) error {
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return fmt.Errorf("app_spec must contain one JSON document")
		}
		return fmt.Errorf("decode trailing app_spec content: %w", err)
	}
	return nil
}

func containsUnsafeApplicationAppSpecContent(appSpec json.RawMessage) bool {
	content := strings.ToLower(string(appSpec))
	markers := []string{
		"-----begin private key-----",
		"-----begin rsa private key-----",
		"-----begin ec private key-----",
		"aws_secret_access_key",
		"authorization: bearer",
		"api_key=",
		"password=",
		"private_key=",
		"secret=",
		"token=",
	}
	for _, marker := range markers {
		if strings.Contains(content, marker) {
			return true
		}
	}
	return false
}
