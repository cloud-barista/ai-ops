package guard

import (
	"strings"
	"testing"
)

func ptr(v int) *int {
	return &v
}

func validScaleRequest() Request {
	return Request{
		Mode:             ModeMock,
		Service:          "aiops-service",
		TargetResource:   "gpu-vm-l4",
		Action:           ActionScaleOut,
		Instances:        ptr(3),
		AllowedServices:  []string{"aiops-service", "aiops-worker"},
		AllowedResources: []string{"cpu-vm-standard", "gpu-vm-l4"},
		MinInstances:     1,
		MaxInstances:     5,
	}
}

func TestScaleOutBuildsStableVMActionPlan(t *testing.T) {
	result := Execute(validScaleRequest())

	if !result.Valid {
		t.Fatalf("expected valid result, got reason=%q", result.Reason)
	}

	want := "scale service=aiops-service target_resource=gpu-vm-l4 instances=3"
	if result.ActionPlan != want {
		t.Fatalf("action plan mismatch\nwant: %s\n got: %s", want, result.ActionPlan)
	}
}

func TestRejectsServiceOutsideAllowlist(t *testing.T) {
	req := validScaleRequest()
	req.Service = "unknown-service"

	result := Execute(req)

	if result.Valid {
		t.Fatal("expected invalid result for service outside policy")
	}
	if !strings.Contains(result.Reason, "service is not allowed") {
		t.Fatalf("unexpected reason: %q", result.Reason)
	}
}

func TestRejectsResourceOutsideAllowlist(t *testing.T) {
	req := validScaleRequest()
	req.TargetResource = "gpu-vm-unknown"

	result := Execute(req)

	if result.Valid {
		t.Fatal("expected invalid result for resource outside policy")
	}
	if !strings.Contains(result.Reason, "target resource is not allowed") {
		t.Fatalf("unexpected reason: %q", result.Reason)
	}
}

func TestRejectsInstancesAboveMax(t *testing.T) {
	req := validScaleRequest()
	req.Instances = ptr(9)

	result := Execute(req)

	if result.Valid {
		t.Fatal("expected invalid result for instance count above max")
	}
	if !strings.Contains(result.Reason, "instances must be between") {
		t.Fatalf("unexpected reason: %q", result.Reason)
	}
}

func TestRejectsUnsupportedAction(t *testing.T) {
	req := validScaleRequest()
	req.Action = "delete_vm"

	result := Execute(req)

	if result.Valid {
		t.Fatal("expected invalid result for unsupported action")
	}
	if !strings.Contains(result.Reason, "unsupported action") {
		t.Fatalf("unexpected reason: %q", result.Reason)
	}
}

func TestValidateModeBuildsActionPlanWithoutInfrastructureExecution(t *testing.T) {
	req := validScaleRequest()
	req.Mode = ModeValidate

	result := Execute(req)

	if !result.Valid {
		t.Fatalf("expected validation success, got reason=%q", result.Reason)
	}
	if result.ActionPlan == "" {
		t.Fatal("expected deterministic action plan")
	}
}

func TestObserveOnlyBuildsReadOnlyPlan(t *testing.T) {
	req := validScaleRequest()
	req.Action = ActionObserveOnly
	req.Instances = nil

	result := Execute(req)

	if !result.Valid {
		t.Fatalf("expected observe validation success, got reason=%q", result.Reason)
	}
	want := "observe service=aiops-service target_resource=gpu-vm-l4"
	if result.ActionPlan != want {
		t.Fatalf("action plan mismatch\nwant: %s\n got: %s", want, result.ActionPlan)
	}
}

func TestRejectsInstancesOnNonScaleActions(t *testing.T) {
	for _, action := range []string{ActionObserveOnly, ActionRestartService} {
		req := validScaleRequest()
		req.Action = action

		result := Execute(req)

		if result.Valid {
			t.Fatalf("expected invalid result for instances on %s", action)
		}
		if !strings.Contains(result.Reason, "only scale_out accepts instances") {
			t.Fatalf("unexpected reason for %s: %q", action, result.Reason)
		}
	}
}
