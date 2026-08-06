package agentcontrol

import (
	"context"
	"fmt"
	"testing"
	"time"
)

type localExperimentMetrics struct {
	cost           float64
	retries        int
	sloViolations  int
	finalSucceeded bool
}

type localExperimentScenario struct {
	name    string
	profile ApplicationProfile
	catalog ResourceCatalog
}

// TestLocalOptimizationExperiment compares a fixed direct choice with the
// local catalog + Agent Control path. Cost and SLO values are synthetic; this
// keeps the test reproducible without cloud credentials or a real inference model.
func TestLocalOptimizationExperiment(t *testing.T) {
	scenarios := []localExperimentScenario{
		localCostScenario(),
		localResourceFailureScenario(),
		localSLOScenario(),
	}

	var baselineTotal, optimizedTotal localExperimentMetrics
	t.Log("scenario | baseline | optimizer")
	for _, scenario := range scenarios {
		baseline := localBaselineMetrics(scenario)
		optimized, flow := localOptimizerMetrics(t, scenario)
		baselineTotal = addLocalMetrics(baselineTotal, baseline)
		optimizedTotal = addLocalMetrics(optimizedTotal, optimized)

		t.Logf(
			"%s | cost=%.2f retries=%d slo=%d success=%t | cost=%.2f retries=%d slo=%d success=%t action=%s",
			scenario.name,
			baseline.cost, baseline.retries, baseline.sloViolations, baseline.finalSucceeded,
			optimized.cost, optimized.retries, optimized.sloViolations, optimized.finalSucceeded,
			localFlowAction(flow),
		)
	}

	if baselineTotal.cost <= 0 || optimizedTotal.cost >= baselineTotal.cost {
		t.Fatalf("optimizer did not reduce synthetic cost: baseline=%#v optimizer=%#v", baselineTotal, optimizedTotal)
	}
	if optimizedTotal.retries >= baselineTotal.retries {
		t.Fatalf("optimizer did not reduce synthetic retries: baseline=%#v optimizer=%#v", baselineTotal, optimizedTotal)
	}
	if optimizedTotal.sloViolations >= baselineTotal.sloViolations {
		t.Fatalf("optimizer did not reduce synthetic SLO violations: baseline=%#v optimizer=%#v", baselineTotal, optimizedTotal)
	}

	costReduction := 100 * (baselineTotal.cost - optimizedTotal.cost) / baselineTotal.cost
	retryReduction := 100 * float64(baselineTotal.retries-optimizedTotal.retries) / float64(baselineTotal.retries)
	sloReduction := 100 * float64(baselineTotal.sloViolations-optimizedTotal.sloViolations) / float64(baselineTotal.sloViolations)
	t.Logf(
		"TOTAL | baseline cost=%.2f retries=%d slo=%d | optimizer cost=%.2f retries=%d slo=%d | reductions cost=%.1f%% retries=%.1f%% slo=%.1f%%",
		baselineTotal.cost, baselineTotal.retries, baselineTotal.sloViolations,
		optimizedTotal.cost, optimizedTotal.retries, optimizedTotal.sloViolations,
		costReduction, retryReduction, sloReduction,
	)
}

func localBaselineMetrics(scenario localExperimentScenario) localExperimentMetrics {
	candidate := scenario.catalog.Candidates[0]
	metrics := localObservation(candidate, scenario.profile)
	if !metrics.finalSucceeded {
		metrics.retries = 1
	}
	if scenario.profile.Requirements.SLO.LatencyP95MSMax > 0 {
		metrics.sloViolations = 1
	}
	return metrics
}

func localOptimizerMetrics(t *testing.T, scenario localExperimentScenario) (localExperimentMetrics, Flow) {
	t.Helper()
	service := NewService()
	application := localApplicationContext(scenario.profile)
	recommender := CatalogResourceRecommender{
		Catalog: scenario.catalog,
		Now:     func() time.Time { return time.Date(2026, 8, 5, 0, 0, 0, 0, time.UTC) },
	}
	recommendation, err := recommender.Recommend(context.Background(), scenario.profile)
	if err != nil {
		t.Fatalf("recommend %s: %v", scenario.name, err)
	}
	if _, err := service.ReceiveApplicationContext(context.Background(), application); err != nil {
		t.Fatalf("receive application context %s: %v", scenario.name, err)
	}
	flow, err := service.ReceiveResourceRecommendation(
		context.Background(),
		localResourceRecommendation(application, recommendation),
	)
	if err != nil {
		t.Fatalf("receive resource recommendation %s: %v", scenario.name, err)
	}

	selected := findLocalCatalogCandidate(scenario.catalog, flow.Decision.SelectedCandidateID)
	metrics := localObservation(selected, scenario.profile)
	if !metrics.finalSucceeded {
		metrics.retries = 1
	}

	if scenario.name == "slo_feedback" {
		flow = localSLOFeedback(t, service, application, flow)
		if flow.ScalingDecision == nil || flow.ScalingDecision.Action != ScalingActionScaleOut {
			t.Fatalf("SLO scenario scaling decision = %#v, want SCALE_OUT", flow.ScalingDecision)
		}
		// The local runtime model applies one bounded replica increase.
		metrics.sloViolations = 0
	}
	return metrics, flow
}

func localObservation(candidate CatalogResource, profile ApplicationProfile) localExperimentMetrics {
	requirements := profile.Requirements
	return localExperimentMetrics{
		cost: candidate.CostPerHour,
		finalSucceeded: candidate.CPUCores >= requirements.Compute.CPUCoresMin &&
			candidate.MemoryMiB >= requirements.Compute.MemoryMiBMin &&
			candidate.StorageGiB >= requirements.Compute.StorageGiBMin,
	}
}

func localSLOFeedback(
	t *testing.T,
	service *Service,
	application ApplicationContextEnvelope,
	flow Flow,
) Flow {
	status := validDeploymentStatusEnvelope()
	status.CorrelationID = application.CorrelationID
	status.TraceID = application.TraceID
	status.Data.DeploymentStatus.DecisionID = flow.Decision.DecisionID
	status.Data.DeploymentStatus.DeploymentID = "deployment-" + application.CorrelationID
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive SLO deployment status: %v", err)
	}

	feedback := validOptimizationFeedbackEnvelope()
	feedback.CorrelationID = application.CorrelationID
	feedback.TraceID = application.TraceID
	feedback.CausationID = status.MessageID
	feedback.Data.OptimizationFeedback.DecisionID = flow.Decision.DecisionID
	feedback.Data.OptimizationFeedback.DeploymentID = status.Data.DeploymentStatus.DeploymentID
	feedback.Data.OptimizationFeedback.Metrics.Inference.LatencyP95MS = 2500
	feedback.Data.OptimizationFeedback.Metrics.Inference.ThroughputRPS = 4
	feedback.Data.OptimizationFeedback.SLOViolations = nil
	updated, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive SLO feedback: %v", err)
	}
	return updated
}

func localApplicationContext(profile ApplicationProfile) ApplicationContextEnvelope {
	base := validApplicationContextEnvelope()
	base.CorrelationID = profile.ProfileID
	base.TraceID = "trace-" + profile.ProfileID
	base.MessageID = "msg-context-" + profile.ProfileID
	base.Data.ApplicationProfile = profile
	return base
}

func localResourceRecommendation(
	application ApplicationContextEnvelope,
	recommendation RecommendationResult,
) ResourceRecommendationEnvelope {
	return ResourceRecommendationEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-resource-" + application.CorrelationID,
			MessageType:     MessageResourceRecommendationCreated,
			OccurredAt:      "2026-08-05T00:01:00Z",
			CorrelationID:   application.CorrelationID,
			TraceID:         application.TraceID,
			CausationID:     application.MessageID,
			Source:          Endpoint{System: "local-experiment", Component: "catalog-recommender"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: ResourceRecommendationData{ResourceRecommendation: recommendation.ResourceRecommendation},
	}
}

func localCostScenario() localExperimentScenario {
	profile := localExperimentProfile("cost_saving", 8, 16384, 100)
	return localExperimentScenario{
		name:    "cost_saving",
		profile: profile,
		catalog: ResourceCatalog{
			Version: "local-cost-v1",
			Candidates: []CatalogResource{
				localCatalogResource("large-expensive", 16, 32768, 200, 2.00, 0.99),
				localCatalogResource("small-cheap", 8, 16384, 100, 0.50, 0.99),
			},
		},
	}
}

func localResourceFailureScenario() localExperimentScenario {
	profile := localExperimentProfile("retry_reduction", 8, 16384, 100)
	return localExperimentScenario{
		name:    "retry_reduction",
		profile: profile,
		catalog: ResourceCatalog{
			Version: "local-retry-v1",
			Candidates: []CatalogResource{
				localCatalogResource("infeasible-direct", 4, 8192, 50, 0.20, 0.99),
				localCatalogResource("feasible-recommended", 8, 16384, 100, 0.80, 0.99),
			},
		},
	}
}

func localSLOScenario() localExperimentScenario {
	profile := localExperimentProfile("slo_feedback", 8, 16384, 100)
	profile.Requirements.Deployment.ReplicasMax = 2
	profile.Requirements.SLO.LatencyP95MSMax = 2000
	profile.Requirements.SLO.ThroughputRPSMin = 5
	return localExperimentScenario{
		name:    "slo_feedback",
		profile: profile,
		catalog: ResourceCatalog{
			Version:    "local-slo-v1",
			Candidates: []CatalogResource{localCatalogResource("slo-node", 8, 16384, 100, 1.00, 0.99)},
		},
	}
}

func localExperimentProfile(id string, cpu, memory, storage int) ApplicationProfile {
	profile := validApplicationContextEnvelope().Data.ApplicationProfile
	profile.ProfileID = id
	profile.AppID = "local-optimizer-app"
	profile.AppVersion = "1.0.0"
	profile.Requirements.Compute = ComputeRequirements{CPUCoresMin: cpu, MemoryMiBMin: memory, StorageGiBMin: storage}
	profile.Requirements.Accelerator = AcceleratorRequirements{}
	profile.Requirements.Deployment = DeploymentRequirements{ReplicasMin: 1, ReplicasMax: 1, Isolation: "SHARED"}
	profile.Requirements.SLO = SLORequirements{}
	profile.Requirements.Cost = CostRequirements{}
	return profile
}

func localCatalogResource(id string, cpu, memory, storage int, cost, availability float64) CatalogResource {
	return CatalogResource{
		CandidateID:       id,
		CPUCores:          cpu,
		MemoryMiB:         memory,
		StorageGiB:        storage,
		CostPerHour:       cost,
		AvailabilityScore: availability,
	}
}

func findLocalCatalogCandidate(catalog ResourceCatalog, candidateID string) CatalogResource {
	for _, candidate := range catalog.Candidates {
		if candidate.CandidateID == candidateID {
			return candidate
		}
	}
	panic(fmt.Sprintf("candidate %q not found in local catalog", candidateID))
}

func localFlowAction(flow Flow) string {
	if flow.ScalingDecision != nil && flow.ScalingDecision.Action != ScalingActionNoAction {
		return flow.ScalingDecision.Action
	}
	if flow.Decision != nil {
		if flow.RepairDecision != nil {
			return flow.RepairDecision.Action
		}
		return flow.Decision.Action
	}
	return "NONE"
}

func addLocalMetrics(left, right localExperimentMetrics) localExperimentMetrics {
	return localExperimentMetrics{
		cost:           left.cost + right.cost,
		retries:        left.retries + right.retries,
		sloViolations:  left.sloViolations + right.sloViolations,
		finalSucceeded: left.finalSucceeded && right.finalSucceeded,
	}
}
