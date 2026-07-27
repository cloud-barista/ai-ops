// Package scheduler defines the boundary for VM-local workload scheduling.
//
// It deliberately contains contracts only. The ETRI agent transport and the
// VM command used to apply an order are integration details of later adapters.
package scheduler

import (
	"context"
	"time"

	"github.com/khu/ai-app-deployer/internal/model"
)

// VMStateAdapter receives state reported by a VM-side scheduler/agent and
// exposes the latest snapshot to the App Deployer. An ETRI adapter can map its
// DTOs to this contract without changing orchestration code.
type VMStateAdapter interface {
	Report(ctx context.Context, snapshot VMStateSnapshot) error
	Get(ctx context.Context, vmID string) (VMStateSnapshot, error)
}

// ExecutionOrderAdapter applies a complete workload order on a target VM.
// Implementations may call a VM-side agent, SSH command, or another runtime
// mechanism once that contract is agreed.
type ExecutionOrderAdapter interface {
	ApplyExecutionOrder(ctx context.Context, target model.TargetProfile, req ExecutionOrderChangeRequest) (ExecutionOrderChangeResult, error)
}

// VMStateSnapshot is the VM-local scheduler's observed resource and workload
// state. Revision is used to reject stale order changes.
type VMStateSnapshot struct {
	VMID       string                   `json:"vm_id"`
	Revision   uint64                   `json:"revision"`
	ObservedAt time.Time                `json:"observed_at"`
	Status     string                   `json:"status"`
	Capacity   model.ResourceCapacity   `json:"capacity"`
	Available  model.ResourceCapacity   `json:"available"`
	Allocated  model.ResourceAllocation `json:"allocated"`
	Workloads  []WorkloadState          `json:"workloads,omitempty"`
}

// WorkloadState describes one App Deployer workload currently known by the VM
// scheduler.
type WorkloadState struct {
	DeploymentID string                   `json:"deployment_id"`
	AppVersionID string                   `json:"app_version_id,omitempty"`
	RuntimeID    string                   `json:"runtime_id,omitempty"`
	Status       string                   `json:"status"`
	Allocation   model.ResourceAllocation `json:"allocation"`
	Order        int                      `json:"order"`
}

// ExecutionOrderChangeRequest carries a complete desired order. The adapter
// must validate that the order belongs to the VM and matches ExpectedRevision.
type ExecutionOrderChangeRequest struct {
	RequestID        string   `json:"request_id,omitempty"`
	VMID             string   `json:"vm_id"`
	ExpectedRevision uint64   `json:"expected_revision"`
	DeploymentOrder  []string `json:"deployment_order"`
	Reason           string   `json:"reason,omitempty"`
}

// ExecutionOrderChangeResult reports the order and revision acknowledged by
// the VM-side scheduler.
type ExecutionOrderChangeResult struct {
	RequestID    string    `json:"request_id,omitempty"`
	VMID         string    `json:"vm_id"`
	Revision     uint64    `json:"revision"`
	AppliedOrder []string  `json:"applied_order"`
	AppliedAt    time.Time `json:"applied_at"`
}
