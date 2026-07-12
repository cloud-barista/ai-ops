package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"

	"github.com/khu/ai-app-deployer/internal/model"
)

func (s *Shell) apps(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: apps list | get <app-id> | add [json-file]")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls":
		return s.showList(ctx, "/api/v1/apps", []column{
			{title: "APP ID", path: "app_id"},
			{title: "VERSION ID", path: "app_version_id"},
			{title: "NAME", path: "name"},
			{title: "VERSION", path: "version"},
		})
	case "get", "show":
		if len(args) != 2 {
			return errors.New("사용법: apps get <app-id>")
		}
		return s.showJSON(ctx, http.MethodGet, "/api/v1/apps/"+url.PathEscape(args[1]), nil)
	case "add", "create":
		payload, err := s.fileOrAppWizard(args[1:])
		if err != nil {
			return err
		}
		return s.showJSON(ctx, http.MethodPost, "/api/v1/apps", payload)
	default:
		return fmt.Errorf("알 수 없는 apps 작업 %q입니다", args[0])
	}
}

func (s *Shell) runtimes(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: runtimes list | add [json-file]")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls":
		return s.showList(ctx, "/api/v1/runtime-profiles", []column{
			{title: "RUNTIME ID", path: "runtime_profile_id"},
			{title: "TYPE", path: "runtime_type"},
			{title: "ADAPTER", path: "adapter_type"},
			{title: "MODE", path: "operating_mode"},
		})
	case "add", "create":
		payload, err := s.fileOrRuntimeWizard(args[1:])
		if err != nil {
			return err
		}
		return s.showJSON(ctx, http.MethodPost, "/api/v1/runtime-profiles", payload)
	default:
		return fmt.Errorf("알 수 없는 runtimes 작업 %q입니다", args[0])
	}
}

func (s *Shell) targets(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: targets list | add [json-file]")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls":
		return s.showList(ctx, "/api/v1/target-profiles", []column{
			{title: "TARGET ID", path: "target_profile_id"},
			{title: "CSP", path: "csp"},
			{title: "RUNTIME", path: "runtime.runtime_type"},
			{title: "HOST", path: "vm.host"},
		})
	case "add", "create":
		payload, err := s.fileOrTargetWizard(args[1:])
		if err != nil {
			return err
		}
		return s.showJSON(ctx, http.MethodPost, "/api/v1/target-profiles", payload)
	default:
		return fmt.Errorf("알 수 없는 targets 작업 %q입니다", args[0])
	}
}

func (s *Shell) resources(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: resources list | check <target-id> [runtime-id]")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls", "inventory":
		return s.showList(ctx, "/api/v1/resources/inventory", []column{
			{title: "TARGET ID", path: "target_profile_id"},
			{title: "RUNTIME", path: "runtime_health"},
			{title: "CPU", path: "cpu_available"},
			{title: "GPU", path: "gpu_available"},
			{title: "STORAGE", path: "storage_available"},
		})
	case "check":
		if len(args) < 2 || len(args) > 3 {
			return errors.New("사용법: resources check <target-id> [runtime-id]")
		}
		req := model.ResourceCheckRequest{TargetProfileID: args[1]}
		if len(args) == 3 {
			req.RuntimeProfileID = args[2]
		}
		return s.showJSON(ctx, http.MethodPost, "/api/v1/resources/check", req)
	default:
		return fmt.Errorf("알 수 없는 resources 작업 %q입니다", args[0])
	}
}

func (s *Shell) deployments(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: deployments list | get | create | logs | stop")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls":
		return s.showList(ctx, "/api/v1/deployments", deploymentColumns())
	case "get", "show", "status":
		if len(args) != 2 {
			return errors.New("사용법: deployments get <deployment-id>")
		}
		return s.showJSON(ctx, http.MethodGet, deploymentPath(args[1]), nil)
	case "create", "add", "run":
		req, err := s.deploymentRequest(ctx, args[1:])
		if err != nil {
			return err
		}
		return s.showJSON(ctx, http.MethodPost, "/api/v1/deployments", req)
	case "logs", "log":
		if len(args) < 2 || len(args) > 3 {
			return errors.New("사용법: deployments logs <deployment-id> [stage]")
		}
		path := deploymentPath(args[1]) + "/logs"
		if len(args) == 3 {
			path += "?stage=" + url.QueryEscape(strings.ToUpper(args[2]))
		}
		return s.showList(ctx, path, []column{
			{title: "TIME", path: "timestamp"},
			{title: "LEVEL", path: "level"},
			{title: "STAGE", path: "stage"},
			{title: "COMPONENT", path: "component"},
			{title: "MESSAGE", path: "message"},
		})
	case "stop":
		if len(args) != 2 {
			return errors.New("사용법: deployments stop <deployment-id>")
		}
		return s.showJSON(ctx, http.MethodPost, deploymentPath(args[1])+"/stop", nil)
	default:
		return fmt.Errorf("알 수 없는 deployments 작업 %q입니다", args[0])
	}
}

func (s *Shell) monitoring(ctx context.Context, args []string) error {
	if len(args) != 1 {
		return errors.New("사용법: monitoring summary | health | alarms | metrics")
	}
	switch strings.ToLower(args[0]) {
	case "summary":
		return s.showJSON(ctx, http.MethodGet, "/api/v1/monitoring/summary", nil)
	case "health", "runtime-health":
		return s.showList(ctx, "/api/v1/monitoring/runtime-health", []column{
			{title: "TARGET ID", path: "target_profile_id"},
			{title: "STATUS", path: "status"},
			{title: "RUNTIME", path: "runtime_health"},
			{title: "GPU", path: "gpu_available"},
			{title: "CHECKED AT", path: "last_checked_at"},
		})
	case "alarms":
		return s.showList(ctx, "/api/v1/monitoring/alarms", []column{
			{title: "SEVERITY", path: "severity"},
			{title: "CODE", path: "error_code"},
			{title: "COUNT", path: "count"},
			{title: "DEPLOYMENT", path: "latest_deployment_id"},
			{title: "MESSAGE", path: "latest_message"},
		})
	case "metrics":
		return s.showMetricList(ctx, "/api/v1/monitoring/metrics")
	default:
		return fmt.Errorf("알 수 없는 monitoring 작업 %q입니다", args[0])
	}
}

func (s *Shell) inference(ctx context.Context, args []string) error {
	if len(args) < 2 {
		return errors.New("사용법: inference health <deployment-id> | invoke <deployment-id> [json-file]")
	}
	deploymentID := url.PathEscape(args[1])
	switch strings.ToLower(args[0]) {
	case "health":
		if len(args) != 2 {
			return errors.New("사용법: inference health <deployment-id>")
		}
		return s.showJSON(ctx, http.MethodGet, "/api/v1/inference/"+deploymentID+"/health", nil)
	case "invoke", "run":
		var payload any
		var err error
		if len(args) == 3 {
			payload, err = readJSONFile(args[2])
		} else if len(args) == 2 {
			payload, err = s.inferenceWizard()
		} else {
			return errors.New("사용법: inference invoke <deployment-id> [json-file]")
		}
		if err != nil {
			return err
		}
		return s.showJSON(ctx, http.MethodPost, "/api/v1/inference/"+deploymentID+"/invoke", payload)
	default:
		return fmt.Errorf("알 수 없는 inference 작업 %q입니다", args[0])
	}
}

func (s *Shell) metrics(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: metrics list [deployment-id] | add <deployment-id> [json-file]")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls":
		if len(args) > 2 {
			return errors.New("사용법: metrics list [deployment-id]")
		}
		path := "/api/v1/monitoring/metrics"
		if len(args) == 2 {
			path = deploymentPath(args[1]) + "/metrics"
		}
		return s.showMetricList(ctx, path)
	case "add", "create":
		if len(args) < 2 || len(args) > 3 {
			return errors.New("사용법: metrics add <deployment-id> [json-file]")
		}
		var payload any
		var err error
		if len(args) == 3 {
			payload, err = readJSONFile(args[2])
		} else {
			payload, err = s.metricWizard()
		}
		if err != nil {
			return err
		}
		return s.showJSON(ctx, http.MethodPost, deploymentPath(args[1])+"/metrics", payload)
	default:
		return fmt.Errorf("알 수 없는 metrics 작업 %q입니다", args[0])
	}
}

func (s *Shell) raw(ctx context.Context, args []string) error {
	if len(args) < 2 || len(args) > 3 {
		return errors.New("사용법: raw <GET|POST> </api/v1/path> [@json-file|inline-json]")
	}
	method := strings.ToUpper(args[0])
	if method != http.MethodGet && method != http.MethodPost {
		return errors.New("raw 명령은 GET과 POST만 지원합니다")
	}
	path := args[1]
	if !strings.HasPrefix(path, "/api/v1/") && path != "/api/v1" {
		return errors.New("path는 /api/v1로 시작해야 합니다")
	}
	var payload any
	if len(args) == 3 {
		var err error
		if strings.HasPrefix(args[2], "@") {
			payload, err = readJSONFile(strings.TrimPrefix(args[2], "@"))
		} else {
			raw := []byte(args[2])
			if !json.Valid(raw) {
				return errors.New("inline JSON이 올바르지 않습니다")
			}
			payload = raw
		}
		if err != nil {
			return err
		}
	}
	return s.showJSON(ctx, method, path, payload)
}

func (s *Shell) deploymentRequest(ctx context.Context, args []string) (model.DeploymentCreateRequest, error) {
	if len(args) == 3 {
		return model.DeploymentCreateRequest{
			AppVersionID:     args[0],
			RuntimeProfileID: args[1],
			TargetProfileID:  args[2],
			RequestedBy:      "appdeployer-cli",
		}, nil
	}
	if len(args) != 0 {
		return model.DeploymentCreateRequest{}, errors.New("사용법: deployments create [app-version-id runtime-id target-id]")
	}
	fmt.Fprintln(s.out, "등록된 항목을 확인한 뒤 배포 식별자를 입력하세요.")
	if err := s.showList(ctx, "/api/v1/apps", []column{{title: "APP VERSION ID", path: "app_version_id"}, {title: "NAME", path: "name"}, {title: "VERSION", path: "version"}}); err != nil {
		return model.DeploymentCreateRequest{}, err
	}
	if err := s.showList(ctx, "/api/v1/runtime-profiles", []column{{title: "RUNTIME ID", path: "runtime_profile_id"}, {title: "TYPE", path: "runtime_type"}}); err != nil {
		return model.DeploymentCreateRequest{}, err
	}
	if err := s.showList(ctx, "/api/v1/target-profiles", []column{{title: "TARGET ID", path: "target_profile_id"}, {title: "RUNTIME", path: "runtime.runtime_type"}}); err != nil {
		return model.DeploymentCreateRequest{}, err
	}
	appVersionID, err := s.prompt("App Version ID", "", true)
	if err != nil {
		return model.DeploymentCreateRequest{}, err
	}
	runtimeID, err := s.prompt("Runtime Profile ID", "", true)
	if err != nil {
		return model.DeploymentCreateRequest{}, err
	}
	targetID, err := s.prompt("Target Profile ID", "", true)
	if err != nil {
		return model.DeploymentCreateRequest{}, err
	}
	return model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: runtimeID,
		TargetProfileID:  targetID,
		RequestedBy:      "appdeployer-cli",
	}, nil
}

func (s *Shell) showMetricList(ctx context.Context, path string) error {
	return s.showList(ctx, path, []column{
		{title: "METRIC ID", path: "metric_id"},
		{title: "DEPLOYMENT", path: "deployment_id"},
		{title: "LATENCY MS", path: "latency_ms"},
		{title: "RPS", path: "throughput_rps"},
		{title: "ERRORS", path: "error_count"},
		{title: "TIMESTAMP", path: "timestamp"},
	})
}

func deploymentColumns() []column {
	return []column{
		{title: "DEPLOYMENT ID", path: "deployment_id"},
		{title: "STATUS", path: "status"},
		{title: "APP VERSION", path: "app_version_id"},
		{title: "RUNTIME", path: "runtime_profile_id"},
		{title: "TARGET", path: "target_profile_id"},
	}
}

func deploymentPath(id string) string {
	return "/api/v1/deployments/" + url.PathEscape(id)
}

func readJSONFile(path string) ([]byte, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("JSON 파일 읽기: %w", err)
	}
	if !json.Valid(raw) {
		return nil, fmt.Errorf("%s 파일의 JSON이 올바르지 않습니다", path)
	}
	return raw, nil
}

func parseInt(value, field string) (int, error) {
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return 0, fmt.Errorf("%s에는 정수를 입력하세요", field)
	}
	return parsed, nil
}
