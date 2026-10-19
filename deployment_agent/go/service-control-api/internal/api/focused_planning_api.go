package api

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strings"
	"time"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
)

const (
	focusedContractVersion = "1.0"
	focusedMaxRequestBytes = 4 << 20
)

// DeploymentPlanningRequest is the only focused pre-deployment input. It
// joins llmop's ApplicationProfile with resource-ops' recommendation result.
type DeploymentPlanningRequest struct {
	SchemaVersion          string                          `json:"schema_version,omitempty"`
	CorrelationID          string                          `json:"correlation_id"`
	TraceID                string                          `json:"trace_id"`
	DecisionAgent          string                          `json:"decision_agent,omitempty"`
	DeploymentContext      *agentcontrol.DeploymentContext `json:"deployment_context,omitempty"`
	ApplicationProfile     agentcontrol.ApplicationProfile `json:"application_profile"`
	ResourceRecommendation ResourceOpsRecommendation       `json:"resource_recommendation"`
}

// ResourceOpsRecommendation mirrors the focused resource-ops response at the
// deployment boundary. Query remains opaque evidence because deployment_agent
// must not reinterpret or rerun infrastructure lookup logic.
type ResourceOpsRecommendation struct {
	SchemaVersion     string                        `json:"schema_version"`
	RequestID         string                        `json:"request_id"`
	ProfileID         string                        `json:"profile_id,omitempty"`
	Source            string                        `json:"source"`
	ObservedAt        string                        `json:"observed_at"`
	Query             json.RawMessage               `json:"query"`
	Recommendations   []ResourceOpsRankedResource   `json:"recommendations"`
	ExcludedResources []ResourceOpsExcludedResource `json:"excluded_resources"`
	Summary           ResourceOpsSummary            `json:"summary"`
	Warnings          []string                      `json:"warnings"`
}

type ResourceOpsRankedResource struct {
	Rank           int                       `json:"rank"`
	Score          float64                   `json:"score"`
	Resource       ResourceOpsProfile        `json:"resource"`
	ScoreBreakdown ResourceOpsScoreBreakdown `json:"score_breakdown"`
	Reasons        []ResourceOpsReason       `json:"reasons"`
}

type ResourceOpsProfile struct {
	ResourceID   string                   `json:"resource_id"`
	Hostname     string                   `json:"hostname,omitempty"`
	Address      string                   `json:"address,omitempty"`
	Provider     string                   `json:"provider,omitempty"`
	Region       string                   `json:"region,omitempty"`
	ResourceType string                   `json:"resource_type,omitempty"`
	Architecture string                   `json:"architecture,omitempty"`
	OperatingSys string                   `json:"operating_system,omitempty"`
	Healthy      bool                     `json:"healthy"`
	ObservedAt   string                   `json:"observed_at"`
	CPU          ResourceOpsCPU           `json:"cpu"`
	Memory       ResourceOpsMemory        `json:"memory"`
	Storage      ResourceOpsStorage       `json:"storage"`
	Accelerators []ResourceOpsAccelerator `json:"accelerators"`
	Labels       map[string]string        `json:"labels,omitempty"`
	Warnings     []string                 `json:"warnings"`
}

type ResourceOpsCPU struct {
	LogicalCores       int      `json:"logical_cores"`
	AvailableCores     *float64 `json:"available_cores,omitempty"`
	UtilizationPercent *float64 `json:"utilization_percent,omitempty"`
}

type ResourceOpsMemory struct {
	TotalMiB     float64  `json:"total_mib"`
	AvailableMiB *float64 `json:"available_mib,omitempty"`
}

type ResourceOpsStorage struct {
	TotalGiB     float64  `json:"total_gib"`
	AvailableGiB *float64 `json:"available_gib,omitempty"`
}

type ResourceOpsAccelerator struct {
	DeviceID           string   `json:"device_id"`
	Index              string   `json:"index,omitempty"`
	Type               string   `json:"type"`
	Model              string   `json:"model,omitempty"`
	MemoryTotalMiB     float64  `json:"memory_total_mib"`
	MemoryAvailableMiB *float64 `json:"memory_available_mib,omitempty"`
	UtilizationPercent *float64 `json:"utilization_percent,omitempty"`
	Healthy            bool     `json:"healthy"`
}

type ResourceOpsScoreBreakdown struct {
	CPU         *float64 `json:"cpu,omitempty"`
	Memory      *float64 `json:"memory,omitempty"`
	Storage     *float64 `json:"storage,omitempty"`
	Accelerator *float64 `json:"accelerator,omitempty"`
	Health      float64  `json:"health"`
	Total       float64  `json:"total"`
}

type ResourceOpsReason struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type ResourceOpsExcludedResource struct {
	ResourceID string                        `json:"resource_id"`
	Failures   []ResourceOpsConditionFailure `json:"failures"`
}

type ResourceOpsConditionFailure struct {
	Code     string `json:"code"`
	Field    string `json:"field"`
	Required any    `json:"required,omitempty"`
	Actual   any    `json:"actual,omitempty"`
	Reason   string `json:"reason"`
}

type ResourceOpsSummary struct {
	Discovered int `json:"discovered"`
	Feasible   int `json:"feasible"`
	Excluded   int `json:"excluded"`
	Returned   int `json:"returned"`
}

func (handler restHandler) RestPostDeploymentPlanFromResults(context echo.Context) error {
	var request DeploymentPlanningRequest
	if err := decodeFocusedRequest(context, &request); err != nil {
		return focusedError(context, http.StatusBadRequest, "INVALID_REQUEST", "Request body must be one valid focused deployment-planning object.", err)
	}
	if err := validatePlanningRequest(request); err != nil {
		return focusedError(context, http.StatusUnprocessableEntity, "INPUT_CONTRACT_MISMATCH", "ApplicationProfile and ResourceRecommendation could not be correlated.", err)
	}

	recommendation, err := adaptResourceOpsRecommendation(request.ApplicationProfile, request.ResourceRecommendation)
	if err != nil {
		return focusedError(context, http.StatusUnprocessableEntity, "INVALID_RESOURCE_RECOMMENDATION", "ResourceRecommendation could not be adapted for deployment planning.", err)
	}
	encodedRequest, err := json.Marshal(request)
	if err != nil {
		return focusedError(context, http.StatusBadRequest, "INVALID_REQUEST", "Request body could not be normalized.", err)
	}
	inputDigest := sha256.Sum256(encodedRequest)
	now := time.Now().UTC().Format(time.RFC3339Nano)
	applicationMessageID := "msg-application-" + request.CorrelationID
	applicationContext := agentcontrol.ApplicationContextEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       applicationMessageID,
			MessageType:     agentcontrol.MessageApplicationContextCreated,
			OccurredAt:      now,
			CorrelationID:   request.CorrelationID,
			TraceID:         request.TraceID,
			Source:          agentcontrol.Endpoint{System: "llmop", Component: "application-profile-api"},
			Target:          agentcontrol.Endpoint{System: "deployment-agent", Component: "agent-control"},
		},
		Data: agentcontrol.ApplicationContextData{
			ApplicationProfile:  request.ApplicationProfile,
			ModelRecommendation: inferDeploymentConfiguration(request.ApplicationProfile),
		},
	}
	resourceMessage := agentcontrol.ResourceRecommendationEnvelope{
		Envelope: agentcontrol.Envelope{
			ContractVersion: agentcontrol.ContractVersionV1,
			MessageID:       "msg-resource-" + request.CorrelationID,
			MessageType:     agentcontrol.MessageResourceRecommendationCreated,
			OccurredAt:      now,
			CorrelationID:   request.CorrelationID,
			TraceID:         request.TraceID,
			CausationID:     applicationMessageID,
			Source:          agentcontrol.Endpoint{System: "resource-ops", Component: "resource-recommendation-api"},
			Target:          agentcontrol.Endpoint{System: "deployment-agent", Component: "agent-control"},
		},
		Data: agentcontrol.ResourceRecommendationData{ResourceRecommendation: recommendation},
	}
	flow, replayed, err := handler.service.agentControl.ReceiveFocusedInputs(
		context.Request().Context(), applicationContext, resourceMessage, request.DecisionAgent,
		fmt.Sprintf("%x", inputDigest[:]), request.DeploymentContext,
	)
	if err != nil {
		return focusedError(context, http.StatusUnprocessableEntity, "DEPLOYMENT_PLANNING_REJECTED", "Deployment planning inputs were rejected.", err)
	}
	if replayed {
		context.Response().Header().Set("Idempotent-Replayed", "true")
		return context.JSON(http.StatusOK, flow)
	}
	flow = handler.maybeEvaluateShadowPolicy(context.Request().Context(), flow, "planning")
	status := http.StatusOK
	if flow.State == agentcontrol.StateDecisionApproved || flow.State == agentcontrol.StateUpdateApproved {
		status = http.StatusCreated
	}
	return context.JSON(status, flow)
}

func decodeFocusedRequest(context echo.Context, destination any) error {
	request := context.Request()
	request.Body = http.MaxBytesReader(context.Response(), request.Body, focusedMaxRequestBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return errors.New("request body must contain exactly one JSON value")
		}
		return err
	}
	return nil
}

func validatePlanningRequest(request DeploymentPlanningRequest) error {
	version := strings.TrimSpace(request.SchemaVersion)
	if version == "" {
		version = focusedContractVersion
	}
	switch {
	case version != focusedContractVersion:
		return fmt.Errorf("schema_version must be %q", focusedContractVersion)
	case strings.TrimSpace(request.CorrelationID) == "":
		return errors.New("correlation_id is required")
	case strings.TrimSpace(request.TraceID) == "":
		return errors.New("trace_id is required")
	case request.ResourceRecommendation.SchemaVersion != focusedContractVersion:
		return errors.New("resource_recommendation.schema_version is unsupported")
	case request.ResourceRecommendation.RequestID != request.CorrelationID:
		return errors.New("resource_recommendation.request_id must match correlation_id")
	case strings.TrimSpace(request.ApplicationProfile.ProfileID) == "":
		return errors.New("application_profile.profile_id is required")
	case request.ResourceRecommendation.ProfileID != request.ApplicationProfile.ProfileID:
		return errors.New("resource_recommendation.profile_id must match application_profile.profile_id")
	case strings.TrimSpace(request.ResourceRecommendation.Source) == "":
		return errors.New("resource_recommendation.source is required")
	case !request.ApplicationProfile.Requirements.Accelerator.Required &&
		(request.ApplicationProfile.Requirements.Accelerator.CountMin != 0 ||
			strings.TrimSpace(request.ApplicationProfile.Requirements.Accelerator.Type) != "" ||
			request.ApplicationProfile.Requirements.Accelerator.MemoryMiBMinPerDevice != 0):
		return errors.New("application_profile.requirements.accelerator fields must be empty or zero when required is false")
	case request.ResourceRecommendation.Summary.Returned != len(request.ResourceRecommendation.Recommendations):
		return errors.New("resource_recommendation.summary.returned does not match recommendations")
	}
	if _, err := time.Parse(time.RFC3339, request.ResourceRecommendation.ObservedAt); err != nil {
		return errors.New("resource_recommendation.observed_at must be RFC3339")
	}
	return nil
}

func adaptResourceOpsRecommendation(
	profile agentcontrol.ApplicationProfile,
	source ResourceOpsRecommendation,
) (agentcontrol.ResourceRecommendation, error) {
	result := agentcontrol.ResourceRecommendation{
		RecommendationID: "resource-rec-" + source.RequestID,
		ProfileID:        profile.ProfileID,
		SnapshotID:       source.Source + "@" + source.ObservedAt,
		Status:           "RETRY_REQUIRED",
		Candidates:       []agentcontrol.ResourceCandidate{},
	}
	replicas := profile.Requirements.Deployment.ReplicasMin
	if replicas < 1 {
		replicas = 1
	}
	seenResourceIDs := make(map[string]struct{}, len(source.Recommendations))
	for index, ranked := range source.Recommendations {
		resourceID := strings.TrimSpace(ranked.Resource.ResourceID)
		if ranked.Rank != index+1 || math.IsNaN(ranked.Score) || math.IsInf(ranked.Score, 0) ||
			ranked.Score < 0 || ranked.Score > 100 || resourceID == "" {
			return agentcontrol.ResourceRecommendation{}, fmt.Errorf("recommendations[%d] has invalid rank, score, or resource_id", index)
		}
		if _, exists := seenResourceIDs[resourceID]; exists {
			return agentcontrol.ResourceRecommendation{}, fmt.Errorf("recommendations[%d] repeats resource_id", index)
		}
		seenResourceIDs[resourceID] = struct{}{}
		if !ranked.Resource.Healthy {
			return agentcontrol.ResourceRecommendation{}, fmt.Errorf("recommendations[%d] is not healthy", index)
		}
	}
	if len(source.Recommendations) < replicas {
		return result, nil
	}

	validated := make([]ResourceOpsRankedResource, 0, len(source.Recommendations))
	for _, ranked := range source.Recommendations {
		if len(resourceCapacityRejectionReasons(ranked.Resource, profile.Requirements)) == 0 {
			validated = append(validated, ranked)
		}
	}
	if replicas == 1 {
		for _, ranked := range source.Recommendations {
			candidate := deploymentCandidate(profile, source.Source, []ResourceOpsRankedResource{ranked})
			result.Candidates = append(result.Candidates, candidate)
			if result.SelectedCandidateID == "" && candidate.Feasible {
				result.SelectedCandidateID = candidate.CandidateID
			}
		}
	} else {
		// Keep the originally ranked group as reviewable rejection evidence if
		// any of its nodes fail the capacity check. A usable group draws from
		// the remaining upstream-ranked nodes without re-scoring or re-sorting.
		original := deploymentCandidate(profile, source.Source, source.Recommendations[:replicas])
		if !original.Feasible {
			result.Candidates = append(result.Candidates, original)
		}
		if len(validated) >= replicas {
			selected := deploymentCandidate(profile, source.Source, validated[:replicas])
			result.Candidates = append(result.Candidates, selected)
			result.SelectedCandidateID = selected.CandidateID
		}
	}
	for index := range result.Candidates {
		result.Candidates[index].Rank = index + 1
	}
	if result.SelectedCandidateID != "" {
		result.Status = "FOUND"
	}
	return result, nil
}

func deploymentCandidate(
	profile agentcontrol.ApplicationProfile,
	source string,
	ranked []ResourceOpsRankedResource,
) agentcontrol.ResourceCandidate {
	resourceIDs := make([]string, 0, len(ranked))
	hints := []string{"source:" + source}
	totalScore := 0.0
	resourceFit := 0.0
	availability := 0.0
	rejectionReasons := make([]string, 0)
	for _, candidate := range ranked {
		resourceIDs = append(resourceIDs, candidate.Resource.ResourceID)
		rejectionReasons = append(rejectionReasons, resourceCapacityRejectionReasons(candidate.Resource, profile.Requirements)...)
		hints = append(hints, "resource_id:"+candidate.Resource.ResourceID)
		if candidate.Resource.Address != "" {
			hints = append(hints, "address:"+candidate.Resource.Address)
		}
		totalScore += normalizeResourceScore(candidate.Score)
		resourceFit += averageBreakdown(candidate.ScoreBreakdown)
		availability += normalizeResourceScore(candidate.ScoreBreakdown.Health)
	}
	count := float64(len(ranked))
	candidateID := resourceIDs[0]
	if len(resourceIDs) > 1 {
		digest := sha256.Sum256([]byte(strings.Join(resourceIDs, "\x00")))
		candidateID = fmt.Sprintf("placement-%x", digest[:8])
	}
	requirements := profile.Requirements
	return agentcontrol.ResourceCandidate{
		CandidateID: candidateID,
		Feasible:    len(rejectionReasons) == 0,
		DesiredInfrastructure: agentcontrol.DesiredInfrastructure{
			NodeCount:         len(ranked),
			CPUCoresPerNode:   requirements.Compute.CPUCoresMin,
			MemoryMiBPerNode:  requirements.Compute.MemoryMiBMin,
			StorageGiBPerNode: requirements.Compute.StorageGiBMin,
			Accelerator: agentcontrol.AcceleratorAllocation{
				Type:                  requirements.Accelerator.Type,
				Count:                 requirements.Accelerator.CountMin,
				MemoryMiBMinPerDevice: requirements.Accelerator.MemoryMiBMinPerDevice,
			},
			Isolation: requirements.Deployment.Isolation,
			Placement: agentcontrol.PlacementConstraints{
				RequiredLabels: commonResourceLabels(ranked),
				AntiAffinity:   len(ranked) > 1,
			},
		},
		ResourceHints:    hints,
		RejectionReasons: rejectionReasons,
		Scores: agentcontrol.ResourceScores{
			ResourceFit:    roundUnitScore(resourceFit / count),
			SLOHeadroom:    0,
			CostEfficiency: 0,
			Availability:   roundUnitScore(availability / count),
			Total:          roundUnitScore(totalScore / count),
		},
	}
}

// Resource Ops owns filtering and ranking. Rechecking its observed capacities
// here prevents a missing or stale resource profile from becoming a feasible
// deployment shape merely because DesiredInfrastructure copies profile minima.
func resourceCapacityRejectionReasons(resource ResourceOpsProfile, requirements agentcontrol.ApplicationRequirements) []string {
	reasons := make([]string, 0)
	add := func(code string) {
		reasons = append(reasons, "resource_id:"+resource.ResourceID+" "+code)
	}
	compute := requirements.Compute
	if resource.CPU.AvailableCores != nil && *resource.CPU.AvailableCores > float64(resource.CPU.LogicalCores) {
		add("CPU_AVAILABILITY_EXCEEDS_CAPACITY")
	}
	if resource.CPU.LogicalCores < compute.CPUCoresMin {
		add("CPU_CAPACITY_INSUFFICIENT")
	} else if resource.CPU.AvailableCores == nil || !capacityAtLeast(*resource.CPU.AvailableCores, float64(compute.CPUCoresMin)) {
		add("CPU_AVAILABLE_INSUFFICIENT_OR_UNKNOWN")
	}
	if resource.Memory.AvailableMiB != nil && *resource.Memory.AvailableMiB > resource.Memory.TotalMiB {
		add("MEMORY_AVAILABILITY_EXCEEDS_CAPACITY")
	}
	if !capacityAtLeast(resource.Memory.TotalMiB, float64(compute.MemoryMiBMin)) {
		add("MEMORY_CAPACITY_INSUFFICIENT")
	} else if resource.Memory.AvailableMiB == nil || !capacityAtLeast(*resource.Memory.AvailableMiB, float64(compute.MemoryMiBMin)) {
		add("MEMORY_AVAILABLE_INSUFFICIENT_OR_UNKNOWN")
	}
	if resource.Storage.AvailableGiB != nil && *resource.Storage.AvailableGiB > resource.Storage.TotalGiB {
		add("STORAGE_AVAILABILITY_EXCEEDS_CAPACITY")
	}
	if compute.StorageGiBMin > 0 {
		if !capacityAtLeast(resource.Storage.TotalGiB, float64(compute.StorageGiBMin)) {
			add("STORAGE_CAPACITY_INSUFFICIENT")
		} else if resource.Storage.AvailableGiB == nil || !capacityAtLeast(*resource.Storage.AvailableGiB, float64(compute.StorageGiBMin)) {
			add("STORAGE_AVAILABLE_INSUFFICIENT_OR_UNKNOWN")
		}
	}
	if requirements.Accelerator.Required {
		accelerator := requirements.Accelerator
		capable, available := 0, 0
		seenDevices := make(map[string]struct{}, len(resource.Accelerators))
		for _, device := range resource.Accelerators {
			if !strings.EqualFold(device.Type, accelerator.Type) {
				continue
			}
			if device.MemoryAvailableMiB != nil && *device.MemoryAvailableMiB > device.MemoryTotalMiB {
				add("ACCELERATOR_AVAILABILITY_EXCEEDS_CAPACITY")
			}
			deviceID := strings.TrimSpace(device.DeviceID)
			if deviceID == "" {
				continue
			}
			if _, exists := seenDevices[deviceID]; exists {
				continue
			}
			seenDevices[deviceID] = struct{}{}
			if !capacityAtLeast(device.MemoryTotalMiB, float64(accelerator.MemoryMiBMinPerDevice)) {
				continue
			}
			capable++
			if device.Healthy && device.MemoryAvailableMiB != nil &&
				*device.MemoryAvailableMiB <= device.MemoryTotalMiB &&
				capacityAtLeast(*device.MemoryAvailableMiB, float64(accelerator.MemoryMiBMinPerDevice)) {
				available++
			}
		}
		if capable < accelerator.CountMin {
			add("ACCELERATOR_CAPACITY_INSUFFICIENT")
		} else if available < accelerator.CountMin {
			add("ACCELERATOR_AVAILABLE_INSUFFICIENT_OR_UNKNOWN")
		}
	}
	return reasons
}

func capacityAtLeast(actual, required float64) bool {
	return !math.IsNaN(actual) && !math.IsInf(actual, 0) && actual > 0 && actual >= required
}

// commonResourceLabels preserves only label predicates shared by every node in
// a multi-node candidate. It is a platform-neutral placement condition, not a
// VM identity, and an adapter may translate it for its own scheduler.
func commonResourceLabels(ranked []ResourceOpsRankedResource) map[string]string {
	if len(ranked) == 0 || len(ranked[0].Resource.Labels) == 0 {
		return nil
	}
	labels := make(map[string]string, len(ranked[0].Resource.Labels))
	for key, value := range ranked[0].Resource.Labels {
		labels[key] = value
	}
	for _, candidate := range ranked[1:] {
		for key, value := range labels {
			otherValue, present := candidate.Resource.Labels[key]
			if !present || otherValue != value {
				delete(labels, key)
			}
		}
	}
	if len(labels) == 0 {
		return nil
	}
	return labels
}

func averageBreakdown(score ResourceOpsScoreBreakdown) float64 {
	values := make([]float64, 0, 4)
	for _, value := range []*float64{score.CPU, score.Memory, score.Storage, score.Accelerator} {
		if value != nil {
			values = append(values, normalizeResourceScore(*value))
		}
	}
	if len(values) == 0 {
		return normalizeResourceScore(score.Total)
	}
	total := 0.0
	for _, value := range values {
		total += value
	}
	return total / float64(len(values))
}

func normalizeResourceScore(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 100 {
		return 1
	}
	return value / 100
}

func roundUnitScore(value float64) float64 {
	return math.Round(value*10000) / 10000
}

func inferDeploymentConfiguration(profile agentcontrol.ApplicationProfile) agentcontrol.ModelRecommendation {
	precision := "FP32"
	parallelism := 1
	if profile.Requirements.Accelerator.Required {
		precision = "FP16"
		parallelism = profile.Requirements.Accelerator.CountMin
		if parallelism < 1 {
			parallelism = 1
		}
	}
	concurrency := int(math.Ceil(profile.Workload.ExpectedRPS))
	if concurrency < 1 {
		concurrency = 1
	}
	return agentcontrol.ModelRecommendation{
		SelectedModel: agentcontrol.SelectedModel{
			ModelID:      profile.AppID,
			ModelVersion: profile.AppVersion,
			Source:       "APPLICATION_PROFILE",
		},
		InferenceConfiguration: agentcontrol.InferenceConfiguration{
			RuntimeEngine:      "VLLM",
			Precision:          precision,
			MaxBatchSize:       1,
			MaxConcurrency:     concurrency,
			TensorParallelSize: parallelism,
			Replicas:           profile.Requirements.Deployment.ReplicasMin,
		},
	}
}

func focusedError(context echo.Context, status int, code string, message string, err error) error {
	logFocusedError(context, status, code, err)
	return context.JSON(status, map[string]any{
		"schema_version": focusedContractVersion,
		"error": map[string]string{
			"code":    code,
			"message": message,
		},
	})
}
