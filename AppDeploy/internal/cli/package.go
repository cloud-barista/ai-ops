package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/khu/ai-app-deployer/internal/artifact"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/rs/zerolog/log"
)

const packageCommandUsage = "사용법: packages build|deploy [--type TYPE --source FILE --name NAME --version VERSION --entrypoint PATH --runtime cpu|gpu --port PORT --health-path PATH --target-id ID]"

type packageCommandOptions struct {
	packageType     string
	sourcePath      string
	appName         string
	appVersion      string
	entrypoint      string
	runtimeType     string
	servicePort     int
	healthcheckPath string
	targetID        string
}

type packageDeployOutput struct {
	Package       model.PackageBuildResponse  `json:"package"`
	App           model.AppResponse           `json:"app"`
	ResourceCheck model.ResourceCheckResponse `json:"resource_check"`
	Deployment    model.DeploymentResponse    `json:"deployment"`
}

func (s *Shell) packages(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New(packageCommandUsage)
	}
	switch strings.ToLower(args[0]) {
	case "build", "create":
		return s.packageBuild(ctx, args[1:])
	case "deploy", "run":
		return s.packageDeploy(ctx, args[1:])
	default:
		return fmt.Errorf("알 수 없는 packages 작업 %q입니다. %s", args[0], packageCommandUsage)
	}
}

func (s *Shell) packageBuild(ctx context.Context, args []string) error {
	options, err := s.packageOptions(ctx, args, false)
	if err != nil {
		return err
	}
	result, err := s.buildPackage(ctx, options)
	if err != nil {
		return err
	}
	return s.printValue(result)
}

func (s *Shell) packageDeploy(ctx context.Context, args []string) error {
	options, err := s.packageOptions(ctx, args, true)
	if err != nil {
		return err
	}

	packageResult, err := s.buildPackage(ctx, options)
	if err != nil {
		return err
	}
	appResponse, err := s.call(ctx, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: packageResult.AppSpec})
	if err != nil {
		return fmt.Errorf("package %s (%s) 생성 후 App 등록 실패: %w", packageResult.ArchiveName, packageResult.ArtifactURI, err)
	}
	var appResult model.AppResponse
	if err := decodeCLIResponse(appResponse.body, &appResult); err != nil {
		return fmt.Errorf("package %s (%s) 생성 후 App 등록 응답 해석 실패: %w", packageResult.ArchiveName, packageResult.ArtifactURI, err)
	}

	var resourceResult model.ResourceCheckResponse
	if options.targetID != "" {
		resourceResponse, err := s.call(ctx, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{
			TargetProfileID: options.targetID,
		})
		if err != nil {
			return fmt.Errorf("package %s 및 App %s 생성 후 자원 점검 실패: %w", packageResult.ArchiveName, appResult.AppVersionID, err)
		}
		if err := decodeCLIResponse(resourceResponse.body, &resourceResult); err != nil {
			return fmt.Errorf("package %s 및 App %s 생성 후 자원 점검 응답 해석 실패: %w", packageResult.ArchiveName, appResult.AppVersionID, err)
		}
		if !strings.EqualFold(resourceResult.Status, "available") {
			checks, _ := json.Marshal(resourceResult.Checks)
			return fmt.Errorf(
				"package %s (%s) 및 App %s는 생성되었지만 자원 점검 상태가 %q입니다 (checks=%s). Deployment는 생성하지 않았습니다",
				packageResult.ArchiveName,
				packageResult.ArtifactURI,
				appResult.AppVersionID,
				resourceResult.Status,
				checks,
			)
		}
	}

	deploymentResponse, err := s.call(ctx, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:    appResult.AppVersionID,
		TargetProfileID: options.targetID,
		RequestedBy:     "appdeployer-cli-package",
	})
	if err != nil {
		return fmt.Errorf("package %s 및 App %s 생성 후 배포 생성 실패: %w", packageResult.ArchiveName, appResult.AppVersionID, err)
	}
	var deploymentResult model.DeploymentResponse
	if err := decodeCLIResponse(deploymentResponse.body, &deploymentResult); err != nil {
		return fmt.Errorf("package %s 및 App %s 생성 후 배포 응답 해석 실패: %w", packageResult.ArchiveName, appResult.AppVersionID, err)
	}

	return s.printValue(packageDeployOutput{
		Package:       packageResult,
		App:           appResult,
		ResourceCheck: resourceResult,
		Deployment:    deploymentResult,
	})
}

func (s *Shell) packageOptions(ctx context.Context, args []string, deploy bool) (packageCommandOptions, error) {
	if len(args) == 0 {
		return s.packageWizard(ctx, deploy)
	}
	options, err := parsePackageFlags(args)
	if err != nil {
		return packageCommandOptions{}, err
	}
	return normalizePackageOptions(options, deploy)
}

func parsePackageFlags(args []string) (packageCommandOptions, error) {
	var options packageCommandOptions
	flags := flag.NewFlagSet("packages", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	flags.StringVar(&options.packageType, "type", "", "package type")
	flags.StringVar(&options.sourcePath, "source", "", "source ZIP or file")
	flags.StringVar(&options.appName, "name", "", "App name")
	flags.StringVar(&options.appName, "app-name", "", "App name")
	flags.StringVar(&options.appVersion, "version", "", "App version")
	flags.StringVar(&options.entrypoint, "entrypoint", "", "build package or start file path")
	flags.StringVar(&options.runtimeType, "runtime", "", "cpu or gpu")
	flags.StringVar(&options.runtimeType, "runtime-type", "", "cpu or gpu")
	flags.IntVar(&options.servicePort, "port", 0, "service port")
	flags.IntVar(&options.servicePort, "service-port", 0, "service port")
	flags.StringVar(&options.healthcheckPath, "health-path", "", "HTTP health path")
	flags.StringVar(&options.targetID, "target-id", "", "Target Profile ID")
	flags.StringVar(&options.targetID, "target-profile-id", "", "Target Profile ID")
	if err := flags.Parse(args); err != nil {
		return packageCommandOptions{}, fmt.Errorf("%s: %w", packageCommandUsage, err)
	}
	if flags.NArg() != 0 {
		return packageCommandOptions{}, fmt.Errorf("위치 인자는 지원하지 않습니다: %s", strings.Join(flags.Args(), " "))
	}
	return options, nil
}

func normalizePackageOptions(options packageCommandOptions, deploy bool) (packageCommandOptions, error) {
	options.packageType = strings.ToLower(strings.TrimSpace(options.packageType))
	options.sourcePath = strings.TrimSpace(options.sourcePath)
	options.appName = strings.TrimSpace(options.appName)
	options.appVersion = strings.TrimSpace(options.appVersion)
	options.entrypoint = strings.TrimSpace(options.entrypoint)
	options.runtimeType = strings.ToLower(strings.TrimSpace(options.runtimeType))
	options.healthcheckPath = strings.TrimSpace(options.healthcheckPath)
	options.targetID = strings.TrimSpace(options.targetID)

	if !isPackageCommandType(options.packageType) {
		return packageCommandOptions{}, errors.New("--type은 aiops-geon-service-control, go, python, node, binary, script 중 하나여야 합니다")
	}
	if options.servicePort < 0 || options.servicePort > 65535 {
		return packageCommandOptions{}, errors.New("--port는 0 또는 1~65535 범위여야 합니다")
	}

	if options.packageType == artifact.PresetAIOpsGeon {
		if options.servicePort == 0 {
			options.servicePort = 18089
		}
		options.runtimeType = "cpu"
	} else {
		if options.sourcePath == "" {
			return packageCommandOptions{}, errors.New("업로드 패키지는 --source가 필요합니다")
		}
		if options.appName == "" {
			options.appName = defaultPackageAppName(options.sourcePath)
		}
		if options.appVersion == "" {
			options.appVersion = "0.1.0"
		}
		if options.entrypoint == "" {
			options.entrypoint = defaultPackageEntrypoint(options.packageType, options.sourcePath)
		}
		if options.entrypoint == "" {
			return packageCommandOptions{}, errors.New("ZIP 내부 시작 파일을 --entrypoint로 지정하세요")
		}
		if options.runtimeType == "" {
			options.runtimeType = "cpu"
		}
		if options.runtimeType != "cpu" && options.runtimeType != "gpu" {
			return packageCommandOptions{}, errors.New("--runtime은 cpu 또는 gpu여야 합니다")
		}
		if options.servicePort > 0 && options.healthcheckPath == "" {
			options.healthcheckPath = "/health"
		}
	}

	return options, nil
}

func (s *Shell) packageWizard(ctx context.Context, deploy bool) (packageCommandOptions, error) {
	s.section("Package 생성")
	fmt.Fprintln(s.out, s.paint(ansiDim, "웹과 같은 고정 규칙으로 Linux amd64 package를 생성합니다."))
	packageType, err := s.promptChoice("Package 유형", artifact.PresetAIOpsGeon, artifact.PresetAIOpsGeon, artifact.PackageTypeGo, artifact.PackageTypePy, artifact.PackageTypeNode, artifact.PackageTypeBin, artifact.PackageTypeSh)
	if err != nil {
		return packageCommandOptions{}, err
	}
	options := packageCommandOptions{packageType: packageType}
	if packageType == artifact.PresetAIOpsGeon {
		options.appVersion, err = s.prompt("App version (비우면 자동 생성)", "", false)
		if err != nil {
			return packageCommandOptions{}, err
		}
		portText, promptErr := s.prompt("Service port", "18089", true)
		if promptErr != nil {
			return packageCommandOptions{}, promptErr
		}
		options.servicePort, err = parseInt(portText, "Service port")
		if err != nil {
			return packageCommandOptions{}, err
		}
		options.runtimeType = "cpu"
	} else {
		options.sourcePath, err = s.prompt("Source ZIP 또는 파일", "", true)
		if err != nil {
			return packageCommandOptions{}, err
		}
		options.appName, err = s.prompt("App 이름", defaultPackageAppName(options.sourcePath), true)
		if err != nil {
			return packageCommandOptions{}, err
		}
		options.appVersion, err = s.prompt("App version", "0.1.0", true)
		if err != nil {
			return packageCommandOptions{}, err
		}
		options.entrypoint, err = s.prompt("Build/시작 경로", defaultPackageEntrypoint(packageType, options.sourcePath), true)
		if err != nil {
			return packageCommandOptions{}, err
		}
		options.runtimeType, err = s.promptChoice("Runtime", "cpu", "cpu", "gpu")
		if err != nil {
			return packageCommandOptions{}, err
		}
		portText, promptErr := s.prompt("Service port (없으면 Enter)", "", false)
		if promptErr != nil {
			return packageCommandOptions{}, promptErr
		}
		if portText != "" {
			options.servicePort, err = parseInt(portText, "Service port")
			if err != nil {
				return packageCommandOptions{}, err
			}
			options.healthcheckPath, err = s.prompt("Health path", "/health", true)
			if err != nil {
				return packageCommandOptions{}, err
			}
		}
	}

	if deploy {
		options.targetID, err = s.packageDeploymentProfiles(ctx, options.runtimeType)
		if err != nil {
			return packageCommandOptions{}, err
		}
	}
	return normalizePackageOptions(options, deploy)
}

func (s *Shell) packageDeploymentProfiles(ctx context.Context, runtimeType string) (string, error) {
	fmt.Fprintf(s.out, "\n%s Target의 종류가 %s인지 확인하세요.\n", s.paint(ansiDim, "배포 대상 선택:"), runtimeType)
	if err := s.showList(ctx, "/api/v1/target-profiles", []column{{title: "TARGET ID", path: "target_profile_id"}, {title: "TYPE", path: "runtime.runtime_type"}, {title: "CSP", path: "csp"}}); err != nil {
		return "", err
	}
	targetID, err := s.prompt("Target Profile hint (Enter=automatic selection)", "", false)
	if err != nil {
		return "", err
	}
	return targetID, nil
}

func (s *Shell) buildPackage(ctx context.Context, options packageCommandOptions) (model.PackageBuildResponse, error) {
	var (
		resp response
		err  error
	)
	if options.packageType == artifact.PresetAIOpsGeon {
		resp, err = s.call(ctx, http.MethodPost, "/api/v1/artifacts/packages", model.PackageBuildRequest{
			Preset:      options.packageType,
			AppVersion:  options.appVersion,
			ServicePort: options.servicePort,
		})
	} else {
		resp, err = s.callUploadedPackage(ctx, options)
	}
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	var result model.PackageBuildResponse
	if err := decodeCLIResponse(resp.body, &result); err != nil {
		return model.PackageBuildResponse{}, err
	}
	return result, nil
}

func (s *Shell) callUploadedPackage(ctx context.Context, options packageCommandOptions) (response, error) {
	source, err := openValidatedPackageSource(options.sourcePath, s.maxPackageUpload)
	if err != nil {
		return response{}, err
	}
	sourceOpen := true
	defer func() {
		if sourceOpen {
			closePackageFile(source, "package source cleanup failed")
		}
	}()

	bodyFile, cleanupBody, err := newTemporaryPackageBody()
	if err != nil {
		return response{}, err
	}
	defer cleanupBody()

	writer := multipart.NewWriter(bodyFile)
	if err := writeUploadedPackageFields(writer, options); err != nil {
		return response{}, err
	}
	if err := writeUploadedPackageSource(writer, source, options.sourcePath); err != nil {
		return response{}, err
	}
	closeErr := source.Close()
	sourceOpen = false
	if closeErr != nil {
		return response{}, fmt.Errorf("source 파일 닫기: %w", closeErr)
	}
	contentType := writer.FormDataContentType()
	if err := writer.Close(); err != nil {
		return response{}, fmt.Errorf("multipart 본문 완료: %w", err)
	}
	if _, err := bodyFile.Seek(0, io.SeekStart); err != nil {
		return response{}, fmt.Errorf("multipart 본문 되감기: %w", err)
	}
	return s.callBody(ctx, http.MethodPost, "/api/v1/artifacts/packages", bodyFile, contentType)
}

func openValidatedPackageSource(sourcePath string, maxUpload int64) (*os.File, error) {
	source, err := os.Open(sourcePath)
	if err != nil {
		return nil, fmt.Errorf("source 파일 열기: %w", err)
	}
	keepOpen := false
	defer func() {
		if !keepOpen {
			closePackageFile(source, "invalid package source cleanup failed")
		}
	}()

	info, err := source.Stat()
	if err != nil {
		return nil, fmt.Errorf("source 파일 확인: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() == 0 {
		return nil, errors.New("source는 비어 있지 않은 일반 파일이어야 합니다")
	}
	if info.Size() > maxUpload {
		return nil, fmt.Errorf("source는 최대 %s까지 업로드할 수 있습니다", packageUploadLimitLabel(maxUpload))
	}
	keepOpen = true
	return source, nil
}

func newTemporaryPackageBody() (*os.File, func(), error) {
	bodyFile, err := os.CreateTemp("", "appdeploy-cli-package-*.multipart")
	if err != nil {
		return nil, nil, fmt.Errorf("multipart 임시 파일 생성: %w", err)
	}
	bodyPath := bodyFile.Name()
	cleanup := func() {
		closePackageFile(bodyFile, "multipart temporary file close failed")
		if err := os.Remove(bodyPath); err != nil && !errors.Is(err, os.ErrNotExist) {
			log.Warn().Err(err).Str("component", "cli-package").Msg("multipart temporary file cleanup failed")
		}
	}
	return bodyFile, cleanup, nil
}

func closePackageFile(file *os.File, message string) {
	if err := file.Close(); err != nil {
		log.Warn().Err(err).Str("component", "cli-package").Msg(message)
	}
}

func writeUploadedPackageFields(writer *multipart.Writer, options packageCommandOptions) error {
	fields := []struct {
		name  string
		value string
	}{
		{name: "package_type", value: options.packageType},
		{name: "app_name", value: options.appName},
		{name: "app_version", value: options.appVersion},
		{name: "entrypoint", value: options.entrypoint},
		{name: "runtime_type", value: options.runtimeType},
		{name: "healthcheck_path", value: options.healthcheckPath},
	}
	if options.servicePort > 0 {
		fields = append(fields, struct {
			name  string
			value string
		}{name: "service_port", value: strconv.Itoa(options.servicePort)})
	}
	for _, field := range fields {
		if field.value == "" {
			continue
		}
		if err := writer.WriteField(field.name, field.value); err != nil {
			return fmt.Errorf("multipart %s 필드 쓰기: %w", field.name, err)
		}
	}
	return nil
}

func writeUploadedPackageSource(writer *multipart.Writer, source io.Reader, sourcePath string) error {
	part, err := writer.CreateFormFile("source", filepath.Base(sourcePath))
	if err != nil {
		return fmt.Errorf("multipart source 필드 생성: %w", err)
	}
	if _, err := io.Copy(part, source); err != nil {
		return fmt.Errorf("multipart source 복사: %w", err)
	}
	return nil
}

func packageUploadLimitLabel(value int64) string {
	if value >= 1<<20 && value%(1<<20) == 0 {
		return fmt.Sprintf("%d MiB", value>>20)
	}
	return fmt.Sprintf("%d bytes", value)
}

func (s *Shell) printValue(value any) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode output: %w", err)
	}
	return s.printJSON(raw)
}

func decodeCLIResponse(raw []byte, target any) error {
	if err := json.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("decode API response: %w", err)
	}
	return nil
}

func isPackageCommandType(value string) bool {
	switch value {
	case artifact.PresetAIOpsGeon, artifact.PackageTypeGo, artifact.PackageTypePy, artifact.PackageTypeNode, artifact.PackageTypeBin, artifact.PackageTypeSh:
		return true
	default:
		return false
	}
}

func defaultPackageEntrypoint(packageType, sourcePath string) string {
	if packageType != artifact.PackageTypeGo && !strings.EqualFold(filepath.Ext(sourcePath), ".zip") {
		return filepath.Base(sourcePath)
	}
	switch packageType {
	case artifact.PackageTypeGo:
		return "."
	case artifact.PackageTypePy:
		return "main.py"
	case artifact.PackageTypeNode:
		return "index.js"
	case artifact.PackageTypeSh:
		return "run.sh"
	default:
		return ""
	}
}

func defaultPackageAppName(sourcePath string) string {
	name := filepath.Base(strings.TrimSpace(sourcePath))
	name = strings.TrimSuffix(name, filepath.Ext(name))
	name = strings.ToLower(name)
	var output strings.Builder
	lastHyphen := false
	for _, char := range name {
		if (char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') {
			output.WriteRune(char)
			lastHyphen = false
			continue
		}
		if output.Len() > 0 && !lastHyphen {
			output.WriteByte('-')
			lastHyphen = true
		}
	}
	name = strings.Trim(output.String(), "-")
	if len(name) < 2 {
		name = "app-" + name
	}
	name = strings.Trim(strings.TrimSpace(name), "-")
	if name == "app-" || name == "" {
		name = "app-upload"
	}
	if len(name) > 63 {
		name = strings.TrimRight(name[:63], "-")
	}
	return name
}
