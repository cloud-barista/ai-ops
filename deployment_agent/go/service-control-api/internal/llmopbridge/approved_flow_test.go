package llmopbridge

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

func TestGuardFirstApprovedFlowContinuesToOneProposal(t *testing.T) {
	input := validApprovedInitialFlowInput()
	now := time.Date(2026, time.August, 5, 5, 0, 0, 0, time.UTC)
	normalizer := llmop.NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	candidate := llmclient.Candidate{
		CandidateID: input.Request.CandidateID,
		Provider:    llmop.OfflineFixtureProvider,
		ActualModel: llmop.OfflineFixtureActualModel,
		Enabled:     true,
		JSONMode:    true,
	}
	policy := plannerguard.Policy{
		Version:           "llm-op-policy-v1",
		MaxRequestLength:  8000,
		AllowedRequesters: []string{input.Request.RequestedBy},
	}
	pipeline := llmop.NewOfflineFixturePlanner(
		`{"decision":"allow_request","reason_code":"BOUNDED_REQUEST_ALLOWED","reason":"The bounded request may continue.","confidence":0.99}`,
		`{"action":"create_deployment_manifest","reason_code":"RESOURCE_PLAN_READY","reason":"The exact resource contract is satisfied.","confidence":0.99,"accelerator":"none","resources":{"cpu":"4","memory":"8Gi","gpu":"0","storage":"100Gi"},"assumptions":[]}`,
		normalizer,
	)

	stage, err := pipeline.ReviewRequest(
		context.Background(),
		candidate,
		policy,
		input.Request,
	)
	if err != nil {
		t.Fatalf("run first safeguard: %v", err)
	}
	input.Safeguard = stage
	projection, err := ProjectApprovedInitialFlow(input, validTrustedResolver)
	if err != nil {
		t.Fatalf("join approved geon Flow: %v", err)
	}
	result, err := pipeline.PrepareApproved(
		context.Background(),
		candidate,
		policy,
		projection.Request,
		projection.Safeguard,
	)
	if err != nil {
		t.Fatalf("resume with one bounded Proposal: %v", err)
	}
	if result.Status != llmop.StatusHandoffReady || result.Handoff.PreparedRequest == nil {
		t.Fatalf("expected a prepare-only AppDeploy handoff, got status=%s", result.Status)
	}
	if result.Handoff.SubmissionMode != "not_submitted" {
		t.Fatalf("approved Flow must remain not_submitted, got %q", result.Handoff.SubmissionMode)
	}
}

func TestProjectApprovedInitialFlowBindsApprovedRevision(t *testing.T) {
	input := validApprovedInitialFlowInput()
	resolvedAppID := ""
	resolvedAppVersion := ""
	resolver := func(appID string, appVersion string) (TrustedAppVersionBinding, error) {
		resolvedAppID = appID
		resolvedAppVersion = appVersion
		return TrustedAppVersionBinding{
			AppID:        appID,
			AppVersion:   appVersion,
			AppVersionID: "appver-registered-001",
		}, nil
	}

	projection, err := ProjectApprovedInitialFlow(input, resolver)
	if err != nil {
		t.Fatalf("project approved initial Flow: %v", err)
	}
	if resolvedAppID != "app-common-001" || resolvedAppVersion != "1.0.0" {
		t.Fatalf("resolver received the wrong identity: %q %q", resolvedAppID, resolvedAppVersion)
	}
	if projection.Request.Application.AppVersionID != "appver-registered-001" {
		t.Fatalf("trusted app version was not bound: %#v", projection.Request.Application)
	}
	withoutEnrichment := projection.Request
	withoutEnrichment.Application.PlanningConstraints = nil
	if !reflect.DeepEqual(withoutEnrichment, input.Request) {
		t.Fatalf("approved bridge changed a request field other than PlanningConstraints: %#v", withoutEnrichment)
	}
	if projection.Request.CandidateID != "qwen-bound-by-caller" ||
		projection.Evidence.LLMCandidateID != "qwen-bound-by-caller" ||
		projection.Evidence.SelectedResourceCandidateID != "resource-candidate-001" {
		t.Fatalf("LLM and resource candidate namespaces were not preserved: %#v", projection.Evidence)
	}
	if projection.Evidence.DecisionID != "decision-bridge-001" ||
		projection.Evidence.Revision != 1 ||
		projection.Evidence.Phase != agentcontrol.ManifestPhaseInitial ||
		projection.Evidence.TriggerAction != agentcontrol.ActionDeploy ||
		projection.Evidence.DeploymentRequestID != "deploy-request-bridge-001" ||
		projection.Evidence.ManifestID != "manifest-bridge-001" {
		t.Fatalf("approved decision/revision evidence was not captured: %#v", projection.Evidence)
	}
	if len(projection.Evidence.ManifestFingerprint) != len("sha256:")+64 ||
		!strings.HasPrefix(projection.Evidence.ManifestFingerprint, "sha256:") {
		t.Fatalf("unexpected Manifest fingerprint: %q", projection.Evidence.ManifestFingerprint)
	}
	if projection.Evidence.SafeguardContinuation.RequestBinding != strings.Repeat("a", 64) ||
		projection.Evidence.SafeguardContinuation.CandidateID != "qwen-bound-by-caller" {
		t.Fatalf("pre-geon safeguard binding was not preserved: %#v", projection.Evidence.SafeguardContinuation)
	}
	if !reflect.DeepEqual(projection.Safeguard, input.Safeguard) {
		t.Fatal("approved safeguard stage was not carried atomically with the projected request")
	}

	again, err := ProjectApprovedInitialFlow(validApprovedInitialFlowInput(), resolver)
	if err != nil {
		t.Fatalf("project the same approved Flow again: %v", err)
	}
	if again.Evidence.ManifestFingerprint != projection.Evidence.ManifestFingerprint {
		t.Fatal("the same approved decision and revision must have a stable fingerprint")
	}
}

func TestProjectApprovedInitialFlowFailsClosedOnLifecycleDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*ApprovedInitialFlowInput)
	}{
		{
			name: "decision state is not approved",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.State = agentcontrol.StateReady
			},
		},
		{
			name: "decision action is not deploy",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.Decision.Action = agentcontrol.ActionRetry
			},
		},
		{
			name: "decision confidence is invalid",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.Decision.Confidence = 2
			},
		},
		{
			name: "Flow Guard is rejected",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.Guard.Status = agentcontrol.GuardRejected
			},
		},
		{
			name: "approved Guard contains a failed check",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.Guard.Checks[0].Passed = false
			},
		},
		{
			name: "Agent authorization is absent",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.AgentAuthorization = nil
			},
		},
		{
			name: "unrecognized legacy Agent lacks execution evidence",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.AgentAuthorization.AgentName = "UnrecognizedDeploymentAgent"
			},
		},
		{
			name: "Decision Agent execution guard drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.AgentExecution = &agentcontrol.DecisionAgentResult{
					AgentName:    input.Flow.AgentAuthorization.AgentName,
					Source:       "runtime",
					Status:       "completed",
					RequestGuard: approvedAgentGuardForBridgeTest(),
					ResultGuard:  agentcontrol.GuardResult{Status: agentcontrol.GuardRejected},
					Decision:     *input.Flow.Decision,
				}
			},
		},
		{
			name: "Decision Agent semantic evidence drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				decision := *input.Flow.Decision
				decision.DecisionID = ""
				decision.Reason = "different execution reason"
				input.Flow.AgentExecution = &agentcontrol.DecisionAgentResult{
					AgentName:    input.Flow.AgentAuthorization.AgentName,
					Source:       "runtime",
					Status:       "completed",
					RequestGuard: approvedAgentGuardForBridgeTest(),
					ResultGuard:  approvedAgentGuardForBridgeTest(),
					Decision:     decision,
				}
			},
		},
		{
			name: "Flow trace chain drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.TraceID = "trace-other-001"
			},
		},
		{
			name: "selected candidate decision drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.Decision.SelectedCandidateID = "resource-other-001"
			},
		},
		{
			name: "DeploymentPlan application drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.DeploymentPlan.AppVersion = "2.0.0"
			},
		},
		{
			name: "DeploymentPlan infrastructure drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.DeploymentPlan.DesiredInfrastructure.CPUCoresPerNode++
			},
		},
		{
			name: "DeploymentPlan resource hints drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.DeploymentPlan.ResourceHints = []string{"unapproved-hint"}
			},
		},
		{
			name: "DeploymentPlan inference drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.DeploymentPlan.InferenceConfiguration.RuntimeEngine = "unapproved-runtime-engine"
			},
		},
		{
			name: "Manifest revision missing",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ManifestRevisions = nil
			},
		},
		{
			name: "Manifest revision greater than one",
			mutate: func(input *ApprovedInitialFlowInput) {
				second := input.Flow.ManifestRevisions[0]
				second.Revision = 2
				input.Flow.ManifestRevisions = append(input.Flow.ManifestRevisions, second)
			},
		},
		{
			name: "Manifest revision is optimized",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ManifestRevisions[0].Phase = agentcontrol.ManifestPhaseOptimized
			},
		},
		{
			name: "active request does not match latest revision",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.DeploymentRequest.Data.DeploymentRequest.RequestID = "deploy-request-stale-001"
			},
		},
		{
			name: "optimization evidence is present",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.OptimizationFeedback = &agentcontrol.OptimizationFeedbackEnvelope{}
			},
		},
		{
			name: "Manifest decision identity drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				manifest := &input.Flow.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.DeploymentManifest
				manifest.DecisionID = "decision-other-001"
				*input.Flow.DeploymentRequest = input.Flow.ManifestRevisions[0].DeploymentRequest
			},
		},
		{
			name: "Manifest application identity drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				manifest := &input.Flow.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.DeploymentManifest
				manifest.Application.AppVersion = "2.0.0"
				*input.Flow.DeploymentRequest = input.Flow.ManifestRevisions[0].DeploymentRequest
			},
		},
		{
			name: "Manifest target runtime drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				manifest := &input.Flow.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.DeploymentManifest
				manifest.TargetRuntime = "OTHER_RUNTIME"
				*input.Flow.DeploymentRequest = input.Flow.ManifestRevisions[0].DeploymentRequest
			},
		},
		{
			name: "deployment request artifact drift",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.Application.Artifact = &agentcontrol.Artifact{
					URI: "https://example.invalid/unapproved-artifact",
				}
				*input.Flow.DeploymentRequest = input.Flow.ManifestRevisions[0].DeploymentRequest
			},
		},
		{
			name: "LLM and resource candidate bindings collide",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Request.CandidateID = "resource-candidate-001"
				input.Safeguard.Review.CandidateID = "resource-candidate-001"
				input.Safeguard.Continuation.CandidateID = "resource-candidate-001"
			},
		},
		{
			name: "analysis user request differs from safeguarded request",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.AnalysisRequest.Data.Application.UserRequest = "different unsafeguarded deployment request"
			},
		},
		{
			name: "pre-geon trace is absent before analysis",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Request.TraceID = ""
				input.Safeguard.TraceID = ""
				input.Safeguard.Continuation.TraceID = ""
			},
		},
		{
			name: "pre-geon request already has planning constraints",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Request.Application.PlanningConstraints = &llmop.PlanningConstraints{}
			},
		},
		{
			name: "safeguard approval is false",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Safeguard.Approved = false
			},
		},
		{
			name: "safeguard decision is not allow request",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Safeguard.Decision.Action = llmop.ActionRequestClarification
			},
		},
		{
			name: "safeguard continuation is absent",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Safeguard.Continuation = nil
			},
		},
		{
			name: "safeguard continuation candidate drifts",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Safeguard.Continuation.CandidateID = "qwen-other-candidate"
			},
		},
		{
			name: "safeguard binding is not sha256 hex",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Safeguard.Continuation.RequestBinding = strings.Repeat("z", 64)
			},
		},
		{
			name: "safeguard submission mode permits submission",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Safeguard.Continuation.SubmissionMode = "submit"
			},
		},
		{
			name: "ApplicationProfile has missing fields",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ApplicationContext.Data.ApplicationProfile.Analysis.MissingFields = []string{"compute.cpu_cores_min"}
			},
		},
		{
			name: "ApplicationProfile used a default",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ApplicationContext.Data.ApplicationProfile.Analysis.Warnings = []string{"CPU was DEFAULTED to 2 cores"}
			},
		},
		{
			name: "ApplicationProfile inferred resources",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ApplicationContext.Data.ApplicationProfile.Analysis.Assumptions = []string{"Assumed storage capacity from workload type"}
			},
		},
		{
			name: "ApplicationProfile uses unstructured estimate assumption",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ApplicationContext.Data.ApplicationProfile.Analysis.Assumptions = []string{"CPU was estimated from the workload"}
			},
		},
		{
			name: "ApplicationProfile has an unclassified warning",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ApplicationContext.Data.ApplicationProfile.Analysis.Warnings = []string{"resource provenance requires review"}
			},
		},
		{
			name: "ApplicationProfile artifact differs from analysis",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Flow.ApplicationContext.Data.ApplicationProfile.Artifact.URI = "artifact://tampered"
			},
		},
		{
			name: "safeguarded app version differs from trusted registry",
			mutate: func(input *ApprovedInitialFlowInput) {
				input.Request.Application.AppVersionID = "appver-caller-001"
			},
		},
	}
	for _, item := range tests {
		t.Run(item.name, func(t *testing.T) {
			input := validApprovedInitialFlowInput()
			item.mutate(&input)
			if _, err := ProjectApprovedInitialFlow(input, validTrustedResolver); err == nil {
				t.Fatal("expected approved initial Flow projection to fail closed")
			}
		})
	}
}

func TestProjectApprovedInitialFlowRequiresTrustedExactAppBinding(t *testing.T) {
	input := validApprovedInitialFlowInput()
	if _, err := ProjectApprovedInitialFlow(input, nil); err == nil {
		t.Fatal("a trusted AppDeploy app version resolver is required")
	}
	resolverError := func(string, string) (TrustedAppVersionBinding, error) {
		return TrustedAppVersionBinding{}, fmt.Errorf("registry unavailable")
	}
	if _, err := ProjectApprovedInitialFlow(input, resolverError); err == nil {
		t.Fatal("registry resolution errors must fail closed")
	}
	mismatched := func(string, string) (TrustedAppVersionBinding, error) {
		return TrustedAppVersionBinding{
			AppID:        "app-other-001",
			AppVersion:   "1.0.0",
			AppVersionID: "appver-registered-001",
		}, nil
	}
	if _, err := ProjectApprovedInitialFlow(input, mismatched); err == nil {
		t.Fatal("a mismatched trusted app binding must fail closed")
	}
}

func TestProjectApprovedInitialFlowAcceptsAuthorizedRuntimeDecisionAgent(t *testing.T) {
	input := validApprovedInitialFlowInput()
	const runtimeAgent = "RuntimeDeploymentAgent"
	input.Flow.RequestedDecisionAgent = runtimeAgent
	input.Flow.AgentAuthorization.AgentName = runtimeAgent
	executionDecision := *input.Flow.Decision
	executionDecision.DecisionID = ""
	executionDecision.ReasoningMode = ""
	input.Flow.AgentExecution = &agentcontrol.DecisionAgentResult{
		AgentName:    runtimeAgent,
		Source:       "internal_runtime",
		Status:       "completed",
		RequestGuard: approvedAgentGuardForBridgeTest(),
		ResultGuard:  approvedAgentGuardForBridgeTest(),
		Decision:     executionDecision,
	}

	if _, err := ProjectApprovedInitialFlow(input, validTrustedResolver); err != nil {
		t.Fatalf("authorized Runtime Deployment Agent must remain supported: %v", err)
	}
	input.Flow.AgentExecution.AgentName = "RuntimeDeploymentAgentTampered"
	if _, err := ProjectApprovedInitialFlow(input, validTrustedResolver); err == nil {
		t.Fatal("runtime Agent execution name tampering must fail closed")
	}
}

func approvedAgentGuardForBridgeTest() agentcontrol.GuardResult {
	return agentcontrol.GuardResult{
		Status: agentcontrol.GuardApproved,
		Checks: []agentcontrol.GuardCheck{
			{Name: "agent_contract", Passed: true},
		},
	}
}

func validTrustedResolver(appID string, appVersion string) (TrustedAppVersionBinding, error) {
	return TrustedAppVersionBinding{
		AppID:        appID,
		AppVersion:   appVersion,
		AppVersionID: "appver-registered-001",
	}, nil
}

func validApprovedInitialFlowInput() ApprovedInitialFlowInput {
	bridgeInput := validBridgeInput()
	bridgeInput.Request.APIVersion = llmop.APIVersion
	bridgeInput.Request.CorrelationID = bridgeInput.AnalysisRequest.CorrelationID
	bridgeInput.Request.TraceID = bridgeInput.AnalysisRequest.TraceID
	bridgeInput.Request.Application.UserRequest = bridgeInput.AnalysisRequest.Data.Application.UserRequest
	bridgeInput.Request.Policy = llmop.RequestPolicy{Mode: llmop.ModePrepareOnly}
	artifact := bridgeInput.AnalysisRequest.Data.Application.Artifact
	artifact.Entrypoint = append([]string(nil), artifact.Entrypoint...)
	bridgeInput.ApplicationContext.Data.ApplicationProfile.Artifact = &artifact
	profile := bridgeInput.ApplicationContext.Data.ApplicationProfile
	recommendation := bridgeInput.ResourceRecommendation.Data.ResourceRecommendation
	selected := recommendation.Candidates[0]
	decision := agentcontrol.AutomationDecision{
		DecisionID:          "decision-bridge-001",
		Action:              agentcontrol.ActionDeploy,
		Reason:              "approved by the bounded automation decision",
		ReasoningMode:       agentcontrol.ReasoningModeRuleBased,
		Confidence:          1,
		SelectedCandidateID: recommendation.SelectedCandidateID,
		CreatedAt:           "2026-08-05T05:00:00Z",
	}
	plan := agentcontrol.DeploymentPlan{
		PlanID:                 "plan-bridge-001",
		ProfileID:              profile.ProfileID,
		AppID:                  profile.AppID,
		AppVersion:             profile.AppVersion,
		SelectedCandidateID:    recommendation.SelectedCandidateID,
		TargetRuntime:          "VM",
		DesiredInfrastructure:  selected.DesiredInfrastructure,
		InferenceConfiguration: bridgeInput.ApplicationContext.Data.ModelRecommendation.InferenceConfiguration,
		ResourceHints:          append([]string(nil), selected.ResourceHints...),
		ReasoningMode:          agentcontrol.ReasoningModeRuleBased,
		CreatedAt:              "2026-08-05T05:00:00Z",
	}
	runtime := agentcontrol.RuntimeConfiguration{RestartPolicy: "ON_FAILURE"}
	runtime.Command = append([]string(nil), profile.Artifact.Entrypoint...)
	deploymentArtifact := *profile.Artifact
	deploymentArtifact.Entrypoint = append(
		[]string(nil),
		profile.Artifact.Entrypoint...,
	)
	spec := agentcontrol.DesiredDeploymentSpec{
		SpecVersion:            agentcontrol.ContractVersionV1,
		DecisionID:             decision.DecisionID,
		Application:            agentcontrol.ManifestApplication{AppID: profile.AppID, AppVersion: profile.AppVersion},
		TargetRuntime:          plan.TargetRuntime,
		DesiredInfrastructure:  plan.DesiredInfrastructure,
		InferenceConfiguration: plan.InferenceConfiguration,
		Runtime:                runtime,
		PolicyHints:            append([]string(nil), plan.ResourceHints...),
		Metadata:               agentcontrol.ManifestMetadata{ProfileID: profile.ProfileID},
	}
	request := agentcontrol.DeploymentCreateRequestEnvelope{
		Envelope: bridgeEnvelope(
			"msg-deploy-request-bridge-001",
			agentcontrol.MessageDeploymentCreateRequest,
			bridgeInput.ResourceRecommendation.MessageID,
		),
		Data: agentcontrol.DeploymentCreateRequestData{
			DeploymentRequest: agentcontrol.DeploymentRequest{
				RequestID:  "deploy-request-bridge-001",
				DecisionID: decision.DecisionID,
				Application: agentcontrol.DeploymentApplication{
					AppID:      profile.AppID,
					AppVersion: profile.AppVersion,
					Artifact:   &deploymentArtifact,
				},
				DeploymentManifest: agentcontrol.DeploymentManifest{
					ManifestID:             "manifest-bridge-001",
					ManifestVersion:        spec.SpecVersion,
					DecisionID:             spec.DecisionID,
					Application:            spec.Application,
					TargetRuntime:          spec.TargetRuntime,
					DesiredInfrastructure:  spec.DesiredInfrastructure,
					InferenceConfiguration: spec.InferenceConfiguration,
					Runtime:                spec.Runtime,
					ResourceHints:          append([]string(nil), spec.PolicyHints...),
					Metadata:               spec.Metadata,
				},
			},
		},
	}
	revision := agentcontrol.ManifestRevision{
		Revision:              1,
		Phase:                 agentcontrol.ManifestPhaseInitial,
		TriggerAction:         agentcontrol.ActionDeploy,
		CreatedAt:             "2026-08-05T05:00:00Z",
		DesiredDeploymentSpec: spec,
		DeploymentRequest:     request,
	}
	flow := agentcontrol.Flow{
		CorrelationID:          bridgeInput.AnalysisRequest.CorrelationID,
		TraceID:                bridgeInput.AnalysisRequest.TraceID,
		ProfileID:              profile.ProfileID,
		State:                  agentcontrol.StateDecisionApproved,
		ApplicationContext:     &bridgeInput.ApplicationContext,
		ResourceRecommendation: &bridgeInput.ResourceRecommendation,
		AgentAuthorization: &agentcontrol.AgentAuthorization{
			AgentName:  agentcontrol.AutomationAgentName,
			Capability: agentcontrol.AutomationCapability,
			Action:     agentcontrol.AutomationDecisionAction,
			Authorized: true,
			Reason:     "authorized for the bounded deployment decision",
		},
		Decision:       &decision,
		DeploymentPlan: &plan,
		Guard: &agentcontrol.GuardResult{
			Status: agentcontrol.GuardApproved,
			Checks: []agentcontrol.GuardCheck{
				{Name: "application_requirements", Passed: true},
				{Name: "resource_compatibility", Passed: true},
				{Name: "identity_consistency", Passed: true},
			},
		},
		DesiredDeploymentSpec: &spec,
		DeploymentRequest:     &request,
		ManifestRevisions:     []agentcontrol.ManifestRevision{revision},
		UpdatedAt:             "2026-08-05T05:00:00Z",
	}
	return ApprovedInitialFlowInput{
		Request:         bridgeInput.Request,
		Safeguard:       validPreGeonSafeguard(bridgeInput.Request),
		AnalysisRequest: bridgeInput.AnalysisRequest,
		Flow:            flow,
	}
}

func validPreGeonSafeguard(request llmop.Request) llmop.SafeguardStageResult {
	confidence := 0.95
	return llmop.SafeguardStageResult{
		APIVersion:    llmop.APIVersion,
		Stage:         llmop.SafeguardStageName,
		RequestID:     request.RequestID,
		CorrelationID: request.CorrelationID,
		TraceID:       request.TraceID,
		Status:        llmop.StatusSafeguardApproved,
		Approved:      true,
		Decision: llmop.Decision{
			Action:            llmop.SafeguardDecisionAllow,
			ReasonCode:        "RESOURCE_REQUEST_ALLOWED",
			Reason:            "request is inside the bounded planning responsibility",
			Confidence:        &confidence,
			ObservationStatus: "not_provided",
		},
		RequestGuard: plannerguard.Decision{
			Valid:         true,
			Status:        "approved",
			PolicyVersion: "llm-op-policy-v1",
			Reason:        "request satisfies the configured planner boundary",
			Checks: []plannerguard.Check{
				{Name: "request_contract", Passed: true},
			},
		},
		Review: &llmop.SafeguardReviewEvidence{
			Provider:    "offline_fixture",
			CandidateID: request.CandidateID,
			ActualModel: "qwen-offline-fixture",
			Decision:    llmop.SafeguardDecisionAllow,
			ReasonCode:  "RESOURCE_REQUEST_ALLOWED",
			Confidence:  &confidence,
		},
		Input: llmop.InputSummary{ObservationStatus: "not_provided"},
		Continuation: &llmop.ApprovedContinuationEvidence{
			Stage:             llmop.SafeguardContinuationStageName,
			RequestID:         request.RequestID,
			CorrelationID:     request.CorrelationID,
			TraceID:           request.TraceID,
			PolicyVersion:     "llm-op-policy-v1",
			CandidateID:       request.CandidateID,
			ReviewDecision:    llmop.SafeguardDecisionAllow,
			ReviewReasonCode:  "RESOURCE_REQUEST_ALLOWED",
			Confidence:        &confidence,
			ObservationStatus: "not_provided",
			BindingAlgorithm:  llmop.SafeguardBindingAlgorithm,
			RequestBinding:    strings.Repeat("a", 64),
			SubmissionMode:    "not_submitted",
		},
	}
}
