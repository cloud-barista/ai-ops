package agentcontrol

import (
	"context"
	"testing"
)

func BenchmarkRuleBasedDecisionFlow(b *testing.B) {
	service := NewService()
	applicationContext := validApplicationContextEnvelope()
	resourceRecommendation := validResourceRecommendationEnvelope()

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := service.ReceiveApplicationContext(context.Background(), applicationContext); err != nil {
			b.Fatal(err)
		}
		flow, err := service.ReceiveResourceRecommendation(context.Background(), resourceRecommendation)
		if err != nil {
			b.Fatal(err)
		}
		if flow.Decision == nil || flow.Decision.Action != ActionDeploy {
			b.Fatalf("decision = %#v, want DEPLOY", flow.Decision)
		}
	}
}

func BenchmarkRuleBasedOptimizationLifecycle(b *testing.B) {
	service := NewService()
	applicationContext := validApplicationContextEnvelope()
	resourceRecommendation := validResourceRecommendationEnvelope()
	deploymentStatus := validDeploymentStatusEnvelope()
	optimizationFeedback := validOptimizationFeedbackEnvelope()

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		flow, experiences, err := runOptimizationLifecycle(
			service,
			applicationContext,
			resourceRecommendation,
			deploymentStatus,
			optimizationFeedback,
		)
		if err != nil {
			b.Fatal(err)
		}
		if flow.FeedbackSummary == nil || !flow.FeedbackSummary.Success {
			b.Fatalf("feedback summary = %#v, want successful lifecycle", flow.FeedbackSummary)
		}
		if len(experiences) != 1 || !experiences[0].Success {
			b.Fatalf("experiences = %#v, want one successful experience", experiences)
		}
	}
}

func runOptimizationLifecycle(
	service *Service,
	applicationContext ApplicationContextEnvelope,
	resourceRecommendation ResourceRecommendationEnvelope,
	deploymentStatus DeploymentStatusEnvelope,
	optimizationFeedback OptimizationFeedbackEnvelope,
) (Flow, []DeploymentExperience, error) {
	if _, err := service.ReceiveApplicationContext(context.Background(), applicationContext); err != nil {
		return Flow{}, nil, err
	}
	if _, err := service.ReceiveResourceRecommendation(context.Background(), resourceRecommendation); err != nil {
		return Flow{}, nil, err
	}
	if _, err := service.ReceiveDeploymentStatus(context.Background(), deploymentStatus); err != nil {
		return Flow{}, nil, err
	}
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), optimizationFeedback)
	if err != nil {
		return Flow{}, nil, err
	}
	experiences := service.QueryExperiences(ExperienceQuery{
		ProfileID:   resourceRecommendation.Data.ResourceRecommendation.ProfileID,
		CandidateID: resourceRecommendation.Data.ResourceRecommendation.SelectedCandidateID,
		Limit:       1,
	})
	return flow, experiences, nil
}

func TestRuleBasedOptimizationLifecycleOutcome(t *testing.T) {
	service := NewService()
	flow, experiences, err := runOptimizationLifecycle(
		service,
		validApplicationContextEnvelope(),
		validResourceRecommendationEnvelope(),
		validDeploymentStatusEnvelope(),
		validOptimizationFeedbackEnvelope(),
	)
	if err != nil {
		t.Fatalf("run optimization lifecycle: %v", err)
	}
	if flow.State != StateDecisionApproved || flow.RepairDecision != nil {
		t.Fatalf("final flow = %#v, want approved without repair", flow)
	}
	if len(experiences) != 1 || experiences[0].Outcome != FeedbackOutcomeSucceeded || !experiences[0].Success {
		t.Fatalf("experiences = %#v, want one successful experience", experiences)
	}
}
