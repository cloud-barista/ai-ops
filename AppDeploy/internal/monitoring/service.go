package monitoring

import (
	"context"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

type repository interface {
	store.DeploymentRepository
	store.MetricRepository
}

type Service struct {
	repo repository
}

func NewService(repo repository) *Service {
	return &Service{repo: repo}
}

func (s *Service) Summary(ctx context.Context) (model.MonitoringSummaryResponse, error) {
	deployments, err := s.repo.ListDeployments(ctx)
	if err != nil {
		return model.MonitoringSummaryResponse{}, err
	}
	runtimeHealth, err := s.RuntimeHealth(ctx)
	if err != nil {
		return model.MonitoringSummaryResponse{}, err
	}
	alarms, err := s.Alarms(ctx)
	if err != nil {
		return model.MonitoringSummaryResponse{}, err
	}

	summary := model.MonitoringSummaryResponse{
		GeneratedAt:   time.Now().UTC(),
		Status:        "ok",
		Deployments:   summarizeDeployments(deployments),
		RuntimeHealth: runtimeHealth,
		Alarms:        alarms,
	}
	if summary.Deployments.Failed > 0 || len(summary.Alarms) > 0 {
		summary.Status = "degraded"
	}
	for _, item := range runtimeHealth {
		if item.Status != "available" {
			summary.Status = "degraded"
			break
		}
	}
	return summary, nil
}

func (s *Service) RuntimeHealth(ctx context.Context) ([]model.RuntimeHealthSnapshot, error) {
	inventory, err := s.repo.ListInventory(ctx)
	if err != nil {
		return nil, err
	}
	items := make([]model.RuntimeHealthSnapshot, 0, len(inventory))
	for _, item := range inventory {
		items = append(items, model.RuntimeHealthSnapshot{
			TargetProfileID:  item.TargetProfileID,
			Status:           runtimeStatus(item),
			RuntimeHealth:    item.RuntimeHealth,
			CPUAvailable:     item.CPUAvailable,
			MemoryAvailable:  item.MemoryAvailable,
			GPUAvailable:     item.GPUAvailable,
			StorageAvailable: item.StorageAvailable,
			LastCheckedAt:    item.LastCheckedAt,
		})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].LastCheckedAt.Equal(items[j].LastCheckedAt) {
			return items[i].TargetProfileID < items[j].TargetProfileID
		}
		return items[i].LastCheckedAt.After(items[j].LastCheckedAt)
	})
	return items, nil
}

func (s *Service) Alarms(ctx context.Context) ([]model.DeploymentAlarmSummary, error) {
	deployments, err := s.repo.ListDeployments(ctx)
	if err != nil {
		return nil, err
	}
	byCode := map[string]model.DeploymentAlarmSummary{}
	for _, deployment := range deployments {
		events, err := s.repo.ListEvents(ctx, deployment.DeploymentID, "")
		if err != nil {
			return nil, err
		}
		for _, event := range events {
			if event.Level != "ERROR" && strings.TrimSpace(event.ErrorCode) == "" {
				continue
			}
			code := strings.TrimSpace(event.ErrorCode)
			if code == "" {
				code = model.ErrRuntimeFailed
			}
			alarm := byCode[code]
			alarm.ErrorCode = code
			alarm.Severity = severityFor(code)
			alarm.Count++
			alarm.Retryable = alarm.Retryable || event.Retryable
			if alarm.LatestAt.IsZero() || event.Timestamp.After(alarm.LatestAt) {
				alarm.LatestAt = event.Timestamp
				alarm.LatestDeploymentID = event.DeploymentID
				alarm.LatestStage = event.Stage
				alarm.LatestMessage = event.Message
			}
			byCode[code] = alarm
		}
	}
	items := make([]model.DeploymentAlarmSummary, 0, len(byCode))
	for _, item := range byCode {
		items = append(items, item)
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].LatestAt.Equal(items[j].LatestAt) {
			return items[i].ErrorCode < items[j].ErrorCode
		}
		return items[i].LatestAt.After(items[j].LatestAt)
	})
	return items, nil
}

func (s *Service) RecordMetric(ctx context.Context, deploymentID string, req model.InferenceMetricCreateRequest) (model.InferenceMetricRecord, error) {
	deploymentID = strings.TrimSpace(deploymentID)
	if deploymentID == "" {
		return model.InferenceMetricRecord{}, apperrors.New(model.ErrDeploymentFailed, "deployment_id is required", http.StatusBadRequest, false)
	}
	if _, err := s.repo.GetDeployment(ctx, deploymentID); err != nil {
		return model.InferenceMetricRecord{}, err
	}
	if err := validateMetric(req); err != nil {
		return model.InferenceMetricRecord{}, err
	}
	timestamp := req.Timestamp
	if timestamp.IsZero() {
		timestamp = time.Now().UTC()
	} else {
		timestamp = timestamp.UTC()
	}
	metric := model.InferenceMetricRecord{
		MetricID:      "metric-" + uuid.NewString(),
		DeploymentID:  deploymentID,
		Timestamp:     timestamp,
		LatencyMS:     req.LatencyMS,
		ThroughputRPS: req.ThroughputRPS,
		QualityScore:  req.QualityScore,
		RequestCount:  req.RequestCount,
		ErrorCount:    req.ErrorCount,
		Metadata:      req.Metadata,
	}
	if err := s.repo.AddMetric(ctx, metric); err != nil {
		return model.InferenceMetricRecord{}, err
	}
	return metric, nil
}

func (s *Service) Metrics(ctx context.Context, deploymentID string) ([]model.InferenceMetricRecord, error) {
	var (
		items []model.InferenceMetricRecord
		err   error
	)
	deploymentID = strings.TrimSpace(deploymentID)
	if deploymentID == "" {
		items, err = s.repo.ListAllMetrics(ctx)
	} else {
		if _, err := s.repo.GetDeployment(ctx, deploymentID); err != nil {
			return nil, err
		}
		items, err = s.repo.ListMetrics(ctx, deploymentID)
	}
	if err != nil {
		return nil, err
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Timestamp.Equal(items[j].Timestamp) {
			return items[i].MetricID < items[j].MetricID
		}
		return items[i].Timestamp.After(items[j].Timestamp)
	})
	return items, nil
}

func summarizeDeployments(deployments []model.DeploymentResponse) model.DeploymentMonitorSummary {
	summary := model.DeploymentMonitorSummary{
		Total:    len(deployments),
		ByStatus: map[string]int{},
	}
	for _, deployment := range deployments {
		summary.ByStatus[deployment.Status]++
		switch {
		case isFailed(deployment.Status):
			summary.Failed++
		case deployment.Status == model.StatusStopped:
			summary.Stopped++
		case deployment.Status != "":
			summary.Active++
		}
	}
	return summary
}

func runtimeStatus(item model.ResourceInventory) string {
	if !item.CPUAvailable || !item.MemoryAvailable || !item.StorageAvailable {
		return "unavailable"
	}
	if strings.TrimSpace(item.RuntimeHealth) != "" && item.RuntimeHealth != "ok" {
		return "degraded"
	}
	return "available"
}

func validateMetric(req model.InferenceMetricCreateRequest) error {
	if req.LatencyMS < 0 || req.ThroughputRPS < 0 || req.QualityScore < 0 || req.RequestCount < 0 || req.ErrorCount < 0 {
		return apperrors.New(model.ErrDeploymentFailed, "metric values must be non-negative", http.StatusBadRequest, false)
	}
	if req.QualityScore > 1 {
		return apperrors.New(model.ErrDeploymentFailed, "quality_score must be between 0 and 1", http.StatusBadRequest, false)
	}
	return nil
}

func isFailed(status string) bool {
	switch status {
	case model.StatusValidationFailed, model.StatusSchedulingFailed, model.StatusDeploymentFailed, model.StatusRuntimeFailed, model.StatusExternalAPIFailed:
		return true
	default:
		return false
	}
}

func severityFor(code string) string {
	switch code {
	case model.ErrAIInfraAPITimeout, model.ErrAIInfraAPIFailed, model.ErrGatewayAuthFailed, model.ErrBespinAPIFailed, model.ErrRuntimeFailed:
		return "critical"
	default:
		return "error"
	}
}
