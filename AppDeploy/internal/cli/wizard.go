package cli

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/khu/ai-app-deployer/internal/model"
)

func (s *Shell) fileOrAppWizard(args []string) (any, error) {
	if len(args) > 1 {
		return nil, errors.New("사용법: apps add [json-file]")
	}
	if len(args) == 1 {
		return readJSONFile(args[0])
	}
	s.section("AI App 등록")
	fmt.Fprintln(s.out, s.paint(ansiDim, "필수 항목은 빈 값으로 둘 수 없습니다."))
	name, err := s.prompt("앱 이름 (소문자/숫자/하이픈)", "", true)
	if err != nil {
		return nil, err
	}
	version, err := s.prompt("버전", "0.1.0", true)
	if err != nil {
		return nil, err
	}
	description, err := s.prompt("설명", "", false)
	if err != nil {
		return nil, err
	}
	artifactType, err := s.promptChoice("Artifact 종류", "script", "package", "git", "binary", "script")
	if err != nil {
		return nil, err
	}
	artifactURI, err := s.prompt("Artifact URI", "", true)
	if err != nil {
		return nil, err
	}
	command, err := s.prompt("실행 명령", "bash", true)
	if err != nil {
		return nil, err
	}
	argsLine, err := s.prompt("실행 인자 (공백 구분, 따옴표 사용 가능)", "", false)
	if err != nil {
		return nil, err
	}
	entrypointArgs, err := tokenize(argsLine)
	if err != nil {
		return nil, fmt.Errorf("실행 인자: %w", err)
	}
	runtimeType, err := s.promptChoice("Runtime", "cpu", "mock", "cpu", "gpu", "aiinfra")
	if err != nil {
		return nil, err
	}
	accelerator := "none"
	if runtimeType == "gpu" {
		accelerator = "nvidia"
	}
	cpu, err := s.prompt("CPU 요구량", "1", false)
	if err != nil {
		return nil, err
	}
	memory, err := s.prompt("메모리 요구량", "1Gi", false)
	if err != nil {
		return nil, err
	}
	gpuDefault := "0"
	if runtimeType == "gpu" {
		gpuDefault = "1"
	}
	gpu, err := s.prompt("GPU 요구 개수", gpuDefault, false)
	if err != nil {
		return nil, err
	}
	storage, err := s.prompt("저장소 요구량", "1Gi", false)
	if err != nil {
		return nil, err
	}
	portText, err := s.prompt("서비스 포트 (없으면 Enter)", "", false)
	if err != nil {
		return nil, err
	}

	spec := model.AppSpec{
		SchemaVersion: "appspec.khu.ai/v1alpha1",
		Kind:          "AIApp",
		Metadata: model.Metadata{
			Name:        name,
			Version:     version,
			Description: description,
		},
		Artifact: model.Artifact{Type: artifactType, URI: artifactURI},
		Entrypoint: model.Entrypoint{
			Command: command,
			Args:    entrypointArgs,
		},
		Runtime:   model.AppRuntime{Type: runtimeType, Accelerator: accelerator},
		Resources: model.Resources{CPU: cpu, Memory: memory, GPU: gpu, Storage: storage},
	}
	if portText != "" {
		port, parseErr := parseInt(portText, "서비스 포트")
		if parseErr != nil {
			return nil, parseErr
		}
		spec.Network = &model.Network{Ports: []model.Port{{Name: "http", AppPort: port, Protocol: "TCP"}}}
		spec.Healthcheck = &model.Healthcheck{Type: "http", Path: "/health"}
	}
	return model.AppCreateRequest{AppSpec: spec}, nil
}

func (s *Shell) fileOrTargetWizard(args []string) (any, error) {
	if len(args) > 1 {
		return nil, errors.New("사용법: targets add [json-file]")
	}
	if len(args) == 1 {
		return readJSONFile(args[0])
	}
	s.section("Target Profile 등록")
	runtimeType, err := s.promptChoice("Target Runtime", "cpu", "mock", "cpu", "gpu", "aiinfra")
	if err != nil {
		return nil, err
	}
	defaults := runtimeDefaults(runtimeType)
	id, err := s.prompt("Target Profile ID", "target-"+runtimeType+"-001", true)
	if err != nil {
		return nil, err
	}
	name, err := s.prompt("표시 이름", runtimeType+"-target", false)
	if err != nil {
		return nil, err
	}
	cspDefault := "local"
	if runtimeType == "mock" {
		cspDefault = "mock"
	} else if runtimeType == "aiinfra" {
		cspDefault = "etri"
	}
	csp, err := s.promptChoice("CSP", cspDefault, "aws", "azure", "gcp", "etri", "local", "mock")
	if err != nil {
		return nil, err
	}
	host := ""
	if csp != "mock" {
		host, err = s.prompt("VM/Gateway host", "", true)
		if err != nil {
			return nil, err
		}
	}
	sshPort := 0
	credentialRef := ""
	if runtimeType == "cpu" || runtimeType == "gpu" {
		portText, promptErr := s.prompt("SSH port", "22", true)
		if promptErr != nil {
			return nil, promptErr
		}
		sshPort, err = parseInt(portText, "SSH port")
		if err != nil {
			return nil, err
		}
		credentialRef, err = s.prompt("Credential ref (secret 값 아님)", "cred://local/"+runtimeType+"-vm-001", false)
		if err != nil {
			return nil, err
		}
	}
	profile := model.TargetProfile{
		TargetProfileID: id,
		Name:            name,
		CSP:             csp,
		VM: model.VMProfile{
			Host:          host,
			SSHPort:       sshPort,
			CredentialRef: credentialRef,
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   runtimeType,
			Accelerator:   defaults.accelerator,
			OperatingMode: defaults.mode,
		},
	}
	if runtimeType == "gpu" {
		profile.GPU = &model.GPUProfile{Vendor: "nvidia", Count: 1, DriverRequired: true}
	}
	if runtimeType == "cpu" || runtimeType == "gpu" {
		profile.Storage = &model.Storage{
			ArtifactDir: "/tmp/aiapp/artifacts",
			ModelDir:    "/tmp/aiapp/models",
			LogDir:      "/tmp/aiapp/logs",
		}
	}
	return profile, nil
}

func (s *Shell) inferenceWizard() (any, error) {
	s.section("Inference 요청")
	method, err := s.promptChoice("HTTP method", "POST", "GET", "POST")
	if err != nil {
		return nil, err
	}
	path, err := s.prompt("App path", "/generate", true)
	if err != nil {
		return nil, err
	}
	timeoutText, err := s.prompt("Timeout seconds", "30", true)
	if err != nil {
		return nil, err
	}
	timeout, err := parseInt(timeoutText, "Timeout seconds")
	if err != nil {
		return nil, err
	}
	bodyText, err := s.prompt("JSON body (GET 또는 본문 없음은 Enter)", "", false)
	if err != nil {
		return nil, err
	}
	req := model.InferenceInvokeRequest{Method: method, Path: path, TimeoutSeconds: timeout}
	if bodyText != "" {
		if !json.Valid([]byte(bodyText)) {
			return nil, errors.New("JSON body가 올바르지 않습니다")
		}
		req.Body = json.RawMessage(bodyText)
	}
	return req, nil
}

func (s *Shell) metricWizard() (any, error) {
	s.section("Metric 기록")
	latencyText, err := s.prompt("Latency ms", "0", true)
	if err != nil {
		return nil, err
	}
	throughputText, err := s.prompt("Throughput rps", "0", true)
	if err != nil {
		return nil, err
	}
	requestCountText, err := s.prompt("Request count", "0", true)
	if err != nil {
		return nil, err
	}
	errorCountText, err := s.prompt("Error count", "0", true)
	if err != nil {
		return nil, err
	}
	var latency, throughput float64
	if _, err := fmt.Sscan(latencyText, &latency); err != nil {
		return nil, errors.New("Latency ms에는 숫자를 입력하세요")
	}
	if _, err := fmt.Sscan(throughputText, &throughput); err != nil {
		return nil, errors.New("Throughput rps에는 숫자를 입력하세요")
	}
	requestCount, err := parseInt(requestCountText, "Request count")
	if err != nil {
		return nil, err
	}
	errorCount, err := parseInt(errorCountText, "Error count")
	if err != nil {
		return nil, err
	}
	return model.InferenceMetricCreateRequest{
		LatencyMS:     latency,
		ThroughputRPS: throughput,
		RequestCount:  requestCount,
		ErrorCount:    errorCount,
	}, nil
}

func (s *Shell) prompt(label, defaultValue string, required bool) (string, error) {
	for {
		if defaultValue == "" {
			fmt.Fprintf(s.out, "%s: ", s.paint(ansiCyan, label))
		} else {
			fmt.Fprintf(s.out, "%s %s: ", s.paint(ansiCyan, label), s.paint(ansiDim, "["+defaultValue+"]"))
		}
		line, err := s.reader.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return "", fmt.Errorf("입력 읽기: %w", err)
		}
		value := strings.TrimSpace(line)
		if value == "" {
			value = defaultValue
		}
		if value != "" || !required {
			return value, nil
		}
		if errors.Is(err, io.EOF) {
			return "", io.EOF
		}
		s.warning("값을 입력하세요.")
	}
}

func (s *Shell) promptChoice(label, defaultValue string, allowed ...string) (string, error) {
	for {
		value, err := s.prompt(label+" ("+strings.Join(allowed, "/")+")", defaultValue, true)
		if err != nil {
			return "", err
		}
		for _, candidate := range allowed {
			if strings.EqualFold(value, candidate) {
				return candidate, nil
			}
		}
		s.warning("허용 값: " + strings.Join(allowed, ", "))
	}
}

type runtimeDefaultValues struct {
	accelerator string
	adapter     string
	mode        string
}

func runtimeDefaults(runtimeType string) runtimeDefaultValues {
	switch runtimeType {
	case "mock":
		return runtimeDefaultValues{accelerator: "none", adapter: "mock", mode: "local_mock"}
	case "gpu":
		return runtimeDefaultValues{accelerator: "nvidia", adapter: "gpu_vm", mode: "vm_process"}
	case "aiinfra":
		return runtimeDefaultValues{accelerator: "none", adapter: "etri_aiinfra", mode: "remote_api"}
	default:
		return runtimeDefaultValues{accelerator: "none", adapter: "cpu_vm", mode: "vm_process"}
	}
}
