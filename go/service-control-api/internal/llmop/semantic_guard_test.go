package llmop

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func TestExtractExactResourceIntentKoreanFixture(t *testing.T) {
	intent, err := extractExactResourceIntent(
		"NVIDIA GPU 1개, CPU 4개, 메모리 16Gi, 저장소 20Gi 기준으로 준비해줘.",
	)
	if err != nil {
		t.Fatalf("extract exact Korean resource intent: %v", err)
	}
	if intent.CPU.Value != 4 || intent.GPU.Value != 1 ||
		intent.MemoryMi.Value != 16*1024 || intent.StorageMi.Value != 20*1024 ||
		!intent.complete() {
		t.Fatalf("unexpected exact resource intent: %#v", intent)
	}
}

func TestExtractExactResourceIntentReturnsStableAmbiguityCode(t *testing.T) {
	_, err := extractExactResourceIntent(
		"CPU 4.5개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 준비해줘.",
	)
	var semanticErr *semanticGuardError
	if !errors.As(err, &semanticErr) {
		t.Fatalf("expected semanticGuardError, got %v", err)
	}
	if semanticErr.code != "ambiguous_explicit_intent" || semanticErr.field != "cpu" {
		t.Fatalf("unexpected semantic guard detail: %#v", semanticErr)
	}
}

func TestSemanticGuardAcceptsEquivalentResourceUnits(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "CPU 4개, GPU 1개, 메모리 16384Mi, 저장소 20480Mi 기준으로 배포 계획만 작성해줘."

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	if err != nil {
		t.Fatalf("equivalent Mi and Gi quantities should pass: %v", err)
	}
	if result.Status != StatusHandoffReady || result.Handoff.PreparedRequest == nil {
		t.Fatalf("expected a prepared AppDeploy request, got status %s", result.Status)
	}
}

func TestSemanticGuardRejectsExplicitResourceMismatch(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name        string
		cpu         string
		memory      string
		gpu         string
		storage     string
		accelerator string
	}{
		{name: "cpu", cpu: "8", memory: "16Gi", gpu: "1", storage: "20Gi", accelerator: "nvidia"},
		{name: "memory", cpu: "4", memory: "32Gi", gpu: "1", storage: "20Gi", accelerator: "nvidia"},
		{name: "gpu", cpu: "4", memory: "16Gi", gpu: "2", storage: "20Gi", accelerator: "nvidia"},
		{name: "storage", cpu: "4", memory: "16Gi", gpu: "1", storage: "40Gi", accelerator: "nvidia"},
	}
	for _, item := range tests {
		t.Run(item.name, func(t *testing.T) {
			result, err := prepareSemanticProposal(
				t,
				now,
				testRequest(t, now),
				createProposalJSON(
					item.cpu,
					item.memory,
					item.gpu,
					item.storage,
					item.accelerator,
				),
			)
			assertSemanticRejection(t, result, err)
		})
	}
}

func TestSemanticGuardRequiresCompleteUnambiguousIntentForCreate(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name        string
		userRequest string
	}{
		{
			name:        "missing storage",
			userRequest: "CPU 4개, GPU 1개, 메모리 16Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "approximate cpu",
			userRequest: "약 CPU 4개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "cpu range",
			userRequest: "CPU 2~4개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "fractional cpu",
			userRequest: "CPU 4.5개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "fractional gpu notation",
			userRequest: "CPU 4개, GPU 1/2개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "localized decimal cpu",
			userRequest: "CPU 4,5개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "approximately four cpu",
			userRequest: "CPU 4개 정도, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "roughly four cpu",
			userRequest: "roughly CPU 4, GPU 1, memory 16Gi, storage 20Gi",
		},
		{
			name:        "memory around value",
			userRequest: "CPU 4개, GPU 1개, 메모리 16Gi 내외, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "minimum marker",
			userRequest: "최소한 CPU 4개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "plus marker",
			userRequest: "CPU 4개+, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
		{
			name:        "conflicting cpu",
			userRequest: "CPU 4개와 CPU 8개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘.",
		},
	}
	for _, item := range tests {
		t.Run(item.name, func(t *testing.T) {
			request := testRequest(t, now)
			request.Application.UserRequest = item.userRequest
			result, err := prepareSemanticProposal(
				t,
				now,
				request,
				createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
			)
			assertSemanticRejection(t, result, err)
		})
	}
}

func TestSemanticGuardAllowsClarificationForIncompleteIntent(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "CPU 4개와 메모리 16Gi를 고려해서 배포 계획만 작성해줘."
	clarification := `{
		"action":"request_clarification",
		"reason_code":"MISSING_RESOURCE_VALUES",
		"reason":"The resource request needs exact GPU and storage values.",
		"confidence":0.9,
		"assumptions":[]
	}`

	result, err := prepareSemanticProposal(t, now, request, clarification)
	if err != nil {
		t.Fatalf("clarification should remain a valid fail-closed outcome: %v", err)
	}
	if result.Status != StatusClarificationNeeded {
		t.Fatalf("expected %s, got %s", StatusClarificationNeeded, result.Status)
	}
	if result.Manifest != nil || result.Handoff.PreparedRequest != nil {
		t.Fatal("clarification must not prepare an AppDeploy request")
	}
}

func TestSemanticGuardRequiresOneFreshReadyTarget(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.ResourceSnapshot.Targets = []appdeploy.RuntimeHealthSnapshot{
		{
			TargetProfileID:  "target-cpu-memory",
			CPUAvailable:     true,
			MemoryAvailable:  true,
			GPUAvailable:     false,
			StorageAvailable: false,
			LastCheckedAt:    request.OperationContext.ResourceSnapshot.ObservedAt,
		},
		{
			TargetProfileID:  "target-gpu-storage",
			CPUAvailable:     false,
			MemoryAvailable:  false,
			GPUAvailable:     true,
			StorageAvailable: true,
			LastCheckedAt:    request.OperationContext.ResourceSnapshot.ObservedAt,
		},
	}

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsAnyMissingRequiredAvailability(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	tests := []struct {
		name   string
		mutate func(*appdeploy.RuntimeHealthSnapshot)
	}{
		{name: "cpu", mutate: func(target *appdeploy.RuntimeHealthSnapshot) { target.CPUAvailable = false }},
		{name: "memory", mutate: func(target *appdeploy.RuntimeHealthSnapshot) { target.MemoryAvailable = false }},
		{name: "gpu", mutate: func(target *appdeploy.RuntimeHealthSnapshot) { target.GPUAvailable = false }},
		{name: "storage", mutate: func(target *appdeploy.RuntimeHealthSnapshot) { target.StorageAvailable = false }},
	}
	for _, item := range tests {
		t.Run(item.name, func(t *testing.T) {
			request := testRequest(t, now)
			item.mutate(&request.OperationContext.ResourceSnapshot.Targets[0])
			result, err := prepareSemanticProposal(
				t,
				now,
				request,
				createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
			)
			assertSemanticRejection(t, result, err)
		})
	}
}

func TestSemanticGuardHonorsTrustedTargetHint(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.TargetProfileID = "target-gpu-unavailable"

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardDoesNotRequireGPUAvailabilityForCPUPlan(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "CPU 4개, GPU 0개, 메모리 16Gi, 저장소 20Gi 기준으로 배포 계획만 작성해줘."
	request.OperationContext.ResourceSnapshot.Targets[0].GPUAvailable = false

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "0", "20Gi", "none"),
	)
	if err != nil {
		t.Fatalf("CPU-only plan should not require GPU availability: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
}

func TestSemanticGuardDoesNotTreatMissingSnapshotAsNegativeEvidence(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.ResourceSnapshot = nil

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	if err != nil {
		t.Fatalf("missing snapshot is unknown evidence, not a resource conflict: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
}

func TestSemanticGuardRejectsFreshEmptySnapshot(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.ResourceSnapshot.Targets = nil

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsUnavailableRuntimeDespiteReadyBooleans(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.ResourceSnapshot.Targets[0].RuntimeHealth = "down"

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestNormalizerRejectsAnonymousReadyTargetBeforeProposal(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.ResourceSnapshot.Targets[0].TargetProfileID = ""

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	if err == nil {
		t.Fatal("anonymous resource target must fail during normalization")
	}
	if result.Status != StatusRequestRejected {
		t.Fatalf("expected %s, got %s", StatusRequestRejected, result.Status)
	}
	if result.Manifest != nil || result.Handoff.PreparedRequest != nil {
		t.Fatal("normalization rejection must not prepare an AppDeploy request")
	}
}

func TestSemanticGuardRejectsDuplicateTargetReadinessRows(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	conflicting := request.OperationContext.ResourceSnapshot.Targets[0]
	conflicting.Status = "unavailable"
	conflicting.RuntimeHealth = "down"
	conflicting.CPUAvailable = false
	request.OperationContext.ResourceSnapshot.Targets = append(
		request.OperationContext.ResourceSnapshot.Targets,
		conflicting,
	)

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
	var semanticErr *semanticGuardError
	if !errors.As(err, &semanticErr) || semanticErr.code != "ambiguous_resource_snapshot" {
		t.Fatalf("expected ambiguous_resource_snapshot, got %v", err)
	}
}

func TestSemanticGuardRejectsCrossSourceRuntimeContradiction(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.MonitoringSummary.Summary.RuntimeHealth = []appdeploy.RuntimeHealthSnapshot{{
		TargetProfileID: request.OperationContext.ResourceSnapshot.Targets[0].TargetProfileID,
		Status:          "available",
		RuntimeHealth:   "down",
		LastCheckedAt:    request.OperationContext.MonitoringSummary.Summary.GeneratedAt,
	}}

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsProvidedStaleSnapshot(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.OperationContext.ResourceSnapshot.ObservedAt = now.Add(-11 * time.Minute)
	request.OperationContext.ResourceSnapshot.Targets[0].LastCheckedAt = now.Add(-11 * time.Minute)

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
	if result.Evidence.Input.ResourceSnapshotIncluded {
		t.Fatal("stale resource snapshot must not be marked as prompt input")
	}
}

func TestSemanticGuardAppliesTrustedPlanningMinimaWithoutLeakingSourceIDs(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "이 추론 서비스의 준비 전용 배포 매니페스트를 작성해줘."
	request.Application.PlanningConstraints = &PlanningConstraints{
		SourceProfileID:        "profile-source-001",
		SourceRecommendationID: "recommendation-source-001",
		RecommendationFeasible: true,
		CPUCoresMin:            4,
		MemoryMiBMin:           16 * 1024,
		GPUCountMin:            1,
		StorageGiBMin:          20,
		Accelerator:            "nvidia",
	}
	client := &capturingCompletionClient{
		content: createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	}
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }

	result, err := NewPlanner(client, normalizer).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
	if err != nil {
		t.Fatalf("proposal satisfying trusted minima should pass: %v", err)
	}
	if result.Status != StatusHandoffReady {
		t.Fatalf("expected %s, got %s", StatusHandoffReady, result.Status)
	}
	for _, sourceID := range []string{"profile-source-001", "recommendation-source-001"} {
		if strings.Contains(client.userPrompt, sourceID) {
			t.Fatalf("planning source identifier reached the Qwen prompt: %s", sourceID)
		}
	}
	if !strings.Contains(client.userPrompt, `"planning_constraints"`) {
		t.Fatal("expected identifier-free planning minima in the Qwen prompt")
	}

	request.Application.PlanningConstraints.CPUCoresMin = 8
	result, err = prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsResourceInflationWithoutExactRecommendation(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "이 추론 서비스의 준비 전용 배포 매니페스트를 작성해줘."
	request.Application.PlanningConstraints = &PlanningConstraints{
		SourceProfileID:        "profile-source-002",
		SourceRecommendationID: "recommendation-source-002",
		RecommendationFeasible: true,
		CPUCoresMin:            4,
		MemoryMiBMin:           16 * 1024,
		GPUCountMin:            1,
		StorageGiBMin:          20,
		Accelerator:            "nvidia",
	}

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("8", "32Gi", "1", "40Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsAmbiguousMentionWithPlanningConstraints(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := cpuOnlyPlanningRequest(t, now)
	request.Application.UserRequest = "CPU 최소 4개인 준비 전용 배포 매니페스트를 작성해줘."

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "0", "20Gi", "none"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsAcceleratorEscalationFromCPUConstraints(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := cpuOnlyPlanningRequest(t, now)

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardTreatsExplicitGPUZeroAsExactWithPlanningConstraints(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := testRequest(t, now)
	request.Application.UserRequest = "CPU 4개, GPU 0개, 메모리 16Gi, 저장소 20Gi 기준으로 준비해줘."
	request.Application.PlanningConstraints = &PlanningConstraints{
		SourceProfileID:        "profile-source-gpu-001",
		SourceRecommendationID: "recommendation-source-gpu-001",
		RecommendationFeasible: true,
		CPUCoresMin:            4,
		MemoryMiBMin:           16 * 1024,
		GPUCountMin:            1,
		StorageGiBMin:          20,
		Accelerator:            "nvidia",
	}

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
	)
	assertSemanticRejection(t, result, err)
}

func TestSemanticGuardRejectsZeroPositiveResourceIntentWithPlanningConstraints(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	userRequests := []string{
		"CPU 0개, GPU 1개, 메모리 16Gi, 저장소 20Gi 기준으로 준비해줘.",
		"CPU 4개, GPU 1개, 메모리 0Gi, 저장소 20Gi 기준으로 준비해줘.",
		"CPU 4개, GPU 1개, 메모리 16Gi, 저장소 0Gi 기준으로 준비해줘.",
	}
	for _, userRequest := range userRequests {
		request := testRequest(t, now)
		request.Application.UserRequest = userRequest
		request.Application.PlanningConstraints = &PlanningConstraints{
			SourceProfileID:        "profile-source-gpu-001",
			SourceRecommendationID: "recommendation-source-gpu-001",
			RecommendationFeasible: true,
			CPUCoresMin:            4,
			MemoryMiBMin:           16 * 1024,
			GPUCountMin:            1,
			StorageGiBMin:          20,
			Accelerator:            "nvidia",
		}

		result, err := prepareSemanticProposal(
			t,
			now,
			request,
			createProposalJSON("4", "16Gi", "1", "20Gi", "nvidia"),
		)
		assertSemanticRejection(t, result, err)
	}
}

func TestSemanticGuardPreservesExactUpstreamResourceRecommendation(t *testing.T) {
	now := mustTime(t, "2026-08-05T14:05:00+09:00")
	request := cpuOnlyPlanningRequest(t, now)
	request.Application.UserRequest = "CPU 2개, GPU 0개, 메모리 4Gi, 저장소 20Gi 기준으로 준비해줘."
	request.Application.PlanningConstraints.CPUCoresMin = 2
	request.Application.PlanningConstraints.MemoryMiBMin = 4 * 1024
	request.Application.PlanningConstraints.StorageGiBMin = 20
	request.Application.PlanningConstraints.RecommendedResources = &RecommendedResources{
		CPUCores:    4,
		MemoryMiB:   8 * 1024,
		GPUCount:    0,
		StorageGiB:  100,
		Accelerator: "none",
	}

	result, err := prepareSemanticProposal(
		t,
		now,
		request,
		createProposalJSON("4", "8Gi", "0", "100Gi", "none"),
	)
	if err != nil || result.Status != StatusHandoffReady {
		t.Fatalf("exact upstream recommendation should reach handoff: %s, %v", result.Status, err)
	}

	for _, proposal := range []string{
		createProposalJSON("2", "8Gi", "0", "100Gi", "none"),
		createProposalJSON("8", "8Gi", "0", "100Gi", "none"),
		createProposalJSON("4", "16Gi", "0", "100Gi", "none"),
	} {
		result, err := prepareSemanticProposal(t, now, request, proposal)
		assertSemanticRejection(t, result, err)
	}
}

func cpuOnlyPlanningRequest(t *testing.T, now time.Time) Request {
	t.Helper()
	request := testRequest(t, now)
	request.Application.UserRequest = "이 추론 서비스의 준비 전용 배포 매니페스트를 작성해줘."
	request.Application.PlanningConstraints = &PlanningConstraints{
		SourceProfileID:        "profile-source-cpu-001",
		SourceRecommendationID: "recommendation-source-cpu-001",
		RecommendationFeasible: true,
		CPUCoresMin:            4,
		MemoryMiBMin:           16 * 1024,
		GPUCountMin:            0,
		StorageGiBMin:          20,
		Accelerator:            "none",
	}
	return request
}

func prepareSemanticProposal(
	t *testing.T,
	now time.Time,
	request Request,
	proposal string,
) (Result, error) {
	t.Helper()
	normalizer := NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	return NewPlanner(
		&capturingCompletionClient{content: proposal},
		normalizer,
	).Prepare(
		context.Background(),
		testCandidate(),
		testGuardPolicy(),
		request,
	)
}

func createProposalJSON(
	cpu string,
	memory string,
	gpu string,
	storage string,
	accelerator string,
) string {
	return fmt.Sprintf(`{
		"action":"create_deployment_manifest",
		"reason_code":"RESOURCE_PLAN_READY",
		"reason":"The requested resource plan is explicit and bounded.",
		"confidence":0.9,
		"accelerator":%q,
		"resources":{"cpu":%q,"memory":%q,"gpu":%q,"storage":%q},
		"assumptions":[]
	}`, accelerator, cpu, memory, gpu, storage)
}

func assertSemanticRejection(t *testing.T, result Result, err error) {
	t.Helper()
	if err == nil {
		t.Fatal("expected deterministic semantic rejection")
	}
	if result.Status != StatusManifestRejected {
		t.Fatalf("expected %s, got %s", StatusManifestRejected, result.Status)
	}
	if result.Manifest != nil || result.Handoff.PreparedRequest != nil {
		t.Fatal("rejected proposal must not prepare an AppDeploy request")
	}
	if result.Handoff.NextEndpoint != "" {
		t.Fatalf("rejected proposal exposed a handoff endpoint: %q", result.Handoff.NextEndpoint)
	}
	if result.Decision.Reason != "Qwen proposal failed deterministic request-alignment safeguards" {
		t.Fatalf("unexpected public rejection reason: %q", result.Decision.Reason)
	}
}
