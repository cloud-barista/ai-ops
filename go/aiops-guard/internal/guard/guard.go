package guard

import (
	"bytes"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
)

const (
	ModeMock   = "mock"
	ModeDryRun = "dry-run"
	ModeReal   = "real"

	ActionObserveOnly    = "observe_only"
	ActionRolloutRestart = "rollout_restart"
	ActionScaleOut       = "scale_out"
)

var kubernetesNamePattern = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)

type Request struct {
	Mode               string   `json:"mode"`
	Namespace          string   `json:"namespace"`
	Deployment         string   `json:"deployment"`
	Action             string   `json:"action"`
	Replicas           *int     `json:"replicas,omitempty"`
	AllowedNamespaces  []string `json:"allowed_namespaces"`
	AllowedDeployments []string `json:"allowed_deployments"`
	MinReplicas        int      `json:"min_replicas"`
	MaxReplicas        int      `json:"max_replicas"`
}

type Result struct {
	Command string `json:"command"`
	Mode    string `json:"mode"`
	Valid   bool   `json:"valid"`
	Stdout  string `json:"stdout"`
	Stderr  string `json:"stderr"`
}

type Runner func(name string, args ...string) (stdout string, stderr string, exitCode int)

func Execute(req Request, runner Runner) Result {
	args, err := BuildKubectlArgs(req)
	command := ""
	if len(args) > 0 {
		command = CommandString(args)
	}
	if err != nil {
		return Result{
			Command: command,
			Mode:    req.Mode,
			Valid:   false,
			Stderr:  err.Error(),
		}
	}

	if req.Mode == ModeMock {
		return Result{
			Command: command,
			Mode:    req.Mode,
			Valid:   true,
			Stdout:  "mock: command validated and not executed",
		}
	}

	if runner == nil {
		runner = DefaultRunner
	}

	stdout, stderr, exitCode := runner(args[0], args[1:]...)
	return Result{
		Command: command,
		Mode:    req.Mode,
		Valid:   exitCode == 0,
		Stdout:  strings.TrimSpace(stdout),
		Stderr:  strings.TrimSpace(stderr),
	}
}

func BuildKubectlArgs(req Request) ([]string, error) {
	if err := Validate(req); err != nil {
		return nil, err
	}

	switch req.Action {
	case ActionObserveOnly:
		return []string{"kubectl", "get", "deployment", req.Deployment, "-n", req.Namespace, "-o", "json"}, nil
	case ActionRolloutRestart:
		args := []string{"kubectl", "rollout", "restart", "deployment", req.Deployment, "-n", req.Namespace}
		if req.Mode == ModeDryRun {
			args = append(args, "--dry-run=server")
		}
		return args, nil
	case ActionScaleOut:
		args := []string{"kubectl", "scale", "deployment", req.Deployment, fmt.Sprintf("--replicas=%d", *req.Replicas), "-n", req.Namespace}
		if req.Mode == ModeDryRun {
			args = append(args, "--dry-run=server")
		}
		return args, nil
	default:
		return nil, fmt.Errorf("unsupported action: %s", req.Action)
	}
}

func Validate(req Request) error {
	switch req.Mode {
	case ModeMock, ModeDryRun, ModeReal:
	default:
		return fmt.Errorf("unsupported mode: %s", req.Mode)
	}

	if err := validateKubernetesName("namespace", req.Namespace); err != nil {
		return err
	}
	if err := validateKubernetesName("deployment", req.Deployment); err != nil {
		return err
	}
	if !contains(req.AllowedNamespaces, req.Namespace) {
		return fmt.Errorf("namespace is not allowed: %s", req.Namespace)
	}
	if !contains(req.AllowedDeployments, req.Deployment) {
		return fmt.Errorf("deployment is not allowed: %s", req.Deployment)
	}

	switch req.Action {
	case ActionObserveOnly, ActionRolloutRestart:
		if req.Replicas != nil {
			return fmt.Errorf("only %s accepts replicas", ActionScaleOut)
		}
		return nil
	case ActionScaleOut:
		if req.Replicas == nil {
			return fmt.Errorf("replicas is required for action: %s", ActionScaleOut)
		}
		if req.MinReplicas <= 0 || req.MaxReplicas < req.MinReplicas {
			return fmt.Errorf("invalid replica policy: min=%d max=%d", req.MinReplicas, req.MaxReplicas)
		}
		if *req.Replicas < req.MinReplicas || *req.Replicas > req.MaxReplicas {
			return fmt.Errorf("replicas must be between %d and %d: %d", req.MinReplicas, req.MaxReplicas, *req.Replicas)
		}
		return nil
	default:
		return fmt.Errorf("unsupported action: %s", req.Action)
	}
}

func CommandString(args []string) string {
	return strings.Join(args, " ")
}

func DefaultRunner(name string, args ...string) (string, string, int) {
	cmd := exec.Command(name, args...)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return stdout.String(), combineError(stderr.String(), err), 1
	}
	return stdout.String(), stderr.String(), 0
}

func validateKubernetesName(field string, value string) error {
	if value == "" {
		return fmt.Errorf("%s is required", field)
	}
	if len(value) > 63 {
		return fmt.Errorf("%s must be 63 characters or fewer: %s", field, value)
	}
	if !kubernetesNamePattern.MatchString(value) {
		return fmt.Errorf("%s is not a valid Kubernetes DNS label: %s", field, value)
	}
	return nil
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func combineError(stderr string, err error) string {
	stderr = strings.TrimSpace(stderr)
	if stderr == "" {
		return err.Error()
	}
	return stderr + "\n" + err.Error()
}
