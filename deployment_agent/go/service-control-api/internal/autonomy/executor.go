package autonomy

import (
	"context"
	"strings"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

type AppDeployControl interface {
	GetDeployment(context.Context, string) (appdeploy.DeploymentResponse, error)
	ListDeploymentMetrics(context.Context, string) (appdeploy.DeploymentMetricsResponse, error)
	GetMonitoringSummary(context.Context) (appdeploy.MonitoringSummaryResponse, error)
	CreateDeployment(context.Context, appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error)
	StopDeployment(context.Context, string) (appdeploy.DeploymentResponse, error)
}

type ExecutionStatus string

const (
	ExecutionSucceeded      ExecutionStatus = "succeeded"
	ExecutionFailed         ExecutionStatus = "execution_failed"
	ExecutionPartialFailure ExecutionStatus = "partial_failure"
	ExecutionBlocked        ExecutionStatus = "blocked"
	ExecutionNotApplicable  ExecutionStatus = "not_applicable"
)

type ExecutionInput struct {
	Action                 Action
	Deployment             appdeploy.DeploymentResponse
	StandbyTargetProfileID string
	RollbackAppVersionID   string
}

type ExecutionResult struct {
	Status                 ExecutionStatus `json:"status"`
	Action                 Action          `json:"action"`
	DeploymentID           string          `json:"deployment_id"`
	NewDeploymentID        string          `json:"new_deployment_id,omitempty"`
	StopCompleted          bool            `json:"stop_completed,omitempty"`
	TrafficHandoffRequired bool            `json:"traffic_handoff_required,omitempty"`
	Reason                 string          `json:"reason"`
}

type Executor struct {
	control AppDeployControl
}

func NewExecutor(control AppDeployControl) Executor {
	return Executor{control: control}
}

func (executor Executor) Execute(ctx context.Context, input ExecutionInput) ExecutionResult {
	result := ExecutionResult{Action: input.Action, DeploymentID: input.Deployment.DeploymentID}
	if executor.control == nil {
		result.Status = ExecutionBlocked
		result.Reason = "AppDeploy control is not configured"
		return result
	}
	switch input.Action {
	case ActionObserve:
		if _, err := executor.control.GetDeployment(ctx, input.Deployment.DeploymentID); err != nil {
			return failedExecution(result, "AppDeploy deployment observation failed")
		}
		if _, err := executor.control.ListDeploymentMetrics(ctx, input.Deployment.DeploymentID); err != nil {
			return failedExecution(result, "AppDeploy metric observation failed")
		}
		result.Status = ExecutionSucceeded
		result.Reason = "deployment status and metrics were refreshed without changing state"
		return result
	case ActionStop:
		if terminalDeploymentStatus(input.Deployment.Status) {
			if _, err := executor.control.GetDeployment(ctx, input.Deployment.DeploymentID); err != nil {
				return failedExecution(result, "AppDeploy terminal deployment observation failed")
			}
			result.Status = ExecutionNotApplicable
			result.Reason = "the deployment is already terminal; status was observed without issuing stop"
			return result
		}
		if _, err := executor.control.StopDeployment(ctx, input.Deployment.DeploymentID); err != nil {
			return failedExecution(result, "AppDeploy stop request failed")
		}
		result.Status = ExecutionSucceeded
		result.StopCompleted = true
		result.Reason = "deployment stop request was accepted"
		return result
	case ActionRestart:
		return executor.replace(ctx, result, input.Deployment.Manifest, input.Deployment.Status)
	case ActionRollback:
		if strings.TrimSpace(input.RollbackAppVersionID) == "" {
			result.Status = ExecutionBlocked
			result.Reason = "rollback App Version is not configured"
			return result
		}
		manifest := input.Deployment.Manifest
		manifest.Spec.AppVersionID = input.RollbackAppVersionID
		return executor.replace(ctx, result, manifest, input.Deployment.Status)
	case ActionScaleOut:
		if strings.TrimSpace(input.StandbyTargetProfileID) == "" {
			result.Status = ExecutionBlocked
			result.Reason = "standby Target Profile is not configured"
			return result
		}
		manifest := input.Deployment.Manifest
		manifest.Spec.TargetProfileID = input.StandbyTargetProfileID
		created, err := executor.control.CreateDeployment(ctx, manifest)
		if err != nil {
			return failedExecution(result, "AppDeploy scale-out deployment request failed")
		}
		result.Status = ExecutionSucceeded
		result.NewDeploymentID = created.DeploymentID
		result.TrafficHandoffRequired = true
		result.Reason = "an additional VM deployment was requested; traffic routing still requires an external handoff"
		return result
	default:
		result.Status = ExecutionBlocked
		result.Reason = "the proposed Action is outside the executor allowlist"
		return result
	}
}

func (executor Executor) replace(ctx context.Context, result ExecutionResult, manifest appdeploy.DeploymentManifest, currentStatus string) ExecutionResult {
	if strings.TrimSpace(manifest.Spec.AppVersionID) == "" {
		result.Status = ExecutionBlocked
		result.Reason = "the current deployment does not contain a reusable manifest"
		return result
	}
	if !terminalDeploymentStatus(currentStatus) {
		if _, err := executor.control.StopDeployment(ctx, result.DeploymentID); err != nil {
			return failedExecution(result, "AppDeploy stop request failed before replacement")
		}
		result.StopCompleted = true
	}
	created, err := executor.control.CreateDeployment(ctx, manifest)
	if err != nil {
		if result.StopCompleted {
			result.Status = ExecutionPartialFailure
			result.Reason = "the original deployment stopped but replacement creation failed; automatic execution is locked"
		} else {
			result.Status = ExecutionFailed
			result.Reason = "AppDeploy replacement creation failed"
		}
		return result
	}
	result.Status = ExecutionSucceeded
	result.NewDeploymentID = created.DeploymentID
	result.Reason = "the original deployment stopped and a replacement deployment was requested"
	return result
}

func terminalDeploymentStatus(status string) bool {
	switch strings.ToUpper(strings.TrimSpace(status)) {
	case "STOPPED", "FAILED", "SUCCEEDED", "COMPLETED", "CANCELLED", "CANCELED", "TERMINATED":
		return true
	default:
		return false
	}
}

func failedExecution(result ExecutionResult, reason string) ExecutionResult {
	result.Status = ExecutionFailed
	result.Reason = reason
	return result
}
