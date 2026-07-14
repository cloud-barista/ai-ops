package artifact

import (
	"fmt"

	"github.com/khu/ai-app-deployer/internal/model"
)

func aiopsPackageAppSpec(version string, port int, archiveName, artifactURI, checksum string) model.AppSpec {
	entryCommand := fmt.Sprintf("rm -rf ./run && mkdir -p ./run && tar -xzf %s -C ./run && chmod +x ./run/%s && exec env PORT=%d AIOPS_REPO_ROOT=\"$PWD/run\" ./run/%s", archiveName, aiopsBinaryName, port, aiopsBinaryName)
	return model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:        PresetAIOpsGeon,
			Version:     version,
			Description: "ai-ops-geon service-control API package created by AppDeploy",
		},
		Artifact: model.Artifact{Type: "package", URI: artifactURI, Checksum: "sha256:" + checksum},
		Entrypoint: model.Entrypoint{
			Command: "sh",
			Args:    []string{"-c", entryCommand},
		},
		Runtime:   model.AppRuntime{Type: "cpu", Accelerator: "none"},
		Resources: model.Resources{CPU: "1", Memory: "512Mi", GPU: "0", Storage: "1Gi"},
		Network:   &model.Network{Ports: []model.Port{{Name: "http", AppPort: port, Protocol: "TCP"}}},
		Healthcheck: &model.Healthcheck{
			Type: "http",
			Path: "/healthz",
		},
	}
}

func uploadedPackageAppSpec(packageType, appName, version, runtimeType string, port int, healthPath, entrypoint, archiveName, artifactURI, checksum string) model.AppSpec {
	accelerator := "none"
	gpu := "0"
	memory := "1Gi"
	if runtimeType == "gpu" {
		accelerator = "nvidia"
		gpu = "1"
		memory = "4Gi"
	}
	appSpec := model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:        appName,
			Version:     version,
			Description: fmt.Sprintf("%s package created by AppDeploy", packageType),
		},
		Artifact: model.Artifact{Type: "package", URI: artifactURI, Checksum: "sha256:" + checksum},
		Entrypoint: model.Entrypoint{
			Command: "sh",
			Args:    []string{"-c", uploadedLaunchCommand(packageType, entrypoint, archiveName, port)},
		},
		Runtime:   model.AppRuntime{Type: runtimeType, Accelerator: accelerator},
		Resources: model.Resources{CPU: "1", Memory: memory, GPU: gpu, Storage: "1Gi"},
	}
	if port > 0 {
		appSpec.Network = &model.Network{Ports: []model.Port{{Name: "http", AppPort: port, Protocol: "TCP"}}}
		appSpec.Healthcheck = &model.Healthcheck{Type: "http", Path: healthPath}
	}
	return appSpec
}

func uploadedLaunchCommand(packageType, entrypoint, archiveName string, port int) string {
	environment := ""
	if port > 0 {
		environment = fmt.Sprintf("env PORT=%d ", port)
	}
	var launch string
	switch packageType {
	case PackageTypePy:
		launch = environment + "python3 ./" + entrypoint
	case PackageTypeNode:
		launch = environment + "node ./" + entrypoint
	case PackageTypeSh:
		launch = environment + "bash ./" + entrypoint
	default:
		launch = environment + "./" + entrypoint
	}
	chmod := ""
	if packageType == PackageTypeGo || packageType == PackageTypeBin || packageType == PackageTypeSh {
		chmod = "chmod +x ./run/" + entrypoint + " && "
	}
	return fmt.Sprintf("rm -rf ./run && mkdir -p ./run && tar -xzf %s -C ./run && %scd ./run && exec %s", archiveName, chmod, launch)
}
