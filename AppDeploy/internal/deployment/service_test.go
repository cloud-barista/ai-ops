package deployment

import (
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestRuntimeProcessStarted(t *testing.T) {
	tests := []struct {
		name   string
		events []model.DeploymentEvent
		want   bool
	}{
		{name: "validation failed before deploy", events: []model.DeploymentEvent{{Stage: model.StatusValidating}, {Stage: model.StatusValidationFailed}}, want: false},
		{name: "stop retry after validation failure", events: []model.DeploymentEvent{{Stage: model.StatusValidationFailed}, {Stage: model.StatusStopping}, {Stage: model.StatusRuntimeFailed}}, want: false},
		{name: "deploy started", events: []model.DeploymentEvent{{Stage: model.StatusDeploying}}, want: true},
		{name: "running", events: []model.DeploymentEvent{{Stage: model.StatusRunning}}, want: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := runtimeProcessStarted(test.events); got != test.want {
				t.Fatalf("runtimeProcessStarted() = %t, want %t", got, test.want)
			}
		})
	}
}
