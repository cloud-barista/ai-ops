package agentcontrol

import (
	"testing"
	"time"
)

var fixedScalingTime = time.Date(2026, 7, 29, 7, 0, 0, 0, time.UTC)

func TestEvaluateScalingDecision(t *testing.T) {
	tests := []struct {
		name            string
		flow            Flow
		wantAction      string
		wantReplicas    int
		wantEvidenceLen int
	}{
		{
			name:            "SLO violation scales out within maximum",
			flow:            scalingTestFlow(1, 1, 3, 85, 92, []string{"latency_p95_ms"}),
			wantAction:      ScalingActionScaleOut,
			wantReplicas:    2,
			wantEvidenceLen: 1,
		},
		{
			name:            "healthy low utilization scales in above minimum",
			flow:            scalingTestFlow(2, 1, 3, 18, 22, nil),
			wantAction:      ScalingActionScaleIn,
			wantReplicas:    1,
			wantEvidenceLen: 2,
		},
		{
			name:         "healthy workload at minimum remains unchanged",
			flow:         scalingTestFlow(1, 1, 3, 55, 61, nil),
			wantAction:   ScalingActionKeep,
			wantReplicas: 1,
		},
		{
			name:            "SLO violation at maximum remains unchanged",
			flow:            scalingTestFlow(3, 1, 3, 90, 95, []string{"throughput_rps"}),
			wantAction:      ScalingActionKeep,
			wantReplicas:    3,
			wantEvidenceLen: 1,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			decision := ProposeRuleBasedScalingDecision(test.flow, fixedScalingTime)
			if decision == nil {
				t.Fatal("scaling decision is nil")
			}
			if decision.Action != test.wantAction {
				t.Fatalf("action = %q, want %q", decision.Action, test.wantAction)
			}
			if decision.DesiredReplicas != test.wantReplicas {
				t.Fatalf("desired replicas = %d, want %d", decision.DesiredReplicas, test.wantReplicas)
			}
			if len(decision.Evidence) != test.wantEvidenceLen {
				t.Fatalf("evidence = %#v, want length %d", decision.Evidence, test.wantEvidenceLen)
			}
			if decision.CreatedAt != fixedScalingTime.Format(time.RFC3339Nano) {
				t.Fatalf("created_at = %q", decision.CreatedAt)
			}
		})
	}
}

func TestEvaluateScalingDecisionWaitsForRuntimeEvidence(t *testing.T) {
	withoutStatus := scalingTestFlow(1, 1, 3, 50, 50, nil)
	withoutStatus.DeploymentStatus = nil
	if decision := ProposeRuleBasedScalingDecision(withoutStatus, fixedScalingTime); decision != nil {
		t.Fatalf("decision = %#v, want nil before deployment status", decision)
	}

	withoutMetrics := scalingTestFlow(1, 1, 3, 50, 50, nil)
	withoutMetrics.OptimizationFeedback = nil
	decision := ProposeRuleBasedScalingDecision(withoutMetrics, fixedScalingTime)
	if decision == nil || decision.Action != ScalingActionKeep {
		t.Fatalf("decision = %#v, want KEEP while metrics are missing", decision)
	}
}

func TestEvaluateScalingDecisionDoesNotScaleFailedDeployment(t *testing.T) {
	flow := scalingTestFlow(1, 1, 3, 95, 98, []string{"latency_p95_ms"})
	flow.DeploymentStatus.Data.DeploymentStatus.State = DeploymentStateFailed

	decision := ProposeRuleBasedScalingDecision(flow, fixedScalingTime)
	if decision == nil || decision.Action != ScalingActionKeep {
		t.Fatalf("decision = %#v, want KEEP for failed deployment", decision)
	}
	if decision.DesiredReplicas != 1 {
		t.Fatalf("desired replicas = %d, want 1", decision.DesiredReplicas)
	}
}

func TestNormalizeScalingActionCanonicalizesLegacyNoAction(t *testing.T) {
	for _, test := range []struct {
		action string
		want   string
	}{
		{action: ScalingActionKeep, want: ScalingActionKeep},
		{action: ScalingActionNoAction, want: ScalingActionKeep},
	} {
		if got := normalizeScalingAction(test.action); got != test.want {
			t.Fatalf("normalizeScalingAction(%q) = %q, want %q", test.action, got, test.want)
		}
	}
}

func scalingTestFlow(
	current int,
	minimum int,
	maximum int,
	cpuAverage float64,
	acceleratorAverage float64,
	sloViolations []string,
) Flow {
	return Flow{
		ApplicationContext: &ApplicationContextEnvelope{
			Data: ApplicationContextData{
				ApplicationProfile: ApplicationProfile{
					Requirements: ApplicationRequirements{
						Deployment: DeploymentRequirements{
							ReplicasMin: minimum,
							ReplicasMax: maximum,
						},
						SLO: SLORequirements{
							LatencyP95MSMax:  2000,
							ThroughputRPSMin: 5,
						},
					},
				},
			},
		},
		DeploymentPlan: &DeploymentPlan{
			InferenceConfiguration: InferenceConfiguration{
				Replicas: current,
			},
		},
		DeploymentStatus: &DeploymentStatusEnvelope{
			Data: DeploymentStatusData{
				DeploymentStatus: DeploymentStatus{
					State: DeploymentStateRunning,
				},
			},
		},
		OptimizationFeedback: &OptimizationFeedbackEnvelope{
			Data: OptimizationFeedbackData{
				OptimizationFeedback: OptimizationFeedback{
					Outcome:       FeedbackOutcomeSucceeded,
					SLOViolations: append([]string(nil), sloViolations...),
					Metrics: OptimizationMetrics{
						Resource: ResourceMetrics{
							CPUAveragePercent:         cpuAverage,
							AcceleratorAveragePercent: acceleratorAverage,
						},
						Inference: InferenceMetrics{
							LatencyP95MS:  1000,
							ThroughputRPS: 6,
						},
					},
				},
			},
		},
	}
}
