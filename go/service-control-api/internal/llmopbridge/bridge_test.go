package llmopbridge

import (
	"context"
	"encoding/json"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/llmop"
)

func TestProjectRejectsUnsafeProjectedIdentifiers(t *testing.T) {
	input := validBridgeInput()
	input.AnalysisRequest.Envelope.MessageID = "message\u202eunsafe"

	if _, err := Project(input); err == nil {
		t.Fatal("unsafe Common JSON identifiers must not reach Projection evidence")
	}
}

func TestProjectRejectsUnboundedUserRequestBeforeProjection(t *testing.T) {
	input := validBridgeInput()
	input.AnalysisRequest.Data.Application.UserRequest = strings.Repeat(
		"가",
		maxBridgeUserRequestRunes+1,
	)

	if _, err := Project(input); err == nil {
		t.Fatal("unbounded user request must not reach the llmop projection")
	}
}

func TestProjectRejectsWhitespaceOnlyUserRequest(t *testing.T) {
	input := validBridgeInput()
	input.AnalysisRequest.Data.Application.UserRequest = " \t\r\n "

	if _, err := Project(input); err == nil {
		t.Fatal("whitespace-only user request must not reach the llmop projection")
	}
}

func TestProjectRejectsIdentifierOutsideLLMOpContract(t *testing.T) {
	input := validBridgeInput()
	input.Request.RequestID = "a+b"

	if _, err := Project(input); err == nil {
		t.Fatal("bridge must enforce the downstream LLM operation identifier contract")
	}
}

func TestProjectAcceptsLocalAnalyzerAndCatalogRecommendationCPUPath(t *testing.T) {
	input := localAnalyzerCatalogBridgeInput(t)
	projection, err := Project(input)
	if err != nil {
		t.Fatalf("project actual local analyzer/recommender CPU path: %v", err)
	}
	constraints := projection.Request.Application.PlanningConstraints
	if constraints == nil || constraints.CPUCoresMin != 2 ||
		constraints.MemoryMiBMin != 4*1024 || constraints.StorageGiBMin != 20 ||
		constraints.RecommendedResources == nil ||
		constraints.RecommendedResources.CPUCores != 4 ||
		constraints.RecommendedResources.MemoryMiB != 8*1024 ||
		constraints.RecommendedResources.StorageGiB != 100 ||
		projection.Evidence.SelectedResourceCandidateID != "mock-cpu-balanced" {
		t.Fatalf("unexpected analyzer/recommender projection: %#v", constraints)
	}
}

func TestProjectRejectsActualLocalAnalyzerCatalogGPUDeviceMemory(t *testing.T) {
	input := localAnalyzerCatalogBridgeInputForRequest(
		t,
		"CPU 8개, GPU 1개, 메모리 32Gi, 스토리지 200Gi 기준으로 준비해줘.",
	)
	_, err := Project(input)
	if err == nil || !strings.Contains(err.Error(), "device memory") {
		t.Fatalf("expected actual GPU analyzer/catalog path to fail closed, got %v", err)
	}
}

func localAnalyzerCatalogBridgeInput(t *testing.T) Input {
	return localAnalyzerCatalogBridgeInputForRequest(
		t,
		"추론 서비스의 준비 전용 배포 매니페스트를 작성해줘.",
	)
}

func localAnalyzerCatalogBridgeInputForRequest(t *testing.T, userRequest string) Input {
	t.Helper()
	inputRequest := agentcontrol.AutomationRunInput{
		InputType: agentcontrol.InputTypeNaturalLanguage,
		Request:   userRequest,
	}
	analyzer := agentcontrol.LocalRequirementAnalyzer{}
	analysisResult, err := analyzer.Analyze(context.Background(), inputRequest)
	if err != nil {
		t.Fatalf("analyze local natural-language request: %v", err)
	}
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve bridge test source path")
	}
	repositoryRoot := filepath.Clean(filepath.Join(filepath.Dir(currentFile), "../../../.."))
	catalog, err := agentcontrol.LoadResourceCatalog(filepath.Join(
		repositoryRoot,
		"config",
		"mock_resource_catalog.json",
	))
	if err != nil {
		t.Fatalf("load checked-in resource catalog: %v", err)
	}
	recommender := agentcontrol.CatalogResourceRecommender{
		Catalog: catalog,
		Now: func() time.Time {
			return time.Date(2026, time.August, 5, 5, 0, 0, 0, time.UTC)
		},
	}
	recommendationResult, err := recommender.Recommend(
		context.Background(),
		analysisResult.ApplicationProfile,
	)
	if err != nil {
		t.Fatalf("recommend catalog resources: %v", err)
	}

	input := validBridgeInput()
	input.AnalysisRequest.Data.Application.AppID = analysisResult.ApplicationProfile.AppID
	input.AnalysisRequest.Data.Application.AppVersion = analysisResult.ApplicationProfile.AppVersion
	input.AnalysisRequest.Data.Application.UserRequest = inputRequest.Request
	input.ApplicationContext.Data.ApplicationProfile = analysisResult.ApplicationProfile
	input.ResourceRecommendation.Data.ResourceRecommendation = recommendationResult.ResourceRecommendation
	return input
}

func TestProjectPreservesNamespacesAndProjectsPlanningConstraints(t *testing.T) {
	input := validBridgeInput()
	projection, err := Project(input)
	if err != nil {
		t.Fatalf("Project returned an unexpected error: %v", err)
	}
	request := projection.Request
	if request.APIVersion != llmop.APIVersion || request.Policy.Mode != llmop.ModePrepareOnly {
		t.Fatal("bridge did not set the bounded llmop prepare-only contract")
	}
	if request.CorrelationID != input.AnalysisRequest.CorrelationID ||
		request.TraceID != input.AnalysisRequest.TraceID {
		t.Fatal("Common JSON correlation and trace identity were not preserved")
	}
	if request.Application.UserRequest != input.AnalysisRequest.Data.Application.UserRequest {
		t.Fatal("natural-language user_request was not preserved")
	}
	if request.CandidateID != "qwen-bound-by-caller" {
		t.Fatalf("bridge changed the caller-bound completion candidate: %q", request.CandidateID)
	}
	if request.Application.TargetProfileID != "target-hint-001" {
		t.Fatalf("bridge changed the trusted AppDeploy target hint: %q", request.Application.TargetProfileID)
	}
	if request.CandidateID == projection.Evidence.SelectedResourceCandidateID ||
		request.Application.TargetProfileID == projection.Evidence.SourceProfileID ||
		request.Application.TargetProfileID == projection.Evidence.SelectedResourceCandidateID {
		t.Fatal("bridge conflated completion, profile, resource, and AppDeploy target namespaces")
	}
	wantConstraints := &llmop.PlanningConstraints{
		SourceProfileID:        "profile-bridge-001",
		SourceRecommendationID: "resource-rec-bridge-001",
		RecommendationFeasible: true,
		CPUCoresMin:            2,
		MemoryMiBMin:           4 * 1024,
		GPUCountMin:            0,
		StorageGiBMin:          20,
		Accelerator:            "none",
		RecommendedResources: &llmop.RecommendedResources{
			CPUCores:    4,
			MemoryMiB:   8 * 1024,
			GPUCount:    0,
			StorageGiB:  100,
			Accelerator: "none",
		},
	}
	if !reflect.DeepEqual(request.Application.PlanningConstraints, wantConstraints) {
		t.Fatalf("unexpected planning constraints: %#v", request.Application.PlanningConstraints)
	}

	content, err := json.Marshal(request)
	if err != nil {
		t.Fatalf("marshal projected request: %v", err)
	}
	for _, forbidden := range []string{
		"model-must-not-project",
		"runtime-must-not-project",
		"artifact-must-not-project",
		"entrypoint-must-not-project",
		"resource-hint-must-not-project",
	} {
		if strings.Contains(string(content), forbidden) {
			t.Fatalf("out-of-scope Common JSON value reached the projected request: %s", forbidden)
		}
	}
}

func TestProjectIgnoresModelRecommendation(t *testing.T) {
	first := validBridgeInput()
	second := validBridgeInput()
	second.ApplicationContext.Data.ModelRecommendation = agentcontrol.ModelRecommendation{
		RecommendationID: "different-model-rec",
		SelectedModel: agentcontrol.SelectedModel{
			ModelID:      "different-model",
			ModelVersion: "different-version",
			Source:       "different-source",
		},
		InferenceConfiguration: agentcontrol.InferenceConfiguration{
			RuntimeEngine:      "different-runtime",
			Precision:          "different-precision",
			MaxBatchSize:       99,
			MaxConcurrency:     99,
			TensorParallelSize: 99,
			Replicas:           99,
		},
	}

	firstProjection, err := Project(first)
	if err != nil {
		t.Fatalf("project first input: %v", err)
	}
	secondProjection, err := Project(second)
	if err != nil {
		t.Fatalf("project second input: %v", err)
	}
	if !reflect.DeepEqual(firstProjection, secondProjection) {
		t.Fatal("ModelRecommendation affected projection despite being outside LLM_Op scope")
	}
}

func TestProjectSupportsNVIDIAProfileWithoutDeviceMemoryRequirement(t *testing.T) {
	input := validBridgeInput()
	input.AnalysisRequest.Data.Application.UserRequest =
		"CPU 2개, GPU 1개, 메모리 4Gi, 저장소 20Gi 기준으로 준비해줘."
	input.ApplicationContext.Data.ApplicationProfile.Requirements.Accelerator =
		agentcontrol.AcceleratorRequirements{
			Required: true,
			Type:     "NVIDIA",
			CountMin: 1,
		}
	input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Accelerator =
		agentcontrol.AcceleratorAllocation{
			Type:  "NVIDIA",
			Count: 1,
		}

	projection, err := Project(input)
	if err != nil {
		t.Fatalf("project supported NVIDIA profile: %v", err)
	}
	constraints := projection.Request.Application.PlanningConstraints
	if constraints.GPUCountMin != 1 || constraints.Accelerator != "nvidia" ||
		constraints.RecommendedResources == nil ||
		constraints.RecommendedResources.GPUCount != 1 ||
		constraints.RecommendedResources.Accelerator != "nvidia" {
		t.Fatalf("unexpected NVIDIA accelerator projection: %#v", constraints)
	}
}

func TestProjectFailsClosedOnInconsistentOrLossyInput(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*Input)
	}{
		{
			name: "correlation mismatch",
			mutate: func(input *Input) {
				input.ResourceRecommendation.CorrelationID = "corr-other-001"
			},
		},
		{
			name: "causation mismatch",
			mutate: func(input *Input) {
				input.ApplicationContext.CausationID = "msg-other-001"
			},
		},
		{
			name: "application identity mismatch",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.AppID = "app-other"
			},
		},
		{
			name: "profile join mismatch",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.ProfileID = "profile-other-001"
			},
		},
		{
			name: "recommendation not found",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Status = "RETRY_REQUIRED"
			},
		},
		{
			name: "selected candidate missing",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.SelectedCandidateID = "resource-missing-001"
			},
		},
		{
			name: "selected candidate duplicated",
			mutate: func(input *Input) {
				recommendation := &input.ResourceRecommendation.Data.ResourceRecommendation
				recommendation.Candidates = append(recommendation.Candidates, recommendation.Candidates[0])
			},
		},
		{
			name: "selected candidate infeasible",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].Feasible = false
			},
		},
		{
			name: "selected candidate below CPU minimum",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.CPUCoresPerNode = 1
			},
		},
		{
			name: "selected candidate exceeds CPU ceiling",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.CPUCoresPerNode = maxCPUCount + 1
			},
		},
		{
			name: "selected candidate isolation mismatch",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Isolation = "SHARED_VM"
			},
		},
		{
			name: "CPU profile selects GPU candidate",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Accelerator =
					agentcontrol.AcceleratorAllocation{Type: "NVIDIA", Count: 1}
			},
		},
		{
			name: "multi-node candidate",
			mutate: func(input *Input) {
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.NodeCount = 2
			},
		},
		{
			name: "zero storage minimum",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.Requirements.Compute.StorageGiBMin = 0
			},
		},
		{
			name: "GPU device memory is unrepresentable",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.Requirements.Accelerator =
					agentcontrol.AcceleratorRequirements{
						Required:              true,
						Type:                  "NVIDIA",
						CountMin:              1,
						MemoryMiBMinPerDevice: 16 * 1024,
					}
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Accelerator =
					agentcontrol.AcceleratorAllocation{
						Type:                  "NVIDIA",
						Count:                 1,
						MemoryMiBMinPerDevice: 16 * 1024,
					}
			},
		},
		{
			name: "selected GPU candidate device memory is unrepresentable",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.Requirements.Accelerator =
					agentcontrol.AcceleratorRequirements{
						Required: true,
						Type:     "NVIDIA",
						CountMin: 1,
					}
				input.ResourceRecommendation.Data.ResourceRecommendation.Candidates[0].DesiredInfrastructure.Accelerator =
					agentcontrol.AcceleratorAllocation{
						Type:                  "NVIDIA",
						Count:                 1,
						MemoryMiBMinPerDevice: 24 * 1024,
					}
			},
		},
		{
			name: "SLO is unrepresented",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.Requirements.SLO.LatencyP95MSMax = 500
			},
		},
		{
			name: "cost is unrepresented",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.Requirements.Cost.Currency = "KRW"
			},
		},
		{
			name: "optional accelerator constraints",
			mutate: func(input *Input) {
				input.ApplicationContext.Data.ApplicationProfile.Requirements.Accelerator =
					agentcontrol.AcceleratorRequirements{Type: "GPU", CountMin: -1}
			},
		},
		{
			name: "conflicting caller correlation",
			mutate: func(input *Input) {
				input.Request.CorrelationID = "corr-conflict-001"
			},
		},
		{
			name: "caller-supplied planning constraints",
			mutate: func(input *Input) {
				input.Request.Application.PlanningConstraints = &llmop.PlanningConstraints{}
			},
		},
	}
	for _, item := range tests {
		t.Run(item.name, func(t *testing.T) {
			input := validBridgeInput()
			item.mutate(&input)
			if _, err := Project(input); err == nil {
				t.Fatal("expected bridge projection to fail closed")
			}
		})
	}
}

func validBridgeInput() Input {
	analysis := agentcontrol.ApplicationAnalysisRequestEnvelope{
		Envelope: bridgeEnvelope(
			"msg-analysis-bridge-001",
			agentcontrol.MessageApplicationAnalysisRequest,
			"",
		),
		Data: agentcontrol.ApplicationAnalysisRequestData{
			Application: agentcontrol.AnalysisRequestApplication{
				AppID:       "app-common-001",
				AppVersion:  "1.0.0",
				UserRequest: "CPU 2개, GPU 0개, 메모리 4Gi, 저장소 20Gi 기준으로 준비해줘.",
				Artifact: agentcontrol.Artifact{
					Type:       "archive",
					URI:        "artifact-must-not-project",
					Entrypoint: []string{"entrypoint-must-not-project"},
				},
			},
		},
	}
	applicationContext := agentcontrol.ApplicationContextEnvelope{
		Envelope: bridgeEnvelope(
			"msg-context-bridge-001",
			agentcontrol.MessageApplicationContextCreated,
			analysis.MessageID,
		),
		Data: agentcontrol.ApplicationContextData{
			ApplicationProfile: agentcontrol.ApplicationProfile{
				ProfileID:  "profile-bridge-001",
				AppID:      "app-common-001",
				AppVersion: "1.0.0",
				Requirements: agentcontrol.ApplicationRequirements{
					Compute: agentcontrol.ComputeRequirements{
						CPUCoresMin:   2,
						MemoryMiBMin:  4 * 1024,
						StorageGiBMin: 20,
					},
					Accelerator: agentcontrol.AcceleratorRequirements{},
					Deployment: agentcontrol.DeploymentRequirements{
						ReplicasMin: 1,
						ReplicasMax: 2,
						Isolation:   "ONE_MAJOR_APP_PER_VM",
					},
				},
			},
			ModelRecommendation: agentcontrol.ModelRecommendation{
				RecommendationID: "model-rec-must-not-project",
				SelectedModel: agentcontrol.SelectedModel{
					ModelID:      "model-must-not-project",
					ModelVersion: "model-version-must-not-project",
					Source:       "model-source-must-not-project",
				},
				InferenceConfiguration: agentcontrol.InferenceConfiguration{
					RuntimeEngine: "runtime-must-not-project",
				},
			},
		},
	}
	resourceRecommendation := agentcontrol.ResourceRecommendationEnvelope{
		Envelope: bridgeEnvelope(
			"msg-resource-bridge-001",
			agentcontrol.MessageResourceRecommendationCreated,
			applicationContext.MessageID,
		),
		Data: agentcontrol.ResourceRecommendationData{
			ResourceRecommendation: agentcontrol.ResourceRecommendation{
				RecommendationID:    "resource-rec-bridge-001",
				ProfileID:           "profile-bridge-001",
				SnapshotID:          "snapshot-bridge-001",
				Status:              recommendationStatusFound,
				SelectedCandidateID: "resource-candidate-001",
				Candidates: []agentcontrol.ResourceCandidate{
					{
						CandidateID: "resource-candidate-001",
						Feasible:    true,
						DesiredInfrastructure: agentcontrol.DesiredInfrastructure{
							NodeCount:         1,
							CPUCoresPerNode:   4,
							MemoryMiBPerNode:  8 * 1024,
							StorageGiBPerNode: 100,
							Accelerator:       agentcontrol.AcceleratorAllocation{},
							Isolation:         "ONE_MAJOR_APP_PER_VM",
						},
						ResourceHints: []string{"resource-hint-must-not-project"},
					},
				},
			},
		},
	}
	return Input{
		Request: llmop.Request{
			RequestID:   "req-bridge-001",
			CandidateID: "qwen-bound-by-caller",
			RequestedBy: "ai-ops-geon-planner",
			Application: llmop.Application{
				AppVersionID:    "appver-registered-001",
				TargetProfileID: "target-hint-001",
			},
		},
		AnalysisRequest:        analysis,
		ApplicationContext:     applicationContext,
		ResourceRecommendation: resourceRecommendation,
	}
}

func bridgeEnvelope(messageID string, messageType string, causationID string) agentcontrol.Envelope {
	return agentcontrol.Envelope{
		ContractVersion: agentcontrol.ContractVersionV1,
		MessageID:       messageID,
		MessageType:     messageType,
		OccurredAt:      "2026-08-05T05:00:00Z",
		CorrelationID:   "corr-bridge-001",
		TraceID:         "trace-bridge-001",
		CausationID:     causationID,
		Source: agentcontrol.Endpoint{
			System:    "source-system",
			Component: "source-component",
		},
		Target: agentcontrol.Endpoint{
			System:    "target-system",
			Component: "target-component",
		},
	}
}
