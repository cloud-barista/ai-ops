package agentcontrol

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
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
	if spec.DesiredInfrastructure != manifest.DesiredInfrastructure {
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
	stored, ok := service.GetFlow(flow.CorrelationID)
	if !ok || len(manifestRevisionPayloads(t, stored)) != 2 {
		t.Fatalf("stored Flow did not preserve both Manifest revisions: %#v", stored)
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
	if revisions := manifestRevisionPayloads(t, flow); len(revisions) != 1 {
		t.Fatalf("KEEP manifest revisions = %d, want 1", len(revisions))
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
	if !strings.Contains(comparison.SimpleInference.Error, "connection refused") {
		t.Fatalf("provider error was not recorded: %#v", comparison.SimpleInference)
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
