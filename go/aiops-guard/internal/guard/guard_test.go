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
		Mode:               "mock",
		Namespace:          "aiops-demo",
		Deployment:         "aiops-service",
		Action:             "scale_out",
		Replicas:           ptr(3),
		AllowedNamespaces:  []string{"aiops-demo"},
		AllowedDeployments: []string{"aiops-service", "aiops-worker"},
		MinReplicas:        1,
		MaxReplicas:        5,
	}
}

func TestScaleOutRendersStableKubectlCommand(t *testing.T) {
	result := Execute(validScaleRequest(), nil)

	if !result.Valid {
		t.Fatalf("expected valid result, got stderr=%q", result.Stderr)
	}

	want := "kubectl scale deployment aiops-service --replicas=3 -n aiops-demo"
	if result.Command != want {
		t.Fatalf("command mismatch\nwant: %s\n got: %s", want, result.Command)
	}
}

func TestRejectsNamespaceOutsideAllowlist(t *testing.T) {
	req := validScaleRequest()
	req.Namespace = "kube-system"

	result := Execute(req, nil)

	if result.Valid {
		t.Fatalf("expected invalid result for namespace outside policy")
	}
	if !strings.Contains(result.Stderr, "namespace is not allowed") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestRejectsReplicaAboveMax(t *testing.T) {
	req := validScaleRequest()
	req.Replicas = ptr(9)

	result := Execute(req, nil)

	if result.Valid {
		t.Fatalf("expected invalid result for replica above max")
	}
	if !strings.Contains(result.Stderr, "replicas must be between") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestRejectsUnsupportedAction(t *testing.T) {
	req := validScaleRequest()
	req.Action = "delete_namespace"

	result := Execute(req, nil)

	if result.Valid {
		t.Fatalf("expected invalid result for unsupported action")
	}
	if !strings.Contains(result.Stderr, "unsupported action") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestDryRunAddsServerDryRunToMutatingCommand(t *testing.T) {
	req := validScaleRequest()
	req.Mode = "dry-run"

	result := Execute(req, func(name string, args ...string) (string, string, int) {
		got := name + " " + strings.Join(args, " ")
		want := "kubectl scale deployment aiops-service --replicas=3 -n aiops-demo --dry-run=server"
		if got != want {
			t.Fatalf("command mismatch\nwant: %s\n got: %s", want, got)
		}
		return "deployment.apps/aiops-service scaled (server dry run)", "", 0
	})

	if !result.Valid {
		t.Fatalf("expected dry-run success, got stderr=%q", result.Stderr)
	}
}

func TestMockDoesNotCallRunner(t *testing.T) {
	req := validScaleRequest()
	called := false

	result := Execute(req, func(name string, args ...string) (string, string, int) {
		called = true
		return "", "", 0
	})

	if called {
		t.Fatalf("mock mode must not call kubectl runner")
	}
	if !result.Valid {
		t.Fatalf("expected mock validation success, got stderr=%q", result.Stderr)
	}
}

func TestObserveOnlyRendersReadOnlyKubectlCommand(t *testing.T) {
	req := validScaleRequest()
	req.Action = "observe_only"
	req.Replicas = nil
	req.Mode = "dry-run"

	result := Execute(req, func(name string, args ...string) (string, string, int) {
		got := name + " " + strings.Join(args, " ")
		want := "kubectl get deployment aiops-service -n aiops-demo -o json"
		if got != want {
			t.Fatalf("command mismatch\nwant: %s\n got: %s", want, got)
		}
		return "aiops-service 3/3", "", 0
	})

	if !result.Valid {
		t.Fatalf("expected observe success, got stderr=%q", result.Stderr)
	}
}

func TestRejectsReplicasOnNonScaleActions(t *testing.T) {
	for _, action := range []string{"observe_only", "rollout_restart"} {
		req := validScaleRequest()
		req.Action = action
		req.Replicas = ptr(3)

		result := Execute(req, nil)

		if result.Valid {
			t.Fatalf("expected invalid result for replicas on %s", action)
		}
		if !strings.Contains(result.Stderr, "only scale_out accepts replicas") {
			t.Fatalf("unexpected stderr for %s: %q", action, result.Stderr)
		}
	}
}

func TestRolloutRestartDryRunCommand(t *testing.T) {
	req := validScaleRequest()
	req.Mode = "dry-run"
	req.Action = "rollout_restart"
	req.Replicas = nil

	result := Execute(req, func(name string, args ...string) (string, string, int) {
		got := name + " " + strings.Join(args, " ")
		want := "kubectl rollout restart deployment aiops-service -n aiops-demo --dry-run=server"
		if got != want {
			t.Fatalf("command mismatch\nwant: %s\n got: %s", want, got)
		}
		return "deployment.apps/aiops-service restarted (server dry run)", "", 0
	})

	if !result.Valid {
		t.Fatalf("expected rollout restart dry-run success, got stderr=%q", result.Stderr)
	}
}
