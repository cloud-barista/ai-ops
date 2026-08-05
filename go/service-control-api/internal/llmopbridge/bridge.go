// Package llmopbridge projects the existing Common JSON planning flow into
// the bounded llmop request contract without changing agentcontrol.
package llmopbridge

import (
	"fmt"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
)

const (
	recommendationStatusFound = "FOUND"
	maxCPUCount               = 256
	maxMemoryMiB              = 2 * 1024 * 1024
	maxGPUCount               = 16
	maxStorageGiB             = 64 * 1024
)

// Input combines caller-bound llmop identity with three existing Common JSON
// messages. Request.CandidateID is an already selected completion binding; the
// bridge never reads ModelRecommendation and never performs model selection.
type Input struct {
	Request                llmop.Request
	AnalysisRequest        agentcontrol.ApplicationAnalysisRequestEnvelope
	ApplicationContext     agentcontrol.ApplicationContextEnvelope
	ResourceRecommendation agentcontrol.ResourceRecommendationEnvelope
}

// Evidence records compatibility joins. These identifiers are not AppDeploy
// target IDs and are never copied into the deployment manifest.
type Evidence struct {
	AnalysisMessageID               string `json:"analysis_message_id"`
	ApplicationContextMessageID     string `json:"application_context_message_id"`
	ResourceRecommendationMessageID string `json:"resource_recommendation_message_id"`
	SourceProfileID                 string `json:"source_profile_id"`
	SourceRecommendationID          string `json:"source_recommendation_id"`
	SourceSnapshotID                string `json:"source_snapshot_id"`
	SelectedResourceCandidateID     string `json:"selected_resource_candidate_id"`
}

type Projection struct {
	Request  llmop.Request `json:"request"`
	Evidence Evidence      `json:"evidence"`
}

// Project validates the Common JSON chain and projects only lossless fields.
// Unsupported topology, GPU-memory, SLO, and cost requirements fail closed.
func Project(input Input) (Projection, error) {
	analysis := input.AnalysisRequest
	applicationContext := input.ApplicationContext
	resourceMessage := input.ResourceRecommendation

	if err := validateEnvelope(
		analysis.Envelope,
		agentcontrol.MessageApplicationAnalysisRequest,
	); err != nil {
		return Projection{}, fmt.Errorf("analysis request envelope: %w", err)
	}
	if err := validateEnvelope(
		applicationContext.Envelope,
		agentcontrol.MessageApplicationContextCreated,
	); err != nil {
		return Projection{}, fmt.Errorf("application context envelope: %w", err)
	}
	if err := validateEnvelope(
		resourceMessage.Envelope,
		agentcontrol.MessageResourceRecommendationCreated,
	); err != nil {
		return Projection{}, fmt.Errorf("resource recommendation envelope: %w", err)
	}
	if err := validateMessageChain(analysis, applicationContext, resourceMessage); err != nil {
		return Projection{}, err
	}

	application := analysis.Data.Application
	profile := applicationContext.Data.ApplicationProfile
	recommendation := resourceMessage.Data.ResourceRecommendation
	if strings.TrimSpace(application.AppID) == "" ||
		strings.TrimSpace(application.AppVersion) == "" ||
		strings.TrimSpace(application.UserRequest) == "" {
		return Projection{}, fmt.Errorf("analysis application identity and user_request are required")
	}
	if strings.TrimSpace(profile.ProfileID) == "" ||
		strings.TrimSpace(profile.AppID) == "" ||
		strings.TrimSpace(profile.AppVersion) == "" {
		return Projection{}, fmt.Errorf("ApplicationProfile identity is required")
	}
	if application.AppID != profile.AppID || application.AppVersion != profile.AppVersion {
		return Projection{}, fmt.Errorf("analysis application identity does not match ApplicationProfile")
	}
	if recommendation.ProfileID != profile.ProfileID {
		return Projection{}, fmt.Errorf("ResourceRecommendation profile_id does not match ApplicationProfile")
	}
	if recommendation.Status != recommendationStatusFound {
		return Projection{}, fmt.Errorf("resource recommendation status must be FOUND")
	}

	selected, err := findSelectedCandidate(recommendation)
	if err != nil {
		return Projection{}, err
	}
	if err := validateSelectedCandidate(profile.Requirements, selected); err != nil {
		return Projection{}, err
	}
	constraints, err := projectConstraints(profile, recommendation, selected)
	if err != nil {
		return Projection{}, err
	}

	request := input.Request
	if err := validateRequestTemplate(request, analysis); err != nil {
		return Projection{}, err
	}
	request.APIVersion = llmop.APIVersion
	request.CorrelationID = analysis.CorrelationID
	request.TraceID = analysis.TraceID
	request.Policy = llmop.RequestPolicy{Mode: llmop.ModePrepareOnly}
	request.Application.UserRequest = application.UserRequest
	request.Application.PlanningConstraints = &constraints

	return Projection{
		Request: request,
		Evidence: Evidence{
			AnalysisMessageID:               analysis.MessageID,
			ApplicationContextMessageID:     applicationContext.MessageID,
			ResourceRecommendationMessageID: resourceMessage.MessageID,
			SourceProfileID:                 profile.ProfileID,
			SourceRecommendationID:          recommendation.RecommendationID,
			SourceSnapshotID:                recommendation.SnapshotID,
			SelectedResourceCandidateID:     selected.CandidateID,
		},
	}, nil
}

func validateEnvelope(envelope agentcontrol.Envelope, expectedType string) error {
	switch {
	case envelope.ContractVersion != agentcontrol.ContractVersionV1:
		return fmt.Errorf("contract_version must be %s", agentcontrol.ContractVersionV1)
	case strings.TrimSpace(envelope.MessageID) == "":
		return fmt.Errorf("message_id is required")
	case envelope.MessageType != expectedType:
		return fmt.Errorf("message_type must be %s", expectedType)
	case strings.TrimSpace(envelope.CorrelationID) == "":
		return fmt.Errorf("correlation_id is required")
	case strings.TrimSpace(envelope.TraceID) == "":
		return fmt.Errorf("trace_id is required")
	case strings.TrimSpace(envelope.Source.System) == "" ||
		strings.TrimSpace(envelope.Source.Component) == "":
		return fmt.Errorf("source system and component are required")
	case strings.TrimSpace(envelope.Target.System) == "" ||
		strings.TrimSpace(envelope.Target.Component) == "":
		return fmt.Errorf("target system and component are required")
	}
	if _, err := time.Parse(time.RFC3339, envelope.OccurredAt); err != nil {
		return fmt.Errorf("occurred_at must be RFC3339: %w", err)
	}
	return nil
}

func validateMessageChain(
	analysis agentcontrol.ApplicationAnalysisRequestEnvelope,
	applicationContext agentcontrol.ApplicationContextEnvelope,
	resourceMessage agentcontrol.ResourceRecommendationEnvelope,
) error {
	if applicationContext.CorrelationID != analysis.CorrelationID ||
		resourceMessage.CorrelationID != analysis.CorrelationID {
		return fmt.Errorf("Common JSON correlation_id values do not match")
	}
	if applicationContext.TraceID != analysis.TraceID ||
		resourceMessage.TraceID != analysis.TraceID {
		return fmt.Errorf("Common JSON trace_id values do not match")
	}
	if applicationContext.CausationID != analysis.MessageID {
		return fmt.Errorf("application context causation_id does not match analysis message_id")
	}
	if resourceMessage.CausationID != applicationContext.MessageID {
		return fmt.Errorf("resource recommendation causation_id does not match context message_id")
	}
	return nil
}

func validateRequestTemplate(
	request llmop.Request,
	analysis agentcontrol.ApplicationAnalysisRequestEnvelope,
) error {
	if request.APIVersion != "" && request.APIVersion != llmop.APIVersion {
		return fmt.Errorf("unsupported llmop api_version")
	}
	if strings.TrimSpace(request.RequestID) == "" ||
		strings.TrimSpace(request.CandidateID) == "" ||
		strings.TrimSpace(request.RequestedBy) == "" ||
		strings.TrimSpace(request.Application.AppVersionID) == "" {
		return fmt.Errorf("caller-supplied llmop identity fields are required")
	}
	if request.Policy.Mode != "" && request.Policy.Mode != llmop.ModePrepareOnly {
		return fmt.Errorf("only prepare_only mode is supported")
	}
	if strings.TrimSpace(request.Policy.ApprovalReference) != "" {
		return fmt.Errorf("approval_reference is not supported by the bridge")
	}
	if request.CorrelationID != "" && request.CorrelationID != analysis.CorrelationID {
		return fmt.Errorf("llmop correlation_id conflicts with Common JSON")
	}
	if request.TraceID != "" && request.TraceID != analysis.TraceID {
		return fmt.Errorf("llmop trace_id conflicts with Common JSON")
	}
	if request.Application.PlanningConstraints != nil {
		return fmt.Errorf("planning_constraints must be projected from Common JSON")
	}
	return nil
}

func findSelectedCandidate(
	recommendation agentcontrol.ResourceRecommendation,
) (agentcontrol.ResourceCandidate, error) {
	if strings.TrimSpace(recommendation.RecommendationID) == "" ||
		strings.TrimSpace(recommendation.SnapshotID) == "" ||
		strings.TrimSpace(recommendation.SelectedCandidateID) == "" {
		return agentcontrol.ResourceCandidate{}, fmt.Errorf("resource recommendation identifiers are required")
	}
	var selected agentcontrol.ResourceCandidate
	matches := 0
	for _, candidate := range recommendation.Candidates {
		if candidate.CandidateID == recommendation.SelectedCandidateID {
			selected = candidate
			matches++
		}
	}
	if matches != 1 {
		return agentcontrol.ResourceCandidate{}, fmt.Errorf("selected resource candidate must exist exactly once")
	}
	if !selected.Feasible {
		return agentcontrol.ResourceCandidate{}, fmt.Errorf("selected resource candidate is not feasible")
	}
	return selected, nil
}

func validateSelectedCandidate(
	requirements agentcontrol.ApplicationRequirements,
	selected agentcontrol.ResourceCandidate,
) error {
	compute := requirements.Compute
	deployment := requirements.Deployment
	actual := selected.DesiredInfrastructure
	if deployment.ReplicasMin != 1 || deployment.ReplicasMax < deployment.ReplicasMin {
		return fmt.Errorf("multi-replica minimum cannot be represented by AppDeploy v1")
	}
	if strings.TrimSpace(deployment.Isolation) != "ONE_MAJOR_APP_PER_VM" ||
		actual.Isolation != deployment.Isolation {
		return fmt.Errorf("deployment isolation is outside the supported VM contract")
	}
	if compute.CPUCoresMin <= 0 || compute.CPUCoresMin > maxCPUCount ||
		compute.MemoryMiBMin <= 0 || compute.MemoryMiBMin > maxMemoryMiB ||
		compute.StorageGiBMin <= 0 || compute.StorageGiBMin > maxStorageGiB {
		return fmt.Errorf("ApplicationProfile compute minima cannot be projected")
	}
	if actual.NodeCount != 1 ||
		actual.CPUCoresPerNode < compute.CPUCoresMin ||
		actual.CPUCoresPerNode > maxCPUCount ||
		actual.MemoryMiBPerNode < compute.MemoryMiBMin ||
		actual.MemoryMiBPerNode > maxMemoryMiB ||
		actual.StorageGiBPerNode < compute.StorageGiBMin ||
		actual.StorageGiBPerNode > maxStorageGiB {
		return fmt.Errorf("selected resource candidate does not satisfy the bounded single-node ApplicationProfile")
	}
	accelerator := requirements.Accelerator
	if accelerator.Required {
		if !supportedNVIDIAType(actual.Accelerator.Type) {
			return fmt.Errorf("selected accelerator type does not match the supported profile")
		}
		if actual.Accelerator.Count < accelerator.CountMin ||
			actual.Accelerator.Count > maxGPUCount ||
			actual.Accelerator.MemoryMiBMinPerDevice < accelerator.MemoryMiBMinPerDevice {
			return fmt.Errorf("selected accelerator does not satisfy the bounded ApplicationProfile")
		}
		if actual.Accelerator.MemoryMiBMinPerDevice != 0 {
			return fmt.Errorf("selected GPU device memory is not represented by AppDeploy v1")
		}
	} else if strings.TrimSpace(actual.Accelerator.Type) != "" ||
		actual.Accelerator.Count != 0 ||
		actual.Accelerator.MemoryMiBMinPerDevice != 0 {
		return fmt.Errorf("CPU-only ApplicationProfile cannot project a GPU resource candidate")
	}
	return nil
}

func projectConstraints(
	profile agentcontrol.ApplicationProfile,
	recommendation agentcontrol.ResourceRecommendation,
	selected agentcontrol.ResourceCandidate,
) (llmop.PlanningConstraints, error) {
	requirements := profile.Requirements
	compute := requirements.Compute
	if compute.CPUCoresMin <= 0 || compute.CPUCoresMin > maxCPUCount ||
		compute.MemoryMiBMin <= 0 || compute.MemoryMiBMin > maxMemoryMiB ||
		compute.StorageGiBMin <= 0 || compute.StorageGiBMin > maxStorageGiB {
		return llmop.PlanningConstraints{}, fmt.Errorf("ApplicationProfile compute minima cannot be projected")
	}
	if requirements.SLO.LatencyP95MSMax != 0 ||
		requirements.SLO.ThroughputRPSMin != 0 {
		return llmop.PlanningConstraints{}, fmt.Errorf("ApplicationProfile SLO values are not represented by the llmop v1alpha1 bridge")
	}
	if strings.TrimSpace(requirements.Cost.Currency) != "" ||
		requirements.Cost.CostPerHourMax != 0 {
		return llmop.PlanningConstraints{}, fmt.Errorf("ApplicationProfile cost values are not represented by the llmop v1alpha1 bridge")
	}
	accelerator, gpuMinimum, err := projectAccelerator(requirements.Accelerator)
	if err != nil {
		return llmop.PlanningConstraints{}, err
	}
	actual := selected.DesiredInfrastructure
	recommendedAccelerator := "none"
	if actual.Accelerator.Count > 0 {
		recommendedAccelerator = "nvidia"
	}
	return llmop.PlanningConstraints{
		SourceProfileID:        profile.ProfileID,
		SourceRecommendationID: recommendation.RecommendationID,
		RecommendationFeasible: true,
		CPUCoresMin:            uint64(compute.CPUCoresMin),
		MemoryMiBMin:           uint64(compute.MemoryMiBMin),
		GPUCountMin:            gpuMinimum,
		StorageGiBMin:          uint64(compute.StorageGiBMin),
		Accelerator:            accelerator,
		RecommendedResources: &llmop.RecommendedResources{
			CPUCores:    uint64(actual.CPUCoresPerNode),
			MemoryMiB:   uint64(actual.MemoryMiBPerNode),
			GPUCount:    uint64(actual.Accelerator.Count),
			StorageGiB:  uint64(actual.StorageGiBPerNode),
			Accelerator: recommendedAccelerator,
		},
	}, nil
}

func projectAccelerator(
	requirements agentcontrol.AcceleratorRequirements,
) (string, uint64, error) {
	if !requirements.Required {
		if strings.TrimSpace(requirements.Type) != "" ||
			requirements.CountMin != 0 ||
			requirements.MemoryMiBMinPerDevice != 0 {
			return "", 0, fmt.Errorf("optional accelerator requirements cannot be projected losslessly")
		}
		return "none", 0, nil
	}
	if requirements.CountMin <= 0 || requirements.CountMin > maxGPUCount {
		return "", 0, fmt.Errorf("required accelerator count_min is outside the supported range")
	}
	if !supportedNVIDIAType(requirements.Type) {
		return "", 0, fmt.Errorf("accelerator type is unsupported by AppDeploy v1")
	}
	if requirements.MemoryMiBMinPerDevice != 0 {
		return "", 0, fmt.Errorf("GPU device memory minimum is not represented by AppDeploy v1")
	}
	return "nvidia", uint64(requirements.CountMin), nil
}

func supportedNVIDIAType(value string) bool {
	return strings.EqualFold(strings.TrimSpace(value), "GPU") ||
		strings.EqualFold(strings.TrimSpace(value), "NVIDIA")
}
