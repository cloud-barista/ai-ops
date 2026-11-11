package agentcontrol

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

type stubAuthorizer struct {
	result AgentAuthorization
	err    error
}

func (stub stubAuthorizer) Authorize(
	_ context.Context,
	request AgentAuthorizationRequest,
) (AgentAuthorization, error) {
	result := stub.result
	result.AgentName = request.AgentName
	result.Capability = request.Capability
	result.Action = request.Action
	return result, stub.err
}

func TestServiceRejectsFlowWhenAutomationAgentIsNotAuthorized(t *testing.T) {
	service := NewServiceWithDependencies(
		nil,
		stubAuthorizer{
			result: AgentAuthorization{
				Authorized: false,
				Reason:     "required bounded action is not registered",
			},
		},
	)

	if _, err := service.ReceiveApplicationContext(
		context.Background(),
		validApplicationContextEnvelope(),
	); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	flow, err := service.ReceiveResourceRecommendation(
		context.Background(),
		validResourceRecommendationEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}

	if flow.State != StateAgentAuthorizationRejected {
		t.Fatalf("state = %q, want %q", flow.State, StateAgentAuthorizationRejected)
	}
	if flow.AgentAuthorization == nil || flow.AgentAuthorization.Authorized {
		t.Fatalf("agent authorization = %#v, want rejected", flow.AgentAuthorization)
	}
	if flow.DeploymentPlan != nil || flow.DeploymentRequest != nil {
		t.Fatal("unauthorized Agent must not create a deployment plan or request")
	}
	if flow.Guard == nil || flow.Guard.Status != GuardRejected {
		t.Fatalf("guard = %#v, want REJECTED", flow.Guard)
	}
}

func TestServiceCreatesDeployPlanForFeasibleRecommendation(t *testing.T) {
	service := NewService()

	waiting, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope())
	if err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	if waiting.State != StateWaitingForRecommendation {
		t.Fatalf("state = %q, want %q", waiting.State, StateWaitingForRecommendation)
	}
	ready, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope())
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if ready.State != StateDecisionApproved {
		t.Fatalf("state = %q, want %q", ready.State, StateDecisionApproved)
	}
	if ready.Decision == nil || ready.Decision.Action != ActionDeploy {
		t.Fatalf("decision = %#v, want DEPLOY", ready.Decision)
	}
	if ready.Decision.ReasoningMode != ReasoningModeRuleBased {
		t.Fatalf("reasoning mode = %q", ready.Decision.ReasoningMode)
	}
	if ready.DeploymentPlan == nil {
		t.Fatal("deployment plan was not created")
	}
	if ready.DeploymentPlan.SelectedCandidateID != "candidate-001" {
		t.Fatalf("selected candidate = %q", ready.DeploymentPlan.SelectedCandidateID)
	}
	if ready.DeploymentPlan.DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice != 24576 {
		t.Fatalf(
			"planned accelerator memory = %d",
			ready.DeploymentPlan.DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice,
		)
	}
	if ready.CorrelationID != "flow-001" || ready.TraceID != "trace-001" || ready.ProfileID != "profile-001" {
		t.Fatalf("flow identifiers were not preserved: %+v", ready)
	}
	if ready.Guard == nil || ready.Guard.Status != GuardApproved {
		t.Fatalf("guard = %#v, want APPROVED", ready.Guard)
	}
	if ready.DeploymentRequest == nil {
		t.Fatal("approved plan must create deployment.create.request")
	}
	if ready.DeploymentRequest.MessageType != MessageDeploymentCreateRequest {
		t.Fatalf("message type = %q", ready.DeploymentRequest.MessageType)
	}
	deploymentRequest := ready.DeploymentRequest.Data.DeploymentRequest
	manifest := deploymentRequest.DeploymentManifest
	if deploymentRequest.DecisionID != ready.Decision.DecisionID ||
		manifest.DecisionID != ready.Decision.DecisionID {
		t.Fatalf(
			"decision IDs are inconsistent: decision=%q request=%q manifest=%q",
			ready.Decision.DecisionID,
			deploymentRequest.DecisionID,
			manifest.DecisionID,
		)
	}
	if deploymentRequest.Application.AppID != manifest.Application.AppID ||
		deploymentRequest.Application.AppVersion != manifest.Application.AppVersion {
		t.Fatalf("application identity is inconsistent: %#v %#v", deploymentRequest.Application, manifest.Application)
	}
	if manifest.Metadata.ProfileID != ready.ProfileID {
		t.Fatalf("manifest profile_id = %q, want %q", manifest.Metadata.ProfileID, ready.ProfileID)
	}
	encoded, err := json.Marshal(ready.DeploymentRequest)
	if err != nil {
		t.Fatalf("marshal deployment request: %v", err)
	}
	if strings.Contains(string(encoded), ":null") {
		t.Fatalf("Common JSON must omit missing values instead of null: %s", encoded)
	}
}

func TestServiceCreatesPlatformNeutralDesiredDeploymentSpec(t *testing.T) {
	service := NewService()
	if _, err := service.ReceiveApplicationContext(
		context.Background(),
		validApplicationContextEnvelope(),
	); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	flow, err := service.ReceiveResourceRecommendation(
		context.Background(),
		validResourceRecommendationEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}

	if flow.DesiredDeploymentSpec == nil {
		t.Fatal("approved flow must expose a DesiredDeploymentSpec")
	}
	spec := flow.DesiredDeploymentSpec
	if spec.SpecVersion != ContractVersionV1 {
		t.Fatalf("spec version = %q, want %q", spec.SpecVersion, ContractVersionV1)
	}
	if flow.DeploymentRequest == nil {
		t.Fatal("compatibility deployment request was not created")
	}
	manifest := flow.DeploymentRequest.Data.DeploymentRequest.DeploymentManifest
	if !reflect.DeepEqual(spec.DesiredInfrastructure, manifest.DesiredInfrastructure) {
		t.Fatalf("spec and compatibility manifest infrastructure differ: %#v %#v", spec, manifest)
	}
	if spec.InferenceConfiguration != manifest.InferenceConfiguration {
		t.Fatalf("spec and compatibility manifest inference configuration differ: %#v %#v", spec, manifest)
	}
	encoded, err := json.Marshal(spec)
	if err != nil {
		t.Fatalf("marshal DesiredDeploymentSpec: %v", err)
	}
	for _, forbidden := range []string{"actual_vm_id", "target_profile_id", "credential", "secret"} {
		if strings.Contains(strings.ToLower(string(encoded)), forbidden) {
			t.Fatalf("DesiredDeploymentSpec contains forbidden platform field %q: %s", forbidden, encoded)
		}
	}
}

func TestApprovedFlowPersistsInitialManifestRevision(t *testing.T) {
	service := NewService()
	created := createApprovedFlow(t, service)

	revisions := manifestRevisionPayloads(t, created)
	if len(revisions) != 1 {
		t.Fatalf("manifest revisions = %d, want 1", len(revisions))
	}
	initial := manifestRevisionMap(t, revisions[0])
	if initial["revision"] != float64(1) || initial["phase"] != "INITIAL" {
		t.Fatalf("initial manifest revision = %#v", initial)
	}
	manifest := manifestFromRevision(t, revisions[0])
	if manifest["manifest_version"] != ContractVersionV1 {
		t.Fatalf("manifest schema version = %#v, want %q", manifest["manifest_version"], ContractVersionV1)
	}

	stored, ok := service.GetFlow(created.CorrelationID)
	if !ok || len(manifestRevisionPayloads(t, stored)) != 1 {
		t.Fatalf("stored Flow did not preserve its initial Manifest: %#v", stored)
	}
}

func TestReevaluatingFlowPreservesExistingManifestRevisions(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	contextMessage := validApplicationContextEnvelope()
	contextMessage.MessageID = "msg-context-replanned"
	flow, err := service.ReceiveApplicationContext(context.Background(), contextMessage)
	if err != nil {
		t.Fatalf("receive replanned application context: %v", err)
	}

	revisions := manifestRevisionPayloads(t, flow)
	if len(revisions) != 2 {
		t.Fatalf("replanned manifest revisions = %d, want 2", len(revisions))
	}
	if manifestRevisionMap(t, revisions[0])["revision"] != float64(1) ||
		manifestRevisionMap(t, revisions[1])["revision"] != float64(2) {
		t.Fatalf("replanned manifest revision history = %#v", revisions)
	}
}

func TestPlanningInputReplayDoesNotCreateManifestRevision(t *testing.T) {
	service := NewService()
	created := createApprovedFlow(t, service)

	replayed, err := service.ReceiveResourceRecommendation(
		context.Background(), validResourceRecommendationEnvelope(),
	)
	if err != nil {
		t.Fatalf("replay resource recommendation: %v", err)
	}
	if len(manifestRevisionPayloads(t, replayed)) != 1 || replayed.UpdatedAt != created.UpdatedAt {
		t.Fatalf("input replay changed the Flow: before=%#v after=%#v", created, replayed)
	}
}

func TestApprovedScaleOutCreatesSecondManifestRevision(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}

	revisions := manifestRevisionPayloads(t, flow)
	if len(revisions) != 2 {
		t.Fatalf("manifest revisions = %d, want 2", len(revisions))
	}
	optimized := manifestRevisionMap(t, revisions[1])
	if optimized["revision"] != float64(2) || optimized["phase"] != "OPTIMIZED" ||
		optimized["trigger_action"] != ScalingActionScaleOut {
		t.Fatalf("optimized manifest revision = %#v", optimized)
	}
	initialReplicas := manifestReplicas(t, manifestFromRevision(t, revisions[0]))
	optimizedManifest := manifestFromRevision(t, revisions[1])
	optimizedReplicas := manifestReplicas(t, optimizedManifest)
	if initialReplicas != 1 || optimizedReplicas != 2 {
		t.Fatalf("manifest replicas = %v -> %v, want 1 -> 2", initialReplicas, optimizedReplicas)
	}
	if optimizedManifest["manifest_version"] != ContractVersionV1 {
		t.Fatalf("optimized manifest schema version = %#v, want %q", optimizedManifest["manifest_version"], ContractVersionV1)
	}
	if flow.DesiredDeploymentSpec == nil || flow.DesiredDeploymentSpec.InferenceConfiguration.Replicas != 2 ||
		flow.DeploymentRequest == nil || flow.DeploymentRequest.CausationID != feedback.MessageID {
		t.Fatalf("latest Flow artifacts were not updated from approved feedback: %#v", flow)
	}
	if flow.DeploymentPlan == nil || flow.DeploymentPlan.InferenceConfiguration.Replicas != 2 {
		t.Fatalf("latest Flow deployment plan replicas = %#v, want 2", flow.DeploymentPlan)
	}
	initialDecisionID := flow.Decision.DecisionID
	scaleDecisionID := flow.ScalingDecision.DecisionID
	if scaleDecisionID == "" || scaleDecisionID == initialDecisionID ||
		flow.OperationAgentExecution == nil || flow.OperationAgentExecution.Decision.DecisionID != scaleDecisionID ||
		flow.ManifestRevisions[0].TriggerDecisionID != "" ||
		flow.ManifestRevisions[1].TriggerDecisionID != scaleDecisionID {
		t.Fatalf("scale decision provenance was not linked across Flow and revision: %#v", flow)
	}
	if flow.ManifestRevisions[1].DeploymentRequest.Data.DeploymentRequest.DecisionID != initialDecisionID ||
		flow.ManifestRevisions[1].DesiredDeploymentSpec.DecisionID != initialDecisionID {
		t.Fatal("scale provenance changed the existing Common JSON decision_id")
	}
	replayed, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil || replayed.ScalingDecision == nil || replayed.ScalingDecision.DecisionID != scaleDecisionID ||
		len(replayed.ManifestRevisions) != 2 || replayed.ManifestRevisions[1].TriggerDecisionID != scaleDecisionID ||
		runtime.calls != 1 {
		t.Fatalf("same feedback replay changed the scale decision provenance: %#v err=%v calls=%d", replayed.ScalingDecision, err, runtime.calls)
	}
	stored, ok := service.GetFlow(flow.CorrelationID)
	if !ok || len(manifestRevisionPayloads(t, stored)) != 2 {
		t.Fatalf("stored Flow did not preserve both Manifest revisions: %#v", stored)
	}
}

func TestDeploymentStatusRejectsOlderAndSameTimeNewMessages(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	latest := validDeploymentStatusEnvelope()
	latest.MessageID = "msg-deployment-status-failed-0330"
	latest.OccurredAt = "2026-07-29T03:30:00Z"
	latest.Data.DeploymentStatus.UpdatedAt = latest.OccurredAt
	latest.Data.DeploymentStatus.State = DeploymentStateFailed
	if _, err := service.ReceiveDeploymentStatus(context.Background(), latest); err != nil {
		t.Fatalf("receive latest status: %v", err)
	}
	for _, test := range []struct {
		name       string
		updatedAt  string
		occurredAt string
	}{
		{name: "older status", updatedAt: "2026-07-29T03:10:00Z", occurredAt: "2026-07-29T03:10:00Z"},
		{name: "same-time conflict", updatedAt: latest.Data.DeploymentStatus.UpdatedAt, occurredAt: latest.OccurredAt},
		{name: "status updated after event", updatedAt: "2026-07-29T03:31:00Z", occurredAt: latest.OccurredAt},
	} {
		t.Run(test.name, func(t *testing.T) {
			incoming := validDeploymentStatusEnvelope()
			incoming.MessageID = "msg-deployment-status-running-" + test.name
			incoming.OccurredAt = test.occurredAt
			incoming.Data.DeploymentStatus.UpdatedAt = test.updatedAt
			if _, err := service.ReceiveDeploymentStatus(context.Background(), incoming); err == nil {
				t.Fatal("accepted an out-of-order or internally inconsistent status")
			}
			stored, ok := service.GetFlow(latest.CorrelationID)
			if !ok || stored.DeploymentStatus == nil || stored.DeploymentStatus.MessageID != latest.MessageID ||
				stored.DeploymentStatus.Data.DeploymentStatus.State != DeploymentStateFailed ||
				stored.FeedbackSummary == nil || stored.FeedbackSummary.Success {
				t.Fatalf("stale status changed the current Flow: %+v", stored)
			}
		})
	}
	if replay, err := service.ReceiveDeploymentStatus(context.Background(), latest); err != nil ||
		replay.DeploymentStatus == nil || replay.DeploymentStatus.MessageID != latest.MessageID {
		t.Fatalf("same-ID status replay is not idempotent: %+v %v", replay, err)
	}
	changedSameID := latest
	changedSameID.Data.DeploymentStatus.State = DeploymentStateRunning
	if _, err := service.ReceiveDeploymentStatus(context.Background(), changedSameID); err == nil {
		t.Fatal("same message_id with a changed deployment state was accepted")
	}
	stored, ok := service.GetFlow(latest.CorrelationID)
	if !ok || stored.DeploymentStatus == nil || stored.DeploymentStatus.Data.DeploymentStatus.State != DeploymentStateFailed {
		t.Fatalf("changed same-ID status altered stored Flow: %#v", stored)
	}
}

func TestOptimizationFeedbackRejectsInconsistentObservationAndCreationTimes(t *testing.T) {
	for _, test := range []struct {
		name       string
		endedAt    string
		createdAt  string
		occurredAt string
	}{
		{name: "observation after created_at", endedAt: "2026-07-29T03:30:30Z", createdAt: "2026-07-29T03:30:00Z", occurredAt: "2026-07-29T03:31:00Z"},
		{name: "observation after occurred_at", endedAt: "2026-07-29T03:30:30Z", createdAt: "2026-07-29T03:31:00Z", occurredAt: "2026-07-29T03:30:00Z"},
		{name: "created_at after occurred_at", endedAt: "2026-07-29T03:30:00Z", createdAt: "2026-07-29T03:31:00Z", occurredAt: "2026-07-29T03:30:00Z"},
	} {
		t.Run(test.name, func(t *testing.T) {
			runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
			service := NewServiceWithRuntimes(nil, nil, nil, runtime)
			createApprovedFlow(t, service)
			if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
				t.Fatal(err)
			}
			feedback := validOptimizationFeedbackEnvelope()
			feedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = test.endedAt
			feedback.Data.OptimizationFeedback.CreatedAt = test.createdAt
			feedback.OccurredAt = test.occurredAt
			feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
			if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err == nil {
				t.Fatal("accepted inconsistent feedback timestamps")
			}
			stored, ok := service.GetFlow(feedback.CorrelationID)
			if !ok || stored.OptimizationFeedback != nil || len(stored.ManifestRevisions) != 1 || runtime.calls != 0 {
				t.Fatalf("invalid future observation changed Flow or ran Operation Agent: %+v calls=%d", stored, runtime.calls)
			}
		})
	}
}

func TestOlderOptimizationFeedbackCannotReplaceNewerFailedOutcome(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatal(err)
	}
	failed := validOptimizationFeedbackEnvelope()
	failed.Data.OptimizationFeedback.Outcome = FeedbackOutcomeFailed
	failed.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), failed); err != nil {
		t.Fatalf("receive newer failed feedback: %v", err)
	}
	for _, test := range []struct {
		name    string
		endedAt string
	}{
		{name: "late older observation", endedAt: "2026-07-29T03:20:00Z"},
		{name: "same-window new ID", endedAt: failed.Data.OptimizationFeedback.ObservationWindow.EndedAt},
	} {
		t.Run(test.name, func(t *testing.T) {
			old := validOptimizationFeedbackEnvelope()
			old.MessageID = "msg-feedback-" + test.name
			old.Data.OptimizationFeedback.FeedbackID = "feedback-" + test.name
			old.Data.OptimizationFeedback.ObservationWindow.EndedAt = test.endedAt
			old.Data.OptimizationFeedback.CreatedAt = "2026-07-29T03:40:00Z"
			old.OccurredAt = old.Data.OptimizationFeedback.CreatedAt
			old.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
			if _, err := service.ReceiveOptimizationFeedback(context.Background(), old); err == nil {
				t.Fatal("accepted stale successful feedback after newer failed outcome")
			}
			stored, ok := service.GetFlow(failed.CorrelationID)
			if !ok || stored.OptimizationFeedback == nil || stored.OptimizationFeedback.MessageID != failed.MessageID ||
				stored.FeedbackSummary == nil || stored.FeedbackSummary.Outcome != FeedbackOutcomeFailed ||
				stored.ScalingDecision != nil || len(stored.ManifestRevisions) != 1 || runtime.calls != 1 {
				t.Fatalf("stale feedback changed the current Flow: %+v calls=%d", stored, runtime.calls)
			}
		})
	}
	if replay, err := service.ReceiveOptimizationFeedback(context.Background(), failed); err != nil ||
		replay.OptimizationFeedback == nil || replay.OptimizationFeedback.MessageID != failed.MessageID || runtime.calls != 1 {
		t.Fatalf("same-ID feedback replay is not idempotent: %+v %v calls=%d", replay, err, runtime.calls)
	}
}

func TestScalingWaitsForRevisionStatusAndNewObservation(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	service.now = func() time.Time { return time.Date(2026, 7, 29, 3, 0, 0, 0, time.UTC) }
	contextMessage := validApplicationContextEnvelope()
	contextMessage.Data.ApplicationProfile.Requirements.Deployment.ReplicasMax = 3
	if _, err := service.ReceiveApplicationContext(context.Background(), contextMessage); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	if _, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope()); err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive initial deployment status: %v", err)
	}

	service.now = func() time.Time { return time.Date(2026, 7, 29, 3, 30, 30, 0, time.UTC) }
	firstFeedback := validOptimizationFeedbackEnvelope()
	firstFeedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	first, err := service.ReceiveOptimizationFeedback(context.Background(), firstFeedback)
	if err != nil || first.ScalingDecision == nil || first.ScalingDecision.Action != ScalingActionScaleOut ||
		first.DeploymentPlan.InferenceConfiguration.Replicas != 2 || len(first.ManifestRevisions) != 2 {
		t.Fatalf("initial scale out failed: flow=%#v err=%v", first, err)
	}

	runtime.result.Decision.CurrentReplicas = 2
	runtime.result.Decision.DesiredReplicas = 3
	repeated := firstFeedback
	repeated.MessageID = "msg-feedback-repeated-window"
	repeated.OccurredAt = "2026-07-29T03:31:00Z"
	repeated.Data.OptimizationFeedback.FeedbackID = "feedback-repeated-window"
	repeated.Data.OptimizationFeedback.CreatedAt = "2026-07-29T03:31:00Z"
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), repeated); err == nil {
		t.Fatal("accepted a second feedback message for the same observation window")
	}

	backdatedStatus := validDeploymentStatusEnvelope()
	backdatedStatus.MessageID = "msg-deployment-status-before-revision-2"
	backdatedStatus.CausationID = first.ManifestRevisions[1].DeploymentRequest.MessageID
	backdatedStatus.OccurredAt = "2026-07-29T03:30:00Z"
	backdatedStatus.Data.DeploymentStatus.UpdatedAt = backdatedStatus.OccurredAt
	if _, err := service.ReceiveDeploymentStatus(context.Background(), backdatedStatus); err != nil {
		t.Fatalf("receive backdated revision status: %v", err)
	}
	backdatedFeedback := firstFeedback
	backdatedFeedback.MessageID = "msg-feedback-after-backdated-status"
	backdatedFeedback.CausationID = backdatedStatus.MessageID
	backdatedFeedback.OccurredAt = "2026-07-29T03:31:00Z"
	backdatedFeedback.Data.OptimizationFeedback.FeedbackID = "feedback-after-backdated-status"
	backdatedFeedback.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:30:00Z"
	backdatedFeedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:30:30Z"
	backdatedFeedback.Data.OptimizationFeedback.CreatedAt = backdatedFeedback.OccurredAt
	backdatedFlow, err := service.ReceiveOptimizationFeedback(context.Background(), backdatedFeedback)
	if err != nil {
		t.Fatalf("receive observation after backdated status: %v", err)
	}
	if backdatedFlow.ScalingDecision != nil || len(backdatedFlow.ManifestRevisions) != 2 ||
		!hasGuardCheck(operationScalingGuard(t, backdatedFlow), "deployment_status_after_request", false) {
		t.Fatalf("backdated status changed scaling state: %#v", backdatedFlow)
	}

	unlinkedStatus := validDeploymentStatusEnvelope()
	unlinkedStatus.MessageID = "msg-deployment-status-without-revision-ack"
	unlinkedStatus.OccurredAt = "2026-07-29T03:31:00Z"
	unlinkedStatus.Data.DeploymentStatus.UpdatedAt = unlinkedStatus.OccurredAt
	if _, err := service.ReceiveDeploymentStatus(context.Background(), unlinkedStatus); err != nil {
		t.Fatalf("receive status without revision acknowledgement: %v", err)
	}
	unlinkedFeedback := firstFeedback
	unlinkedFeedback.MessageID = "msg-feedback-after-unlinked-status"
	unlinkedFeedback.CausationID = unlinkedStatus.MessageID
	unlinkedFeedback.OccurredAt = "2026-07-29T03:33:00Z"
	unlinkedFeedback.Data.OptimizationFeedback.FeedbackID = "feedback-after-unlinked-status"
	unlinkedFeedback.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:31:00Z"
	unlinkedFeedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:32:00Z"
	unlinkedFeedback.Data.OptimizationFeedback.CreatedAt = unlinkedFeedback.OccurredAt
	unlinkedFlow, err := service.ReceiveOptimizationFeedback(context.Background(), unlinkedFeedback)
	if err != nil {
		t.Fatalf("receive observation after unlinked status: %v", err)
	}
	if unlinkedFlow.ScalingDecision != nil || len(unlinkedFlow.ManifestRevisions) != 2 ||
		!hasGuardCheck(operationScalingGuard(t, unlinkedFlow), "deployment_request_ack", false) {
		t.Fatalf("new status without revision acknowledgement changed scaling state: %#v", unlinkedFlow)
	}

	ack := validDeploymentStatusEnvelope()
	ack.MessageID = "msg-deployment-status-revision-2"
	ack.CausationID = first.ManifestRevisions[1].DeploymentRequest.MessageID
	ack.OccurredAt = "2026-07-29T03:34:00Z"
	ack.Data.DeploymentStatus.UpdatedAt = ack.OccurredAt
	if _, err := service.ReceiveDeploymentStatus(context.Background(), ack); err != nil {
		t.Fatalf("receive revision status: %v", err)
	}

	stale := repeated
	stale.MessageID = "msg-feedback-before-ack"
	stale.CausationID = ack.MessageID
	stale.OccurredAt = "2026-07-29T03:33:30Z"
	stale.Data.OptimizationFeedback.FeedbackID = "feedback-before-ack"
	stale.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:33:00Z"
	stale.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:33:30Z"
	stale.Data.OptimizationFeedback.CreatedAt = stale.OccurredAt
	staleFlow, err := service.ReceiveOptimizationFeedback(context.Background(), stale)
	if err != nil {
		t.Fatalf("receive pre-ack observation: %v", err)
	}
	if staleFlow.ScalingDecision != nil || len(staleFlow.ManifestRevisions) != 2 ||
		!hasGuardCheck(operationScalingGuard(t, staleFlow), "observation_after_status", false) {
		t.Fatalf("pre-ack observation changed scaling state: %#v", staleFlow)
	}

	fresh := firstFeedback
	fresh.MessageID = "msg-feedback-after-revision-2"
	fresh.CausationID = ack.MessageID
	fresh.OccurredAt = "2026-07-29T03:51:00Z"
	fresh.Data.OptimizationFeedback.FeedbackID = "feedback-after-revision-2"
	fresh.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:34:00Z"
	fresh.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:50:00Z"
	fresh.Data.OptimizationFeedback.CreatedAt = fresh.OccurredAt
	service.now = func() time.Time { return time.Date(2026, 7, 29, 3, 51, 0, 0, time.UTC) }
	approved, err := service.ReceiveOptimizationFeedback(context.Background(), fresh)
	if err != nil || approved.ScalingDecision == nil || approved.ScalingDecision.Action != ScalingActionScaleOut ||
		approved.DeploymentPlan.InferenceConfiguration.Replicas != 3 || len(approved.ManifestRevisions) != 3 {
		t.Fatalf("fresh observation after revision status did not scale: flow=%#v err=%v", approved, err)
	}
	if first.ScalingDecision.DecisionID == "" || approved.ScalingDecision.DecisionID == "" ||
		first.ScalingDecision.DecisionID == approved.ScalingDecision.DecisionID ||
		approved.ManifestRevisions[2].TriggerDecisionID != approved.ScalingDecision.DecisionID ||
		approved.ManifestRevisions[1].TriggerDecisionID != first.ScalingDecision.DecisionID ||
		approved.OperationAgentExecution.Decision.DecisionID != approved.ScalingDecision.DecisionID {
		t.Fatalf("different feedback messages did not retain distinct scale decision IDs: first=%#v latest=%#v", first.ScalingDecision, approved.ScalingDecision)
	}
}

func TestKeepScalingDecisionDoesNotCreateManifestRevision(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
	result.Decision.Evidence = nil
	runtime := &recordingOperationOptimizationRuntime{result: result}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), validOptimizationFeedbackEnvelope())
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.ScalingDecision == nil || flow.ScalingDecision.Action != ScalingActionKeep {
		t.Fatalf("scaling decision = %#v, want KEEP", flow.ScalingDecision)
	}
	if flow.ScalingDecision.DecisionID == "" || flow.OperationAgentExecution == nil ||
		flow.OperationAgentExecution.Decision.DecisionID != flow.ScalingDecision.DecisionID {
		t.Fatalf("KEEP decision provenance is missing: %#v", flow.ScalingDecision)
	}
	if revisions := manifestRevisionPayloads(t, flow); len(revisions) != 1 {
		t.Fatalf("KEEP manifest revisions = %d, want 1", len(revisions))
	}
	if len(flow.ScalingDecisionHistory) != 1 ||
		flow.ScalingDecisionHistory[0].DecisionID != flow.ScalingDecision.DecisionID ||
		flow.ScalingDecisionHistory[0].OutputManifestRevision != 0 {
		t.Fatalf("KEEP was not recorded without a new revision: %#v", flow.ScalingDecisionHistory)
	}
}

func TestScalingDecisionHistoryRetainsKeepInputsAcrossStatusAndFeedback(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
	result.Decision.Evidence = nil
	runtime := &recordingOperationOptimizationRuntime{result: result}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	initial := createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive status: %v", err)
	}
	firstFeedback := validOptimizationFeedbackEnvelope()
	first, err := service.ReceiveOptimizationFeedback(context.Background(), firstFeedback)
	if err != nil || len(first.ScalingDecisionHistory) != 1 || first.ScalingDecision == nil {
		t.Fatalf("first KEEP history: flow=%#v err=%v", first, err)
	}
	firstRecord := first.ScalingDecisionHistory[0]
	if firstRecord.DecisionID != first.ScalingDecision.DecisionID ||
		firstRecord.Decision.DecisionID != firstRecord.DecisionID ||
		firstRecord.OperationRunID != first.OperationAgentExecution.RunID ||
		firstRecord.Decision.Action != ScalingActionKeep ||
		firstRecord.ProfileID != first.ProfileID ||
		firstRecord.ApplicationContextMessageID != first.ApplicationContext.MessageID ||
		firstRecord.ApplicationContext.MessageID != first.ApplicationContext.MessageID ||
		firstRecord.ResourceRecommendationMessageID != first.ResourceRecommendation.MessageID ||
		firstRecord.ResourceRecommendation.MessageID != first.ResourceRecommendation.MessageID ||
		firstRecord.DeploymentStatus.MessageID != status.MessageID ||
		firstRecord.OptimizationFeedback.MessageID != firstFeedback.MessageID ||
		firstRecord.InputManifestRevision != 1 || firstRecord.OutputManifestRevision != 0 ||
		firstRecord.InputManifestRequestID != first.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.RequestID ||
		firstRecord.InputManifestRequestMessageID != first.ManifestRevisions[0].DeploymentRequest.MessageID ||
		firstRecord.ReplicasMin != first.ApplicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMin ||
		firstRecord.ReplicasMax != first.ApplicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMax ||
		first.Decision.DecisionID != initial.Decision.DecisionID {
		t.Fatalf("first KEEP record lost decision or input provenance: %#v", firstRecord)
	}
	acceptedContextAt, err := time.Parse(time.RFC3339Nano, firstRecord.ApplicationContextAcceptedAt)
	if err != nil {
		t.Fatalf("context local acceptance time: %v", err)
	}
	acceptedRecommendationAt, err := time.Parse(time.RFC3339Nano, firstRecord.ResourceRecommendationAcceptedAt)
	if err != nil {
		t.Fatalf("recommendation local acceptance time: %v", err)
	}
	acceptedStatusAt, err := time.Parse(time.RFC3339Nano, firstRecord.DeploymentStatusAcceptedAt)
	if err != nil {
		t.Fatalf("status local acceptance time: %v", err)
	}
	acceptedFeedbackAt, err := time.Parse(time.RFC3339Nano, firstRecord.OptimizationFeedbackAcceptedAt)
	if err != nil {
		t.Fatalf("feedback local acceptance time: %v", err)
	}
	committedAt, err := time.Parse(time.RFC3339Nano, firstRecord.CommittedAt)
	if err != nil || acceptedRecommendationAt.Before(acceptedContextAt) ||
		acceptedStatusAt.Before(acceptedRecommendationAt) ||
		acceptedFeedbackAt.Before(acceptedStatusAt) || committedAt.Before(acceptedFeedbackAt) {
		t.Fatalf("local acceptance/commit order: context=%s recommendation=%s status=%s feedback=%s commit=%s err=%v", acceptedContextAt, acceptedRecommendationAt, acceptedStatusAt, acceptedFeedbackAt, committedAt, err)
	}

	nextStatus := status
	nextStatus.MessageID = "msg-deployment-status-keep-next"
	nextStatus.OccurredAt = "2026-07-29T03:31:00Z"
	nextStatus.Data.DeploymentStatus.UpdatedAt = nextStatus.OccurredAt
	cleared, err := service.ReceiveDeploymentStatus(context.Background(), nextStatus)
	if err != nil || cleared.ScalingDecision != nil || len(cleared.ScalingDecisionHistory) != 1 {
		t.Fatalf("status change erased KEEP history: flow=%#v err=%v", cleared, err)
	}

	nextFeedback := firstFeedback
	nextFeedback.MessageID = "msg-feedback-keep-next"
	nextFeedback.CausationID = nextStatus.MessageID
	nextFeedback.OccurredAt = "2026-07-29T03:50:00Z"
	nextFeedback.Data.OptimizationFeedback.FeedbackID = "feedback-keep-next"
	nextFeedback.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:31:00Z"
	nextFeedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:49:00Z"
	nextFeedback.Data.OptimizationFeedback.CreatedAt = nextFeedback.OccurredAt
	second, err := service.ReceiveOptimizationFeedback(context.Background(), nextFeedback)
	if err != nil || len(second.ScalingDecisionHistory) != 2 || second.ScalingDecision == nil {
		t.Fatalf("second KEEP history: flow=%#v err=%v", second, err)
	}
	if second.ScalingDecisionHistory[0].DecisionID != firstRecord.DecisionID ||
		second.ScalingDecisionHistory[0].OptimizationFeedback.MessageID != firstFeedback.MessageID ||
		second.ScalingDecisionHistory[1].DecisionID != second.ScalingDecision.DecisionID ||
		second.ScalingDecisionHistory[1].DecisionID == firstRecord.DecisionID ||
		second.ScalingDecisionHistory[1].DeploymentStatus.MessageID != nextStatus.MessageID ||
		second.ScalingDecisionHistory[1].OptimizationFeedback.MessageID != nextFeedback.MessageID {
		t.Fatalf("distinct feedback did not retain distinct input snapshots: %#v", second.ScalingDecisionHistory)
	}
	replayed, err := service.ReceiveOptimizationFeedback(context.Background(), nextFeedback)
	if err != nil || len(replayed.ScalingDecisionHistory) != 2 || runtime.calls != 2 {
		t.Fatalf("feedback replay appended history or reran Agent: flow=%#v err=%v calls=%d", replayed, err, runtime.calls)
	}
}

func TestOptimizationFeedbackRejectsReusedMessageIDAfterHistoryEviction(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
	result.Decision.Evidence = nil
	runtime := &recordingOperationOptimizationRuntime{result: result}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive status: %v", err)
	}
	base := time.Date(2026, 7, 29, 3, 30, 0, 0, time.UTC)
	var firstFeedback, latestFeedback OptimizationFeedbackEnvelope
	for index := 0; index < scalingDecisionHistoryLimit+2; index++ {
		feedback := validOptimizationFeedbackEnvelope()
		feedback.MessageID = fmt.Sprintf("msg-feedback-history-%d", index)
		feedback.Data.OptimizationFeedback.FeedbackID = fmt.Sprintf("feedback-history-%d", index)
		feedback.OccurredAt = base.Add(time.Duration(index) * time.Second).Format(time.RFC3339)
		feedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = feedback.OccurredAt
		feedback.Data.OptimizationFeedback.CreatedAt = feedback.OccurredAt
		if index == 0 {
			firstFeedback = feedback
		}
		latestFeedback = feedback
		if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
			t.Fatalf("receive feedback %d: %v", index, err)
		}
	}
	before, ok := service.GetFlow(firstFeedback.CorrelationID)
	if !ok || len(before.ScalingDecisionHistory) != scalingDecisionHistoryLimit ||
		before.ScalingDecisionHistoryDropped != 2 || runtime.calls != scalingDecisionHistoryLimit+2 {
		t.Fatalf("history eviction or Agent calls = history %d, dropped %d, calls %d", len(before.ScalingDecisionHistory), before.ScalingDecisionHistoryDropped, runtime.calls)
	}
	replayed, err := service.ReceiveOptimizationFeedback(context.Background(), latestFeedback)
	if err != nil || len(replayed.ScalingDecisionHistory) != scalingDecisionHistoryLimit || runtime.calls != scalingDecisionHistoryLimit+2 {
		t.Fatalf("identical current replay changed the Flow: err=%v calls=%d", err, runtime.calls)
	}
	conflict := latestFeedback
	conflict.Data.OptimizationFeedback.Metrics.Resource.CPUAveragePercent = 77
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), conflict); err == nil {
		t.Fatal("accepted conflicting payload for the current feedback message_id")
	}
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), firstFeedback); err == nil {
		t.Fatal("accepted an identical replay of an older feedback message_id")
	}
	old := firstFeedback
	old.OccurredAt = base.Add(time.Duration(scalingDecisionHistoryLimit+3) * time.Second).Format(time.RFC3339)
	old.Data.OptimizationFeedback.FeedbackID = "feedback-reused-after-eviction"
	old.Data.OptimizationFeedback.ObservationWindow.EndedAt = old.OccurredAt
	old.Data.OptimizationFeedback.CreatedAt = old.OccurredAt
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), old); err == nil {
		t.Fatal("accepted an older feedback message_id with a newer observation end")
	}
	after, _ := service.GetFlow(firstFeedback.CorrelationID)
	if runtime.calls != scalingDecisionHistoryLimit+2 ||
		len(after.ScalingDecisionHistory) != len(before.ScalingDecisionHistory) ||
		after.ScalingDecisionHistoryDropped != before.ScalingDecisionHistoryDropped ||
		len(after.ManifestRevisions) != len(before.ManifestRevisions) {
		t.Fatalf("reused message_id changed decision history or revisions: calls=%d flow=%#v", runtime.calls, after)
	}
}

func TestOptimizationFeedbackReplayClonesFlowUnderLock(t *testing.T) {
	service := NewService()
	flow := createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
		t.Fatalf("receive feedback: %v", err)
	}
	service.mu.Lock()
	stored := service.flows[flow.CorrelationID]
	stored.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Placement.RequiredLabels = map[string]string{"lane": "0"}
	service.flows[flow.CorrelationID] = stored
	service.mu.Unlock()

	var group sync.WaitGroup
	errors := make(chan error, 1)
	group.Add(2)
	go func() {
		defer group.Done()
		for index := 0; index < 1000; index++ {
			if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
				select {
				case errors <- err:
				default:
				}
				return
			}
		}
	}()
	go func() {
		defer group.Done()
		for index := 0; index < 1000; index++ {
			service.mu.Lock()
			current := service.flows[flow.CorrelationID]
			current.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Placement.RequiredLabels["lane"] = fmt.Sprintf("%d", index)
			service.mu.Unlock()
		}
	}()
	group.Wait()
	close(errors)
	for err := range errors {
		t.Fatalf("feedback replay failed under concurrent locked Flow mutation: %v", err)
	}
}

func TestOptimizationFeedbackIDLedgerClearsWithFlow(t *testing.T) {
	for _, clearAll := range []bool{false, true} {
		name := "delete"
		if clearAll {
			name = "clear"
		}
		t.Run(name, func(t *testing.T) {
			service := NewService()
			flow := createApprovedFlow(t, service)
			status := validDeploymentStatusEnvelope()
			feedback := validOptimizationFeedbackEnvelope()
			if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
				t.Fatalf("receive first status: %v", err)
			}
			if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
				t.Fatalf("receive first feedback: %v", err)
			}
			if clearAll {
				service.ClearFlows()
			} else if _, ok := service.DeleteFlow(flow.CorrelationID); !ok {
				t.Fatal("delete Flow failed")
			}
			createApprovedFlow(t, service)
			if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
				t.Fatalf("receive status after Flow reset: %v", err)
			}
			if _, err := service.ReceiveOptimizationFeedback(context.Background(), feedback); err != nil {
				t.Fatalf("old feedback ID remained reserved after Flow reset: %v", err)
			}
		})
	}
}

func TestKeepRequiresLatestOptimizedRevisionStatusAcknowledgement(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive initial status: %v", err)
	}
	firstFeedback := validOptimizationFeedbackEnvelope()
	firstFeedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	first, err := service.ReceiveOptimizationFeedback(context.Background(), firstFeedback)
	if err != nil || len(first.ManifestRevisions) != 2 || len(first.ScalingDecisionHistory) != 1 {
		t.Fatalf("first scale decision: flow=%#v err=%v", first, err)
	}
	runtime.result.Decision.Action = ScalingActionKeep
	runtime.result.Decision.CurrentReplicas = 2
	runtime.result.Decision.DesiredReplicas = 2
	runtime.result.Decision.Evidence = nil
	unacknowledged := firstFeedback
	unacknowledged.MessageID = "msg-feedback-keep-before-revision-ack"
	unacknowledged.OccurredAt = "2026-07-29T03:32:00Z"
	unacknowledged.Data.OptimizationFeedback.FeedbackID = "feedback-keep-before-revision-ack"
	unacknowledged.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:30:00Z"
	unacknowledged.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:31:00Z"
	unacknowledged.Data.OptimizationFeedback.CreatedAt = unacknowledged.OccurredAt
	unacknowledged.Data.OptimizationFeedback.SLOViolations = nil
	unacknowledgedFlow, err := service.ReceiveOptimizationFeedback(context.Background(), unacknowledged)
	if err != nil || unacknowledgedFlow.ScalingDecision != nil ||
		len(unacknowledgedFlow.ScalingDecisionHistory) != 1 || len(unacknowledgedFlow.ManifestRevisions) != 2 ||
		!hasGuardCheck(operationScalingGuard(t, unacknowledgedFlow), "deployment_request_ack", false) {
		t.Fatalf("KEEP used a plan before latest revision acknowledgement: flow=%#v err=%v", unacknowledgedFlow, err)
	}
	ack := status
	ack.MessageID = "msg-status-revision-2-for-keep"
	ack.CausationID = first.ManifestRevisions[1].DeploymentRequest.MessageID
	ack.OccurredAt = "2026-07-29T03:34:00Z"
	ack.Data.DeploymentStatus.UpdatedAt = ack.OccurredAt
	if _, err := service.ReceiveDeploymentStatus(context.Background(), ack); err != nil {
		t.Fatalf("receive latest revision status: %v", err)
	}
	fresh := unacknowledged
	fresh.MessageID = "msg-feedback-keep-after-revision-ack"
	fresh.CausationID = ack.MessageID
	fresh.OccurredAt = "2026-07-29T03:50:00Z"
	fresh.Data.OptimizationFeedback.FeedbackID = "feedback-keep-after-revision-ack"
	fresh.Data.OptimizationFeedback.ObservationWindow.StartedAt = ack.OccurredAt
	fresh.Data.OptimizationFeedback.ObservationWindow.EndedAt = "2026-07-29T03:49:00Z"
	fresh.Data.OptimizationFeedback.CreatedAt = fresh.OccurredAt
	approved, err := service.ReceiveOptimizationFeedback(context.Background(), fresh)
	if err != nil || approved.ScalingDecision == nil || approved.ScalingDecision.Action != ScalingActionKeep ||
		len(approved.ScalingDecisionHistory) != 2 || len(approved.ManifestRevisions) != 2 {
		t.Fatalf("KEEP after revision acknowledgement: flow=%#v err=%v", approved, err)
	}
}

func TestScalingDecisionHistoryLinksRevisionAndDeepClonesInputs(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	service.now = func() time.Time { return time.Date(2026, 7, 29, 3, 0, 0, 0, time.UTC) }
	contextMessage := validApplicationContextEnvelope()
	contextMessage.Data.ApplicationProfile.Analysis.Assumptions = []string{"original assumption"}
	if _, err := service.ReceiveApplicationContext(context.Background(), contextMessage); err != nil {
		t.Fatalf("receive context: %v", err)
	}
	recommendationMessage := validResourceRecommendationEnvelope()
	recommendationMessage.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Placement.RequiredLabels = map[string]string{"accelerator": "gpu"}
	if _, err := service.ReceiveResourceRecommendation(context.Background(), recommendationMessage); err != nil {
		t.Fatalf("receive recommendation: %v", err)
	}
	status := validDeploymentStatusEnvelope()
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil || len(flow.ScalingDecisionHistory) != 1 {
		t.Fatalf("scale history: flow=%#v err=%v", flow, err)
	}
	record := flow.ScalingDecisionHistory[0]
	if record.InputManifestRevision != 1 || record.OutputManifestRevision != 2 ||
		record.OutputManifestRequestID != flow.ManifestRevisions[1].DeploymentRequest.Data.DeploymentRequest.RequestID ||
		record.OutputManifestRequestMessageID != flow.ManifestRevisions[1].DeploymentRequest.MessageID ||
		record.InputManifestRequestMessageID != status.CausationID ||
		record.DecisionID != flow.ManifestRevisions[1].TriggerDecisionID ||
		record.Decision.Action != ScalingActionScaleOut ||
		record.OptimizationFeedback.Data.OptimizationFeedback.Metrics.Inference.LatencyP95MS != feedback.Data.OptimizationFeedback.Metrics.Inference.LatencyP95MS {
		t.Fatalf("scale record lost input or output revision: %#v", record)
	}
	if record.ApplicationContext.MessageID != contextMessage.MessageID ||
		record.ResourceRecommendation.MessageID != recommendationMessage.MessageID ||
		record.ApplicationContext.Data.ApplicationProfile.Analysis.Assumptions[0] != "original assumption" ||
		record.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Placement.RequiredLabels["accelerator"] != "gpu" ||
		record.ApplicationContextAcceptedAt == "" || record.ResourceRecommendationAcceptedAt == "" {
		t.Fatalf("scale record lost planning input snapshot or local receipt time: %#v", record)
	}
	flow.ScalingDecisionHistory[0].Decision.Evidence[0] = "mutated"
	flow.ScalingDecisionHistory[0].ApplicationContext.Data.ApplicationProfile.Analysis.Assumptions[0] = "mutated"
	flow.ScalingDecisionHistory[0].ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Placement.RequiredLabels["accelerator"] = "mutated"
	flow.ScalingDecisionHistory[0].ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].ResourceHints[0] = "mutated"
	flow.ScalingDecisionHistory[0].OptimizationFeedback.Data.OptimizationFeedback.SLOViolations[0] = "mutated"
	flow.ScalingDecisionHistory[0].DeploymentStatus.Data.DeploymentStatus.ActualInfrastructure.ResourceIDs[0] = "mutated"
	stored, ok := service.GetFlow(flow.CorrelationID)
	if !ok || stored.ScalingDecisionHistory[0].Decision.Evidence[0] != "latency_p95_ms" ||
		stored.ScalingDecisionHistory[0].ApplicationContext.Data.ApplicationProfile.Analysis.Assumptions[0] != "original assumption" ||
		stored.ScalingDecisionHistory[0].ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Placement.RequiredLabels["accelerator"] != "gpu" ||
		stored.ScalingDecisionHistory[0].ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].ResourceHints[0] != "vm-gpu-01" ||
		stored.ScalingDecisionHistory[0].OptimizationFeedback.Data.OptimizationFeedback.SLOViolations[0] != "latency_p95_ms" ||
		stored.ScalingDecisionHistory[0].DeploymentStatus.Data.DeploymentStatus.ActualInfrastructure.ResourceIDs[0] != "vm-gpu-01" {
		t.Fatalf("returned Flow mutated stored decision history: %#v", stored.ScalingDecisionHistory)
	}
}

func TestScalingDecisionHistoryIsBoundedAndCountsDroppedRecords(t *testing.T) {
	var flow Flow
	for index := 0; index < scalingDecisionHistoryLimit+3; index++ {
		appendScalingDecisionRecord(&flow, ScalingDecisionRecord{DecisionID: fmt.Sprintf("decision-%d", index)})
	}
	if len(flow.ScalingDecisionHistory) != scalingDecisionHistoryLimit ||
		flow.ScalingDecisionHistoryDropped != 3 ||
		flow.ScalingDecisionHistory[0].DecisionID != "decision-3" ||
		flow.ScalingDecisionHistory[len(flow.ScalingDecisionHistory)-1].DecisionID != fmt.Sprintf("decision-%d", scalingDecisionHistoryLimit+2) {
		t.Fatalf("bounded history = %#v, dropped = %d", flow.ScalingDecisionHistory, flow.ScalingDecisionHistoryDropped)
	}
}

func TestKeepWithoutInputRevisionCannotAppendDecisionHistory(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = result.Decision.CurrentReplicas
	result.Decision.Evidence = nil
	service := NewServiceWithRuntimes(nil, nil, nil, &recordingOperationOptimizationRuntime{result: result})
	flow := createApprovedFlow(t, service)
	flow.ManifestRevisions = nil
	service.flows[flow.CorrelationID] = cloneFlow(flow)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive status: %v", err)
	}
	got, err := service.ReceiveOptimizationFeedback(context.Background(), validOptimizationFeedbackEnvelope())
	if err != nil {
		t.Fatalf("receive feedback: %v", err)
	}
	if got.ScalingDecision != nil || len(got.ScalingDecisionHistory) != 0 ||
		!hasGuardCheck(operationScalingGuard(t, got), "input_manifest_revision", false) {
		t.Fatalf("KEEP without input revision was approved: %#v", got)
	}
}

func TestFailedOptimizationFeedbackCannotCreateScalingManifestRevision(t *testing.T) {
	for _, test := range []struct {
		name              string
		priorScaleOut     bool
		wantReplicas      int
		wantRevisionCount int
	}{
		{name: "SLO violation cannot scale out", wantReplicas: 1, wantRevisionCount: 1},
		{name: "low utilization cannot scale in", priorScaleOut: true, wantReplicas: 2, wantRevisionCount: 2},
	} {
		t.Run(test.name, func(t *testing.T) {
			runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
			service := NewServiceWithRuntimes(nil, nil, nil, runtime)
			createApprovedFlow(t, service)
			if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
				t.Fatalf("receive deployment status: %v", err)
			}

			if test.priorScaleOut {
				prior := validOptimizationFeedbackEnvelope()
				prior.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
				flow, err := service.ReceiveOptimizationFeedback(context.Background(), prior)
				if err != nil || flow.ScalingDecision == nil || flow.ScalingDecision.Action != ScalingActionScaleOut {
					t.Fatalf("prepare approved scale out: flow=%#v err=%v", flow.ScalingDecision, err)
				}
				runtime.result.Decision = ScalingDecision{
					Action: ScalingActionScaleIn, Reason: "Scale in proposed from low utilization.",
					CurrentReplicas: 2, DesiredReplicas: 1,
					Evidence:  []string{"cpu_average_percent=18.00", "accelerator_average_percent=22.00"},
					CreatedAt: "2026-08-05T08:00:00Z",
				}
			}

			feedback := validOptimizationFeedbackEnvelope()
			feedback.MessageID = "msg-feedback-failed"
			feedback.Data.OptimizationFeedback.FeedbackID = "feedback-failed"
			feedback.Data.OptimizationFeedback.Outcome = FeedbackOutcomeFailed
			if test.priorScaleOut {
				feedback.OccurredAt = "2026-07-29T03:31:00Z"
				feedback.Data.OptimizationFeedback.ObservationWindow.StartedAt = "2026-07-29T03:30:00Z"
				feedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = feedback.OccurredAt
				feedback.Data.OptimizationFeedback.CreatedAt = feedback.OccurredAt
				feedback.Data.OptimizationFeedback.Metrics.Resource.CPUAveragePercent = 18
				feedback.Data.OptimizationFeedback.Metrics.Resource.AcceleratorAveragePercent = 22
			} else {
				feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
			}
			runtime.result.Decision.DecisionID = "untrusted-runtime-id"
			flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
			if err != nil {
				t.Fatalf("receive failed optimization feedback: %v", err)
			}
			if flow.ScalingDecision != nil {
				t.Fatalf("failed feedback scaling decision = %#v, want none", flow.ScalingDecision)
			}
			if flow.OperationAgentExecution == nil || flow.OperationAgentExecution.Decision.DecisionID != "" {
				t.Fatalf("rejected proposal retained an untrusted decision ID: %#v", flow.OperationAgentExecution)
			}
			guard := operationScalingGuard(t, flow)
			if guard.Status != GuardRejected || !hasGuardCheck(guard, "feedback_outcome", false) {
				t.Fatalf("failed feedback scaling guard = %#v", guard)
			}
			if flow.FeedbackSummary == nil || flow.FeedbackSummary.Success ||
				flow.FeedbackSummary.Outcome != FeedbackOutcomeFailed {
				t.Fatalf("failed feedback summary = %#v", flow.FeedbackSummary)
			}
			if revisions := manifestRevisionPayloads(t, flow); len(revisions) != test.wantRevisionCount {
				t.Fatalf("manifest revisions = %d, want %d", len(revisions), test.wantRevisionCount)
			}
			if flow.DesiredDeploymentSpec == nil ||
				flow.DesiredDeploymentSpec.InferenceConfiguration.Replicas != test.wantReplicas ||
				flow.DeploymentPlan == nil || flow.DeploymentPlan.InferenceConfiguration.Replicas != test.wantReplicas {
				t.Fatalf("failed feedback changed deployment shape: spec=%#v plan=%#v", flow.DesiredDeploymentSpec, flow.DeploymentPlan)
			}
		})
	}
}

func manifestRevisionPayloads(t *testing.T, flow Flow) []any {
	t.Helper()
	encoded, err := json.Marshal(flow)
	if err != nil {
		t.Fatalf("marshal Flow: %v", err)
	}
	var payload map[string]any
	if err := json.Unmarshal(encoded, &payload); err != nil {
		t.Fatalf("unmarshal Flow: %v", err)
	}
	revisions, _ := payload["manifest_revisions"].([]any)
	return revisions
}

func manifestFromRevision(t *testing.T, value any) map[string]any {
	t.Helper()
	revision := manifestRevisionMap(t, value)
	request, ok := revision["deployment_request"].(map[string]any)
	if !ok {
		t.Fatalf("deployment_request = %#v", revision["deployment_request"])
	}
	data := request["data"].(map[string]any)
	deployment := data["deployment_request"].(map[string]any)
	manifest, ok := deployment["deployment_manifest"].(map[string]any)
	if !ok {
		t.Fatalf("deployment_manifest = %#v", deployment["deployment_manifest"])
	}
	return manifest
}

func manifestRevisionMap(t *testing.T, value any) map[string]any {
	t.Helper()
	revision, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("manifest revision = %#v", value)
	}
	return revision
}

func manifestReplicas(t *testing.T, manifest map[string]any) float64 {
	t.Helper()
	inference, ok := manifest["inference_configuration"].(map[string]any)
	if !ok {
		t.Fatalf("inference_configuration = %#v", manifest["inference_configuration"])
	}
	replicas, ok := inference["replicas"].(float64)
	if !ok {
		t.Fatalf("replicas = %#v", inference["replicas"])
	}
	return replicas
}

func TestServiceRequestsResourceRetryForInsufficientCandidate(t *testing.T) {
	service := NewService()
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	recommendation := validResourceRecommendationEnvelope()
	recommendation.Data.ResourceRecommendation.Candidates[0].
		DesiredInfrastructure.Accelerator.MemoryMiBMinPerDevice = 16384

	flow, err := service.ReceiveResourceRecommendation(context.Background(), recommendation)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if flow.State != StateRetryRequired {
		t.Fatalf("state = %q, want %q", flow.State, StateRetryRequired)
	}
	if flow.Decision == nil || flow.Decision.Action != ActionRetry {
		t.Fatalf("decision = %#v, want RETRY", flow.Decision)
	}
	if flow.Decision.CorrectionRequest == nil ||
		flow.Decision.CorrectionRequest.Target != CorrectionTargetResourceRecommendation {
		t.Fatalf("correction request = %#v", flow.Decision.CorrectionRequest)
	}
	if flow.DeploymentPlan != nil {
		t.Fatal("RETRY decision must not create a deployment plan")
	}
	if flow.Guard == nil || flow.Guard.Status != GuardRetryRequired {
		t.Fatalf("guard = %#v, want RETRY_REQUIRED", flow.Guard)
	}
	if flow.DeploymentRequest != nil {
		t.Fatal("RETRY decision must not create deployment.create.request")
	}
}

func TestServiceRejectsInvalidApplicationRequirements(t *testing.T) {
	service := NewService()
	applicationContext := validApplicationContextEnvelope()
	applicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMin = 3
	applicationContext.Data.ApplicationProfile.Requirements.Deployment.ReplicasMax = 1
	if _, err := service.ReceiveApplicationContext(context.Background(), applicationContext); err != nil {
		t.Fatalf("structurally valid application context must be accepted: %v", err)
	}

	flow, err := service.ReceiveResourceRecommendation(
		context.Background(),
		validResourceRecommendationEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	if flow.State != StateDecisionRejected {
		t.Fatalf("state = %q, want %q", flow.State, StateDecisionRejected)
	}
	if flow.Decision == nil || flow.Decision.Action != ActionReject {
		t.Fatalf("decision = %#v, want REJECT", flow.Decision)
	}
	if flow.Decision.CorrectionRequest == nil ||
		flow.Decision.CorrectionRequest.Target != CorrectionTargetApplicationProfile {
		t.Fatalf("correction request = %#v", flow.Decision.CorrectionRequest)
	}
	if flow.DeploymentPlan != nil {
		t.Fatal("REJECT decision must not create a deployment plan")
	}
	if flow.Guard == nil || flow.Guard.Status != GuardRejected {
		t.Fatalf("guard = %#v, want REJECTED", flow.Guard)
	}
	if flow.DeploymentRequest != nil {
		t.Fatal("REJECT decision must not create deployment.create.request")
	}
}

func TestServiceRejectsMismatchedProfileID(t *testing.T) {
	service := NewService()
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	recommendation := validResourceRecommendationEnvelope()
	recommendation.Data.ResourceRecommendation.ProfileID = "profile-other"

	if _, err := service.ReceiveResourceRecommendation(context.Background(), recommendation); err == nil {
		t.Fatal("mismatched profile_id must be rejected")
	}
}

func TestServiceSummarizesSuccessfulDeploymentFeedback(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.Decision.Action = ScalingActionKeep
	result.Decision.DesiredReplicas = 1
	result.Decision.Evidence = nil
	service := NewServiceWithRuntimes(
		nil,
		nil,
		nil,
		&recordingOperationOptimizationRuntime{result: result},
	)
	createApprovedFlow(t, service)

	flow, err := service.ReceiveDeploymentStatus(
		context.Background(),
		validDeploymentStatusEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	if flow.DeploymentStatus == nil || flow.DeploymentStatus.Data.DeploymentStatus.State != DeploymentStateRunning {
		t.Fatalf("deployment status = %#v, want RUNNING", flow.DeploymentStatus)
	}

	flow, err = service.ReceiveOptimizationFeedback(
		context.Background(),
		validOptimizationFeedbackEnvelope(),
	)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.FeedbackSummary == nil {
		t.Fatal("feedback summary was not created")
	}
	if !flow.FeedbackSummary.Success {
		t.Fatalf("success = false, summary = %#v", flow.FeedbackSummary)
	}
	if flow.FeedbackSummary.DeploymentID != "deployment-001" ||
		flow.FeedbackSummary.DecisionID != "decision-flow-001" {
		t.Fatalf("feedback IDs were not preserved: %#v", flow.FeedbackSummary)
	}
	if !strings.Contains(flow.FeedbackSummary.Cause, "SLO") {
		t.Fatalf("cause = %q, want an SLO result", flow.FeedbackSummary.Cause)
	}
	if flow.ScalingDecision == nil {
		t.Fatal("scaling decision was not created")
	}
	if flow.ScalingDecision.Action != ScalingActionKeep {
		t.Fatalf("scaling action = %q, want %q", flow.ScalingDecision.Action, ScalingActionKeep)
	}
}

func TestNewFailedStatusSummaryDoesNotReuseEarlierSuccessfulFeedback(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatal(err)
	}
	succeeded := validOptimizationFeedbackEnvelope()
	withFeedback, err := service.ReceiveOptimizationFeedback(context.Background(), succeeded)
	if err != nil || withFeedback.FeedbackSummary == nil || !withFeedback.FeedbackSummary.Success {
		t.Fatalf("prepare successful feedback: %+v %v", withFeedback.FeedbackSummary, err)
	}
	failed := validDeploymentStatusEnvelope()
	failed.MessageID = "msg-deployment-status-failed-after-feedback"
	failed.OccurredAt = "2026-07-29T03:31:00Z"
	failed.Data.DeploymentStatus.UpdatedAt = failed.OccurredAt
	failed.Data.DeploymentStatus.State = DeploymentStateFailed
	failed.Data.DeploymentStatus.Message = "runtime crashed"
	flow, err := service.ReceiveDeploymentStatus(context.Background(), failed)
	if err != nil {
		t.Fatalf("receive newer failed status: %v", err)
	}
	summary := flow.FeedbackSummary
	if summary == nil || summary.DeploymentState != DeploymentStateFailed || summary.Success ||
		summary.Outcome != "" || summary.Metrics != nil || len(summary.SLOViolations) != 0 ||
		summary.UpdatedAt != failed.Data.DeploymentStatus.UpdatedAt ||
		!strings.Contains(summary.Cause, failed.Data.DeploymentStatus.Message) {
		t.Fatalf("new failed status reused old successful feedback: %+v", summary)
	}
	if flow.OptimizationFeedback == nil || flow.OptimizationFeedback.MessageID != succeeded.MessageID {
		t.Fatalf("older feedback audit record was discarded: %+v", flow.OptimizationFeedback)
	}
}

func TestRunningDeploymentWithFailedFeedbackReportsFeedbackFailure(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatal(err)
	}
	failed := validOptimizationFeedbackEnvelope()
	failed.Data.OptimizationFeedback.Outcome = FeedbackOutcomeFailed
	flow, err := service.ReceiveOptimizationFeedback(context.Background(), failed)
	if err != nil {
		t.Fatalf("receive failed feedback: %v", err)
	}
	summary := flow.FeedbackSummary
	if summary == nil || summary.DeploymentState != DeploymentStateRunning || summary.Success ||
		summary.Outcome != FeedbackOutcomeFailed ||
		!strings.Contains(summary.Cause, "Optimization feedback reported outcome FAILED") ||
		strings.Contains(summary.Cause, "Deployment failed") {
		t.Fatalf("failed feedback was confused with failed deployment status: %+v", summary)
	}
}

func TestServicePersistsScaleOutDecisionForSLOViolation(t *testing.T) {
	service := NewServiceWithRuntimes(
		nil,
		nil,
		nil,
		&recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()},
	)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(
		context.Background(),
		validDeploymentStatusEnvelope(),
	); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}

	flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.ScalingDecision == nil {
		t.Fatal("scaling decision was not created")
	}
	if flow.ScalingDecision.Action != ScalingActionScaleOut {
		t.Fatalf("scaling action = %q, want %q", flow.ScalingDecision.Action, ScalingActionScaleOut)
	}
	if flow.ScalingDecision.CurrentReplicas != 1 || flow.ScalingDecision.DesiredReplicas != 2 {
		t.Fatalf("scaling replicas = %#v, want 1 -> 2", flow.ScalingDecision)
	}
	stored, ok := service.GetFlow("flow-001")
	if !ok || stored.ScalingDecision == nil ||
		stored.ScalingDecision.Action != ScalingActionScaleOut {
		t.Fatalf("stored Flow scaling decision = %#v, ok=%v", stored.ScalingDecision, ok)
	}
}

type recordingOperationOptimizationRuntime struct {
	result    OperationOptimizationResult
	err       error
	calls     int
	request   OperationOptimizationRequest
	echoRunID bool
}

type blockingOperationOptimizationRuntime struct {
	result  OperationOptimizationResult
	started chan struct{}
	release chan struct{}
}

func (runtime *blockingOperationOptimizationRuntime) Optimize(
	ctx context.Context,
	_ OperationOptimizationRequest,
) (OperationOptimizationResult, error) {
	select {
	case runtime.started <- struct{}{}:
	default:
	}
	select {
	case <-runtime.release:
		return runtime.result, nil
	case <-ctx.Done():
		return OperationOptimizationResult{Status: "failed", Message: ctx.Err().Error()}, ctx.Err()
	}
}

func (runtime *recordingOperationOptimizationRuntime) Optimize(
	_ context.Context,
	request OperationOptimizationRequest,
) (OperationOptimizationResult, error) {
	runtime.calls++
	runtime.request = request
	if runtime.echoRunID {
		runtime.result.RunID = request.RunID
	}
	return runtime.result, runtime.err
}

func TestOptimizationFeedbackRunsOperationAgentAfterTrustedInputsJoin(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)

	statusFlow, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope())
	if err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	if statusFlow.ScalingDecision != nil {
		t.Fatalf("status alone must not create a scaling decision: %#v", statusFlow.ScalingDecision)
	}

	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	flow, err := service.ReceiveOptimizationFeedbackForAgent(
		context.Background(), feedback, "RuntimeOperationAgent",
	)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if runtime.calls != 1 || runtime.request.RequestedAgent != "RuntimeOperationAgent" {
		t.Fatalf("operation runtime = %#v calls=%d", runtime.request, runtime.calls)
	}
	if flow.RequestedOperationAgent != "RuntimeOperationAgent" || flow.OperationAgentExecution == nil {
		t.Fatalf("operation Agent evidence = %#v", flow)
	}
	if flow.ScalingDecision == nil || flow.ScalingDecision.Action != ScalingActionScaleOut {
		t.Fatalf("scaling decision = %#v", flow.ScalingDecision)
	}
}

func TestProtocolFlowUsesStableOperationExecutionRunIDAcrossReplay(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{
		result: approvedOperationOptimizationResult(), echoRunID: true,
	}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	created := createApprovedFlow(t, service)
	if created.AutomationRunID != "" {
		t.Fatalf("protocol-created Flow automation_run_id = %q, want empty", created.AutomationRunID)
	}
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}

	first, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if runtime.calls != 1 || strings.TrimSpace(runtime.request.RunID) == "" ||
		!strings.HasPrefix(runtime.request.RunID, "run-operation-") {
		t.Fatalf("operation Runtime request = %#v calls=%d", runtime.request, runtime.calls)
	}
	if first.OperationAgentExecution == nil ||
		first.OperationAgentExecution.RunID != runtime.request.RunID {
		t.Fatalf("operation execution evidence = %#v", first.OperationAgentExecution)
	}

	replayed, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("replay optimization feedback: %v", err)
	}
	if runtime.calls != 1 {
		t.Fatalf("operation Runtime replay calls = %d, want 1", runtime.calls)
	}
	if replayed.OperationAgentExecution == nil ||
		replayed.OperationAgentExecution.RunID != first.OperationAgentExecution.RunID {
		t.Fatalf("replay operation execution = %#v, first = %#v", replayed.OperationAgentExecution, first.OperationAgentExecution)
	}
}

func TestOptimizationFeedbackDoesNotPersistRejectedOperationDecision(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.ResultGuard = GuardResult{Status: GuardRejected, Checks: []GuardCheck{{Name: "agent_result_guard", Passed: false}}}
	runtime := &recordingOperationOptimizationRuntime{result: result}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}

	flow, err := service.ReceiveOptimizationFeedback(context.Background(), validOptimizationFeedbackEnvelope())
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.OperationAgentExecution == nil || flow.OperationAgentExecution.ResultGuard.Status != GuardRejected {
		t.Fatalf("operation Agent execution = %#v", flow.OperationAgentExecution)
	}
	if flow.ScalingDecision != nil {
		t.Fatalf("rejected operation result must not create a scaling decision: %#v", flow.ScalingDecision)
	}
	if revisions := manifestRevisionPayloads(t, flow); len(revisions) != 1 {
		t.Fatalf("rejected operation result manifest revisions = %d, want 1", len(revisions))
	}
	if len(flow.ScalingDecisionHistory) != 0 {
		t.Fatalf("rejected operation result appended decision history: %#v", flow.ScalingDecisionHistory)
	}
}

func TestOptimizationFeedbackDoesNotPersistInvalidRuntimeDecisionContract(t *testing.T) {
	tests := []struct {
		name        string
		mutate      func(*ScalingDecision)
		failedCheck string
	}{
		{
			name: "missing reason",
			mutate: func(decision *ScalingDecision) {
				decision.Reason = ""
			},
			failedCheck: "decision_reason",
		},
		{
			name: "missing created at",
			mutate: func(decision *ScalingDecision) {
				decision.CreatedAt = ""
			},
			failedCheck: "decision_created_at",
		},
		{
			name: "invalid created at",
			mutate: func(decision *ScalingDecision) {
				decision.CreatedAt = "not-rfc3339"
			},
			failedCheck: "decision_created_at",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := approvedOperationOptimizationResult()
			test.mutate(&result.Decision)
			runtime := &recordingOperationOptimizationRuntime{result: result}
			service := NewServiceWithRuntimes(nil, nil, nil, runtime)
			createApprovedFlow(t, service)
			if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
				t.Fatalf("receive deployment status: %v", err)
			}

			feedback := validOptimizationFeedbackEnvelope()
			feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
			flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
			if err != nil {
				t.Fatalf("receive optimization feedback: %v", err)
			}
			if flow.ScalingDecision != nil {
				t.Fatalf("invalid Runtime decision must not be persisted: %#v", flow.ScalingDecision)
			}
			scalingGuard := operationScalingGuard(t, flow)
			if scalingGuard.Status != GuardRejected || !hasGuardCheck(scalingGuard, test.failedCheck, false) {
				t.Fatalf("scaling Guard evidence = %#v", scalingGuard)
			}
		})
	}
}

func TestOptimizationFeedbackRejectsFeedbackBeforeDeploymentStatus(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)

	if _, err := service.ReceiveOptimizationFeedback(context.Background(), validOptimizationFeedbackEnvelope()); err == nil {
		t.Fatal("optimization feedback before deployment status must be rejected")
	}
	if runtime.calls != 0 {
		t.Fatalf("operation runtime calls=%d, want 0", runtime.calls)
	}
}

func TestOptimizationFeedbackRecordsRuntimeFailureWithoutDecision(t *testing.T) {
	sensitiveError := "runtime endpoint returned Authorization: Bearer super-secret-token"
	runtime := &recordingOperationOptimizationRuntime{
		result: OperationOptimizationResult{
			AgentName:    "RuntimeOperationAgent",
			Source:       "runtime",
			Status:       "failed",
			RequestGuard: approvedDecisionGuard("request approved"),
			Message:      "untrusted runtime diagnostic",
		},
		err: errors.New(sensitiveError),
	}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}

	flow, err := service.ReceiveOptimizationFeedback(context.Background(), validOptimizationFeedbackEnvelope())
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.OperationAgentExecution == nil || flow.OperationAgentExecution.Status != "failed" {
		t.Fatalf("runtime failure evidence = %#v", flow.OperationAgentExecution)
	}
	if flow.OperationAgentExecution.Message != "Operation Agent execution failed." {
		t.Fatalf("runtime failure message = %q", flow.OperationAgentExecution.Message)
	}
	encoded, err := json.Marshal(flow)
	if err != nil {
		t.Fatalf("marshal failed runtime Flow: %v", err)
	}
	if strings.Contains(string(encoded), sensitiveError) || strings.Contains(string(encoded), "untrusted runtime diagnostic") {
		t.Fatalf("serialized Flow leaked runtime diagnostic: %s", encoded)
	}
	if flow.ScalingDecision != nil {
		t.Fatalf("failed runtime must not create a scaling decision: %#v", flow.ScalingDecision)
	}
}

func TestOptimizationFeedbackRejectsStaleDeploymentStatusAfterRuntimeReturns(t *testing.T) {
	runtime := &blockingOperationOptimizationRuntime{
		result:  approvedOperationOptimizationResult(),
		started: make(chan struct{}, 1),
		release: make(chan struct{}),
	}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}

	type feedbackResult struct {
		flow Flow
		err  error
	}
	completed := make(chan feedbackResult, 1)
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	go func() {
		flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
		completed <- feedbackResult{flow: flow, err: err}
	}()
	<-runtime.started

	staleStatus := validDeploymentStatusEnvelope()
	staleStatus.MessageID = "msg-deployment-status-failed"
	staleStatus.OccurredAt = "2026-07-29T03:11:00Z"
	staleStatus.Data.DeploymentStatus.UpdatedAt = staleStatus.OccurredAt
	staleStatus.Data.DeploymentStatus.State = DeploymentStateFailed
	staleStatus.Data.DeploymentStatus.Message = "deployment stopped while Agent was running"
	if _, err := service.ReceiveDeploymentStatus(context.Background(), staleStatus); err != nil {
		t.Fatalf("receive newer deployment status: %v", err)
	}
	close(runtime.release)

	result := <-completed
	if result.err != nil {
		t.Fatalf("receive optimization feedback: %v", result.err)
	}
	if result.flow.DeploymentStatus == nil ||
		result.flow.DeploymentStatus.Data.DeploymentStatus.State != DeploymentStateFailed {
		t.Fatalf("current deployment status = %#v", result.flow.DeploymentStatus)
	}
	if result.flow.ScalingDecision != nil {
		t.Fatalf("stale deployment status must not keep a scaling decision: %#v", result.flow.ScalingDecision)
	}
	if len(result.flow.ScalingDecisionHistory) != 0 {
		t.Fatalf("stale deployment status appended decision history: %#v", result.flow.ScalingDecisionHistory)
	}
	scalingGuard := operationScalingGuard(t, result.flow)
	if scalingGuard.Status != GuardRejected || !hasGuardCheck(scalingGuard, "trusted_deployment_status", false) {
		t.Fatalf("stale deployment evidence = %#v", scalingGuard)
	}
}

func TestOptimizationFeedbackRejectsStalePlanningInputsAfterRuntimeReturns(t *testing.T) {
	runtime := &blockingOperationOptimizationRuntime{
		result:  approvedOperationOptimizationResult(),
		started: make(chan struct{}, 1),
		release: make(chan struct{}),
	}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}

	type feedbackResult struct {
		flow Flow
		err  error
	}
	completed := make(chan feedbackResult, 1)
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	go func() {
		flow, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
		completed <- feedbackResult{flow: flow, err: err}
	}()
	<-runtime.started

	recommendation := validResourceRecommendationEnvelope()
	recommendation.MessageID = "msg-resource-replanned"
	if _, err := service.ReceiveResourceRecommendation(context.Background(), recommendation); err != nil {
		t.Fatalf("receive newer resource recommendation: %v", err)
	}
	close(runtime.release)

	result := <-completed
	if result.err != nil {
		t.Fatalf("receive optimization feedback: %v", result.err)
	}
	if result.flow.ScalingDecision != nil {
		t.Fatalf("stale planning inputs must not keep a scaling decision: %#v", result.flow.ScalingDecision)
	}
	if len(result.flow.ScalingDecisionHistory) != 0 {
		t.Fatalf("stale planning inputs appended decision history: %#v", result.flow.ScalingDecisionHistory)
	}
	scalingGuard := operationScalingGuard(t, result.flow)
	if scalingGuard.Status != GuardRejected || !hasGuardCheck(scalingGuard, "trusted_planning_inputs", false) {
		t.Fatalf("stale planning evidence = %#v", scalingGuard)
	}
	if revisions := manifestRevisionPayloads(t, result.flow); len(revisions) != 2 {
		t.Fatalf("stale operation result changed manifest revisions = %d, want 2", len(revisions))
	}
}

func TestDeploymentStatusReplayPreservesOptimizationEvidenceAndDecision(t *testing.T) {
	runtime := &recordingOperationOptimizationRuntime{result: approvedOperationOptimizationResult()}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	feedback := validOptimizationFeedbackEnvelope()
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	before, err := service.ReceiveOptimizationFeedback(context.Background(), feedback)
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}

	after, err := service.ReceiveDeploymentStatus(context.Background(), status)
	if err != nil {
		t.Fatalf("replay deployment status: %v", err)
	}
	if !reflect.DeepEqual(after.OptimizationFeedback, before.OptimizationFeedback) ||
		!reflect.DeepEqual(after.OperationAgentExecution, before.OperationAgentExecution) ||
		!reflect.DeepEqual(after.ScalingDecision, before.ScalingDecision) {
		t.Fatalf("status replay changed optimization result: before=%#v after=%#v", before, after)
	}
}

func TestOptimizationFeedbackStoresRejectedScalingGuardEvidence(t *testing.T) {
	result := approvedOperationOptimizationResult()
	result.Decision.Evidence = []string{"invented_slo_violation"}
	runtime := &recordingOperationOptimizationRuntime{result: result}
	service := NewServiceWithRuntimes(nil, nil, nil, runtime)
	createApprovedFlow(t, service)
	if _, err := service.ReceiveDeploymentStatus(context.Background(), validDeploymentStatusEnvelope()); err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}

	flow, err := service.ReceiveOptimizationFeedback(context.Background(), validOptimizationFeedbackEnvelope())
	if err != nil {
		t.Fatalf("receive optimization feedback: %v", err)
	}
	if flow.ScalingDecision != nil {
		t.Fatalf("rejected scaling Guard must not create a decision: %#v", flow.ScalingDecision)
	}
	scalingGuard := operationScalingGuard(t, flow)
	if scalingGuard.Status != GuardRejected || !hasGuardCheck(scalingGuard, "slo_evidence", false) {
		t.Fatalf("scaling Guard evidence = %#v", scalingGuard)
	}
}

func TestFlowCloneDeepCopiesOperationAgentExecution(t *testing.T) {
	flow := Flow{OperationAgentExecution: &OperationOptimizationResult{
		RequestGuard: GuardResult{
			Checks: []GuardCheck{{Name: "request", Passed: true}},
			Issues: []ValidationIssue{{Field: "request", Reason: "original"}},
		},
		ResultGuard: GuardResult{
			Checks: []GuardCheck{{Name: "result", Passed: true}},
			Issues: []ValidationIssue{{Field: "result", Reason: "original"}},
		},
		Decision: ScalingDecision{Evidence: []string{"latency_p95_ms"}},
	}}

	copy := cloneFlow(flow)
	copy.OperationAgentExecution.RequestGuard.Checks[0].Name = "changed"
	copy.OperationAgentExecution.RequestGuard.Issues[0].Reason = "changed"
	copy.OperationAgentExecution.ResultGuard.Checks[0].Name = "changed"
	copy.OperationAgentExecution.ResultGuard.Issues[0].Reason = "changed"
	copy.OperationAgentExecution.Decision.Evidence[0] = "changed"

	if flow.OperationAgentExecution.RequestGuard.Checks[0].Name != "request" ||
		flow.OperationAgentExecution.RequestGuard.Issues[0].Reason != "original" ||
		flow.OperationAgentExecution.ResultGuard.Checks[0].Name != "result" ||
		flow.OperationAgentExecution.ResultGuard.Issues[0].Reason != "original" ||
		flow.OperationAgentExecution.Decision.Evidence[0] != "latency_p95_ms" {
		t.Fatalf("original operation evidence changed: %#v", flow.OperationAgentExecution)
	}
}

func TestFlowCloneDeepCopiesOperationScalingGuard(t *testing.T) {
	var execution OperationOptimizationResult
	if err := json.Unmarshal([]byte(`{"scaling_guard":{"status":"REJECTED","checks":[{"name":"slo_evidence","passed":false,"reason":"original"}]}}`), &execution); err != nil {
		t.Fatalf("unmarshal operation evidence: %v", err)
	}
	flow := Flow{OperationAgentExecution: &execution}
	copy := cloneFlow(flow)

	copyGuard := reflect.ValueOf(copy.OperationAgentExecution).Elem().FieldByName("ScalingGuard")
	if !copyGuard.IsValid() {
		t.Fatal("cloned operation evidence is missing scaling_guard")
	}
	copyGuard.FieldByName("Checks").Index(0).FieldByName("Name").SetString("changed")
	originalGuard := reflect.ValueOf(flow.OperationAgentExecution).Elem().FieldByName("ScalingGuard")
	if originalGuard.FieldByName("Checks").Index(0).FieldByName("Name").String() != "slo_evidence" {
		t.Fatalf("original scaling Guard changed: %#v", flow.OperationAgentExecution)
	}
}

func operationScalingGuard(t *testing.T, flow Flow) GuardResult {
	t.Helper()
	if flow.OperationAgentExecution == nil {
		t.Fatal("operation Agent execution is missing")
	}
	encoded, err := json.Marshal(flow.OperationAgentExecution)
	if err != nil {
		t.Fatalf("marshal operation Agent execution: %v", err)
	}
	var evidence struct {
		ScalingGuard GuardResult `json:"scaling_guard"`
	}
	if err := json.Unmarshal(encoded, &evidence); err != nil {
		t.Fatalf("unmarshal operation Agent execution: %v", err)
	}
	return evidence.ScalingGuard
}

func hasGuardCheck(guard GuardResult, name string, passed bool) bool {
	for _, check := range guard.Checks {
		if check.Name == name && check.Passed == passed {
			return true
		}
	}
	return false
}

func approvedOperationOptimizationResult() OperationOptimizationResult {
	return OperationOptimizationResult{
		RunID:        "run-operation-fixture",
		AgentName:    "RuntimeOperationAgent",
		Source:       "runtime",
		Status:       "completed",
		RequestGuard: approvedDecisionGuard("request approved"),
		ResultGuard:  approvedDecisionGuard("result approved"),
		Decision: ScalingDecision{
			Action:          ScalingActionScaleOut,
			Reason:          "Trusted SLO evidence requires one bounded replica increase.",
			CurrentReplicas: 1,
			DesiredReplicas: 2,
			Evidence:        []string{"latency_p95_ms"},
			CreatedAt:       "2026-08-05T08:00:00Z",
		},
	}
}

func TestServiceSummarizesFailedDeploymentStatus(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	status.Data.DeploymentStatus.State = DeploymentStateFailed
	status.Data.DeploymentStatus.Message = "runtime adapter could not start the application"

	flow, err := service.ReceiveDeploymentStatus(context.Background(), status)
	if err != nil {
		t.Fatalf("receive deployment status: %v", err)
	}
	if flow.FeedbackSummary == nil {
		t.Fatal("failed deployment must create a feedback summary")
	}
	if flow.FeedbackSummary.Success {
		t.Fatalf("success = true, summary = %#v", flow.FeedbackSummary)
	}
	if !strings.Contains(flow.FeedbackSummary.Cause, "runtime adapter") {
		t.Fatalf("cause = %q, want deployment failure message", flow.FeedbackSummary.Cause)
	}
}

func TestServiceRejectsFeedbackForAnotherDecision(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)
	status := validDeploymentStatusEnvelope()
	status.Data.DeploymentStatus.DecisionID = "decision-other"

	if _, err := service.ReceiveDeploymentStatus(context.Background(), status); err == nil {
		t.Fatal("feedback for another decision must be rejected")
	}
}

func TestServiceDeletesGeneratedFlows(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	deleted, ok := service.DeleteFlow("  flow-001  ")
	if !ok || deleted.CorrelationID != "flow-001" {
		t.Fatalf("deleted = %#v ok=%v", deleted, ok)
	}
	if _, ok := service.GetFlow("flow-001"); ok {
		t.Fatal("deleted Flow remains available")
	}
	if count := service.ClearFlows(); count != 0 {
		t.Fatalf("cleared = %d, want 0", count)
	}
}

func TestServiceClearsAllGeneratedFlows(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	secondContext := validApplicationContextEnvelope()
	secondContext.CorrelationID = "flow-002"
	secondContext.TraceID = "trace-002"
	secondContext.Data.ApplicationProfile.ProfileID = "profile-002"
	if _, err := service.ReceiveApplicationContext(context.Background(), secondContext); err != nil {
		t.Fatalf("receive second application context: %v", err)
	}

	if count := service.ClearFlows(); count != 2 {
		t.Fatalf("cleared = %d, want 2", count)
	}
	if flows := service.ListFlows(); len(flows) != 0 {
		t.Fatalf("flows after clear = %#v", flows)
	}
}

func TestServiceComparesSimpleAndGuardedReasoning(t *testing.T) {
	reasoner := stubReasoner{
		result: ModelReasoningResult{
			ExecutionStatus: "executed",
			CandidateID:     "qwen3.5-ops-planner",
			Provider:        "test-provider",
			ActualModel:     "qwen3.5:4b",
			LatencyMS:       120,
			Proposal: ReasoningProposal{
				Action:              ActionDeploy,
				SelectedCandidateID: "candidate-001",
				Reason:              "The recommended candidate satisfies the application requirements.",
				Confidence:          0.88,
			},
		},
	}
	service := NewServiceWithReasoner(reasoner)
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(
		context.Background(),
		"flow-001",
		"qwen3.5-ops-planner",
	)
	if err != nil {
		t.Fatalf("compare reasoning: %v", err)
	}
	if comparison.RuleBased.Action != ActionDeploy {
		t.Fatalf("rule-based action = %q", comparison.RuleBased.Action)
	}
	if comparison.SimpleInference.GuardStatus != GuardNotApplied {
		t.Fatalf("simple inference guard = %q", comparison.SimpleInference.GuardStatus)
	}
	if comparison.ValidatedInference.GuardStatus != GuardApproved {
		t.Fatalf("validated inference guard = %q", comparison.ValidatedInference.GuardStatus)
	}
	if !comparison.Agreement.ActionMatch || !comparison.Agreement.CandidateMatch {
		t.Fatalf("agreement = %#v", comparison.Agreement)
	}
	flow, ok := service.GetFlow("flow-001")
	if !ok || flow.ReasoningComparison == nil {
		t.Fatal("comparison was not recorded in the Agent Control flow")
	}
}

func TestServiceGuardRejectsUnsafeReasoningProposal(t *testing.T) {
	service := NewServiceWithReasoner(stubReasoner{
		result: ModelReasoningResult{
			ExecutionStatus: "executed",
			CandidateID:     "qwen3.5-ops-planner",
			ActualModel:     "qwen3.5:4b",
			Proposal: ReasoningProposal{
				Action:              ActionDeploy,
				SelectedCandidateID: "candidate-invented",
				Reason:              "Use an unregistered resource.",
				Confidence:          0.9,
			},
		},
	})
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(context.Background(), "flow-001", "qwen3.5-ops-planner")
	if err != nil {
		t.Fatalf("compare reasoning: %v", err)
	}
	if comparison.SimpleInference.Action != ActionDeploy {
		t.Fatalf("simple inference must preserve the raw proposal: %#v", comparison.SimpleInference)
	}
	if comparison.ValidatedInference.GuardStatus != GuardRejected {
		t.Fatalf("unsafe proposal guard = %q, want REJECTED", comparison.ValidatedInference.GuardStatus)
	}
	if comparison.ValidatedInference.Action != ActionDeploy {
		t.Fatalf("validated fallback action = %q, want safe baseline DEPLOY", comparison.ValidatedInference.Action)
	}
}

func TestServiceRecordsUnavailableReasoningProvider(t *testing.T) {
	service := NewServiceWithReasoner(stubReasoner{err: errors.New("connect: connection refused")})
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(context.Background(), "flow-001", "qwen3.5-ops-planner")
	if err != nil {
		t.Fatalf("provider failure must return a comparison report: %v", err)
	}
	if comparison.SimpleInference.ExecutionStatus != ReasoningProviderUnavailable {
		t.Fatalf("execution status = %q", comparison.SimpleInference.ExecutionStatus)
	}
	if comparison.SimpleInference.Error != "provider_unavailable" {
		t.Fatalf("provider error code=%q, want provider_unavailable", comparison.SimpleInference.Error)
	}
	if strings.Contains(comparison.SimpleInference.Error, "connection refused") {
		t.Fatalf("provider internals leaked into the comparison: %#v", comparison.SimpleInference)
	}
}

func TestServiceDistinguishesRejectedModelOutputFromUnavailableProvider(t *testing.T) {
	service := NewServiceWithReasoner(stubReasoner{
		result: ModelReasoningResult{
			ExecutionStatus: "rejected",
			CandidateID:     "qwen3.5-ops-planner",
			Provider:        "local-openai-compatible",
			ActualModel:     "qwen3.5:4b",
			LatencyMS:       45,
		},
		err: errors.New("parse reasoning proposal: unknown field"),
	})
	createApprovedFlow(t, service)

	comparison, err := service.CompareReasoning(context.Background(), "flow-001", "qwen3.5-ops-planner")
	if err != nil {
		t.Fatalf("rejected output must return a comparison report: %v", err)
	}
	if comparison.SimpleInference.ExecutionStatus != "rejected" {
		t.Fatalf("execution status = %q, want rejected", comparison.SimpleInference.ExecutionStatus)
	}
}

type stubReasoner struct {
	result ModelReasoningResult
	err    error
}

func (reasoner stubReasoner) Propose(
	context.Context,
	string,
	ReasoningInput,
) (ModelReasoningResult, error) {
	return reasoner.result, reasoner.err
}

func createApprovedFlow(t *testing.T, service *Service) Flow {
	t.Helper()
	service.now = func() time.Time { return time.Date(2026, 7, 29, 3, 0, 0, 0, time.UTC) }
	if _, err := service.ReceiveApplicationContext(context.Background(), validApplicationContextEnvelope()); err != nil {
		t.Fatalf("receive application context: %v", err)
	}
	flow, err := service.ReceiveResourceRecommendation(context.Background(), validResourceRecommendationEnvelope())
	if err != nil {
		t.Fatalf("receive resource recommendation: %v", err)
	}
	return flow
}

func validDeploymentStatusEnvelope() DeploymentStatusEnvelope {
	return DeploymentStatusEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-deployment-status-001",
			MessageType:     MessageDeploymentStatusChanged,
			OccurredAt:      "2026-07-29T03:10:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			CausationID:     "msg-deploy-request-flow-001",
			Source:          Endpoint{System: "deployment-orchestrator", Component: "runtime-adapter"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: DeploymentStatusData{
			DeploymentStatus: DeploymentStatus{
				DeploymentID: "deployment-001",
				DecisionID:   "decision-flow-001",
				State:        DeploymentStateRunning,
				ActualInfrastructure: ActualInfrastructure{
					Provider:    "MOCK",
					Region:      "kr-central-1",
					ResourceIDs: []string{"vm-gpu-01"},
				},
				Message:   "The application is running.",
				UpdatedAt: "2026-07-29T03:10:00Z",
			},
		},
	}
}

func validOptimizationFeedbackEnvelope() OptimizationFeedbackEnvelope {
	return OptimizationFeedbackEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       "msg-feedback-001",
			MessageType:     MessageOptimizationFeedbackCreated,
			OccurredAt:      "2026-07-29T03:30:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			CausationID:     "msg-deployment-status-001",
			Source:          Endpoint{System: "deployment-orchestrator", Component: "monitoring"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: OptimizationFeedbackData{
			OptimizationFeedback: OptimizationFeedback{
				FeedbackID:   "feedback-001",
				DecisionID:   "decision-flow-001",
				DeploymentID: "deployment-001",
				Outcome:      FeedbackOutcomeSucceeded,
				ObservationWindow: ObservationWindow{
					StartedAt: "2026-07-29T03:10:00Z",
					EndedAt:   "2026-07-29T03:30:00Z",
				},
				Metrics: OptimizationMetrics{
					Resource: ResourceMetrics{
						CPUAveragePercent:         48.2,
						MemoryPeakMiB:             26800,
						AcceleratorAveragePercent: 72.5,
						AcceleratorMemoryPeakMiB:  21800,
					},
					Inference: InferenceMetrics{
						LatencyP95MS:     1480,
						ThroughputRPS:    6.2,
						ErrorRatePercent: 0.2,
					},
					Cost: CostMetrics{
						Currency:      "KRW",
						EstimatedCost: 833.33,
					},
				},
				SLOViolations: []string{},
				CreatedAt:     "2026-07-29T03:30:00Z",
			},
		},
	}
}

func validApplicationContextEnvelope() ApplicationContextEnvelope {
	return ApplicationContextEnvelope{
		Envelope: Envelope{
			ContractVersion: "1.0",
			MessageID:       "msg-context-001",
			MessageType:     MessageApplicationContextCreated,
			OccurredAt:      "2026-07-29T03:00:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			Source:          Endpoint{System: "khu-ai-app", Component: "application-profile-generator"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: ApplicationContextData{
			ApplicationProfile: ApplicationProfile{
				ProfileID:  "profile-001",
				AppID:      "chat-service",
				AppVersion: "1.0.0",
				Requirements: ApplicationRequirements{
					Compute: ComputeRequirements{
						CPUCoresMin:   8,
						MemoryMiBMin:  32768,
						StorageGiBMin: 100,
					},
					Accelerator: AcceleratorRequirements{
						Required:              true,
						Type:                  "GPU",
						CountMin:              1,
						MemoryMiBMinPerDevice: 24576,
					},
					Deployment: DeploymentRequirements{
						ReplicasMin: 1,
						ReplicasMax: 2,
						Isolation:   "ONE_MAJOR_APP_PER_VM",
					},
					SLO: SLORequirements{
						LatencyP95MSMax:  2000,
						ThroughputRPSMin: 5,
					},
				},
			},
			ModelRecommendation: ModelRecommendation{
				SelectedModel: SelectedModel{
					ModelID:      "qwen2.5-7b-instruct",
					ModelVersion: "1",
					Source:       "HUGGING_FACE",
				},
				InferenceConfiguration: InferenceConfiguration{
					RuntimeEngine:      "VLLM",
					Precision:          "FP16",
					MaxBatchSize:       8,
					MaxConcurrency:     20,
					TensorParallelSize: 1,
					Replicas:           1,
				},
			},
		},
	}
}

func validResourceRecommendationEnvelope() ResourceRecommendationEnvelope {
	return ResourceRecommendationEnvelope{
		Envelope: Envelope{
			ContractVersion: "1.0",
			MessageID:       "msg-resource-001",
			MessageType:     MessageResourceRecommendationCreated,
			OccurredAt:      "2026-07-29T03:01:00Z",
			CorrelationID:   "flow-001",
			TraceID:         "trace-001",
			CausationID:     "msg-context-001",
			Source:          Endpoint{System: "khu-resource-service", Component: "resource-recommender"},
			Target:          Endpoint{System: "khu-agent-control", Component: "automation-agent"},
		},
		Data: ResourceRecommendationData{
			ResourceRecommendation: ResourceRecommendation{
				RecommendationID:    "resource-rec-001",
				ProfileID:           "profile-001",
				SnapshotID:          "snapshot-001",
				Status:              "FOUND",
				SelectedCandidateID: "candidate-001",
				Candidates: []ResourceCandidate{
					{
						CandidateID: "candidate-001",
						Rank:        1,
						Feasible:    true,
						DesiredInfrastructure: DesiredInfrastructure{
							NodeCount:         1,
							CPUCoresPerNode:   8,
							MemoryMiBPerNode:  32768,
							StorageGiBPerNode: 100,
							Accelerator: AcceleratorAllocation{
								Type:                  "GPU",
								Count:                 1,
								MemoryMiBMinPerDevice: 24576,
							},
							Isolation: "ONE_MAJOR_APP_PER_VM",
						},
						ResourceHints: []string{"vm-gpu-01"},
						Scores: ResourceScores{
							ResourceFit:    0.96,
							SLOHeadroom:    0.84,
							CostEfficiency: 0.83,
							Availability:   0.98,
							Total:          0.90,
						},
					},
				},
			},
		},
	}
}
