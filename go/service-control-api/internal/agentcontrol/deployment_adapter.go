package agentcontrol

import (
	"context"
	"fmt"
	"strings"
	"time"
)

const (
	DeploymentAdapterMock    = "mock"
	DeploymentAdapterHandoff = "handoff"

	DeploymentSubmissionSimulated = "SIMULATED"
	DeploymentSubmissionReady     = "READY"
	DeploymentSubmissionFailed    = "FAILED"
)

type DeploymentAdapter interface {
	Submit(context.Context, DeploymentCreateRequestEnvelope) (DeploymentSubmission, error)
}

type DeploymentSubmission struct {
	Adapter      string `json:"adapter"`
	Status       string `json:"status"`
	Simulated    bool   `json:"simulated"`
	RequestID    string `json:"request_id"`
	SubmittedAt  string `json:"submitted_at"`
	ErrorCode    string `json:"error_code,omitempty"`
	ErrorMessage string `json:"error_message,omitempty"`
}

type MockDeploymentAdapter struct {
	Now func() time.Time
}

func (adapter MockDeploymentAdapter) Submit(
	ctx context.Context,
	request DeploymentCreateRequestEnvelope,
) (DeploymentSubmission, error) {
	return createDeploymentSubmission(
		ctx,
		request,
		DeploymentAdapterMock,
		DeploymentSubmissionSimulated,
		true,
		adapter.Now,
	)
}

type HandoffDeploymentAdapter struct {
	Now func() time.Time
}

func (adapter HandoffDeploymentAdapter) Submit(
	ctx context.Context,
	request DeploymentCreateRequestEnvelope,
) (DeploymentSubmission, error) {
	return createDeploymentSubmission(
		ctx,
		request,
		DeploymentAdapterHandoff,
		DeploymentSubmissionReady,
		false,
		adapter.Now,
	)
}

func NewDeploymentAdapter(mode string) (DeploymentAdapter, error) {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "", DeploymentAdapterMock:
		return MockDeploymentAdapter{}, nil
	case DeploymentAdapterHandoff:
		return HandoffDeploymentAdapter{}, nil
	default:
		return nil, fmt.Errorf("unsupported deployment adapter mode")
	}
}

func createDeploymentSubmission(
	ctx context.Context,
	request DeploymentCreateRequestEnvelope,
	adapterName string,
	status string,
	simulated bool,
	now func() time.Time,
) (DeploymentSubmission, error) {
	if err := ctx.Err(); err != nil {
		return DeploymentSubmission{}, err
	}
	requestID := strings.TrimSpace(request.Data.DeploymentRequest.RequestID)
	if requestID == "" {
		return DeploymentSubmission{}, fmt.Errorf("deployment request_id is required")
	}
	if now == nil {
		now = func() time.Time { return time.Now().UTC() }
	}
	return DeploymentSubmission{
		Adapter:     adapterName,
		Status:      status,
		Simulated:   simulated,
		RequestID:   requestID,
		SubmittedAt: now().UTC().Format(time.RFC3339Nano),
	}, nil
}
