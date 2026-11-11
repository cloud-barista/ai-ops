package guard

import (
	"fmt"
	"regexp"
)

const (
	ModeMock     = "mock"
	ModeValidate = "validate"

	ActionObserveOnly    = "observe_only"
	ActionRestartService = "restart_service"
	ActionScaleOut       = "scale_out"
)

var identifierPattern = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)

type Request struct {
	Mode             string   `json:"mode"`
	Service          string   `json:"service"`
	TargetResource   string   `json:"target_resource"`
	Action           string   `json:"action"`
	Instances        *int     `json:"instances,omitempty"`
	AllowedServices  []string `json:"allowed_services"`
	AllowedResources []string `json:"allowed_resources"`
	MinInstances     int      `json:"min_instances"`
	MaxInstances     int      `json:"max_instances"`
}

type Result struct {
	ActionPlan string `json:"action_plan"`
	Mode       string `json:"mode"`
	Valid      bool   `json:"valid"`
	Reason     string `json:"reason"`
}

func Execute(req Request) Result {
	plan, err := BuildActionPlan(req)
	if err != nil {
		return Result{
			Mode:   req.Mode,
			Valid:  false,
			Reason: err.Error(),
		}
	}

	return Result{
		ActionPlan: plan,
		Mode:       req.Mode,
		Valid:      true,
		Reason:     "bounded VM service-control action passed policy validation",
	}
}

func BuildActionPlan(req Request) (string, error) {
	if err := Validate(req); err != nil {
		return "", err
	}

	base := fmt.Sprintf("service=%s target_resource=%s", req.Service, req.TargetResource)
	switch req.Action {
	case ActionObserveOnly:
		return "observe " + base, nil
	case ActionRestartService:
		return "restart " + base, nil
	case ActionScaleOut:
		return fmt.Sprintf("scale %s instances=%d", base, *req.Instances), nil
	default:
		return "", fmt.Errorf("unsupported action: %s", req.Action)
	}
}

func Validate(req Request) error {
	switch req.Mode {
	case ModeMock, ModeValidate:
	default:
		return fmt.Errorf("unsupported mode: %s", req.Mode)
	}

	if err := validateIdentifier("service", req.Service); err != nil {
		return err
	}
	if err := validateIdentifier("target_resource", req.TargetResource); err != nil {
		return err
	}
	if !contains(req.AllowedServices, req.Service) {
		return fmt.Errorf("service is not allowed: %s", req.Service)
	}
	if !contains(req.AllowedResources, req.TargetResource) {
		return fmt.Errorf("target resource is not allowed: %s", req.TargetResource)
	}

	switch req.Action {
	case ActionObserveOnly, ActionRestartService:
		if req.Instances != nil {
			return fmt.Errorf("only %s accepts instances", ActionScaleOut)
		}
		return nil
	case ActionScaleOut:
		if req.Instances == nil {
			return fmt.Errorf("instances is required for action: %s", ActionScaleOut)
		}
		if req.MinInstances <= 0 || req.MaxInstances < req.MinInstances {
			return fmt.Errorf("invalid instance policy: min=%d max=%d", req.MinInstances, req.MaxInstances)
		}
		if *req.Instances < req.MinInstances || *req.Instances > req.MaxInstances {
			return fmt.Errorf("instances must be between %d and %d: %d", req.MinInstances, req.MaxInstances, *req.Instances)
		}
		return nil
	default:
		return fmt.Errorf("unsupported action: %s", req.Action)
	}
}

func validateIdentifier(field string, value string) error {
	if value == "" {
		return fmt.Errorf("%s is required", field)
	}
	if len(value) > 63 {
		return fmt.Errorf("%s must be 63 characters or fewer: %s", field, value)
	}
	if !identifierPattern.MatchString(value) {
		return fmt.Errorf("%s is not a valid identifier: %s", field, value)
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
