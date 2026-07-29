package agentcontrol

import (
	"context"
	"testing"
)

func TestLocalRequirementAnalyzerParsesKoreanGPURequest(t *testing.T) {
	analyzer := LocalRequirementAnalyzer{}
	result, err := analyzer.Analyze(context.Background(), AutomationRunInput{
		InputType: InputTypeNaturalLanguage,
		Request:   "Qwen 추론 서비스를 CPU 4코어, 메모리 16GiB, GPU 1개, VRAM 16GiB, 스토리지 20GiB로 배포해 주세요.",
	})
	if err != nil {
		t.Fatalf("analyze Korean request: %v", err)
	}

	requirements := result.ApplicationProfile.Requirements
	if result.Mode != AnalysisModeLocalRule {
		t.Fatalf("mode = %q, want %q", result.Mode, AnalysisModeLocalRule)
	}
	if requirements.Compute.CPUCoresMin != 4 ||
		requirements.Compute.MemoryMiBMin != 16*1024 ||
		requirements.Compute.StorageGiBMin != 20 {
		t.Fatalf("compute requirements = %#v", requirements.Compute)
	}
	if !requirements.Accelerator.Required ||
		requirements.Accelerator.Type != "GPU" ||
		requirements.Accelerator.CountMin != 1 ||
		requirements.Accelerator.MemoryMiBMinPerDevice != 16*1024 {
		t.Fatalf("accelerator requirements = %#v", requirements.Accelerator)
	}
	if result.ApplicationProfile.Workload.TaskType != "LLM_INFERENCE" {
		t.Fatalf("task type = %q", result.ApplicationProfile.Workload.TaskType)
	}
}

func TestLocalRequirementAnalyzerParsesEnglishCPURequest(t *testing.T) {
	analyzer := LocalRequirementAnalyzer{}
	result, err := analyzer.Analyze(context.Background(), AutomationRunInput{
		InputType: InputTypeNaturalLanguage,
		Request:   "Deploy a CPU inference service with 2 CPU cores, 8 GiB memory, 40 GiB storage and 1 replica.",
	})
	if err != nil {
		t.Fatalf("analyze English request: %v", err)
	}

	requirements := result.ApplicationProfile.Requirements
	if requirements.Compute.CPUCoresMin != 2 ||
		requirements.Compute.MemoryMiBMin != 8*1024 ||
		requirements.Compute.StorageGiBMin != 40 {
		t.Fatalf("compute requirements = %#v", requirements.Compute)
	}
	if requirements.Accelerator.Required || requirements.Accelerator.CountMin != 0 {
		t.Fatalf("CPU request unexpectedly requires an accelerator: %#v", requirements.Accelerator)
	}
	if requirements.Deployment.ReplicasMin != 1 {
		t.Fatalf("replicas_min = %d, want 1", requirements.Deployment.ReplicasMin)
	}
}

func TestLocalRequirementAnalyzerMapsStructuredAppSpecExactly(t *testing.T) {
	analyzer := LocalRequirementAnalyzer{}
	result, err := analyzer.Analyze(context.Background(), AutomationRunInput{
		InputType: InputTypeStructured,
		AppSpec: &StructuredAppSpec{
			AppID:                "vision-service",
			AppVersion:           "2.1.0",
			WorkloadType:         "VISION_INFERENCE",
			CPUCores:             6,
			MemoryMiB:            12288,
			StorageGiB:           50,
			AcceleratorType:      "GPU",
			AcceleratorCount:     2,
			AcceleratorMemoryMiB: 24576,
			ReplicasMin:          2,
			ReplicasMax:          4,
		},
	})
	if err != nil {
		t.Fatalf("analyze structured input: %v", err)
	}

	profile := result.ApplicationProfile
	if result.Mode != AnalysisModeStructured {
		t.Fatalf("mode = %q, want %q", result.Mode, AnalysisModeStructured)
	}
	if profile.AppID != "vision-service" || profile.AppVersion != "2.1.0" {
		t.Fatalf("application identity = %q %q", profile.AppID, profile.AppVersion)
	}
	if profile.Requirements.Compute.CPUCoresMin != 6 ||
		profile.Requirements.Compute.MemoryMiBMin != 12288 ||
		profile.Requirements.Accelerator.CountMin != 2 ||
		profile.Requirements.Deployment.ReplicasMax != 4 {
		t.Fatalf("structured requirements changed: %#v", profile.Requirements)
	}
}

func TestLocalRequirementAnalyzerRejectsEmptyOrUnsupportedInput(t *testing.T) {
	analyzer := LocalRequirementAnalyzer{}
	tests := []AutomationRunInput{
		{InputType: InputTypeNaturalLanguage},
		{InputType: "unsupported", Request: "deploy"},
		{InputType: InputTypeStructured},
	}
	for _, input := range tests {
		if _, err := analyzer.Analyze(context.Background(), input); err == nil {
			t.Fatalf("input %#v must be rejected", input)
		}
	}
}

func TestLocalRequirementAnalyzerRecordsConservativeDefaults(t *testing.T) {
	analyzer := LocalRequirementAnalyzer{}
	result, err := analyzer.Analyze(context.Background(), AutomationRunInput{
		InputType: InputTypeNaturalLanguage,
		Request:   "Qwen 서비스를 GPU로 배포해 주세요.",
	})
	if err != nil {
		t.Fatalf("analyze request with defaults: %v", err)
	}

	requirements := result.ApplicationProfile.Requirements
	if requirements.Compute.CPUCoresMin != 2 ||
		requirements.Compute.MemoryMiBMin != 4096 ||
		requirements.Compute.StorageGiBMin != 20 ||
		requirements.Accelerator.CountMin != 1 ||
		requirements.Deployment.ReplicasMin != 1 {
		t.Fatalf("unexpected conservative defaults: %#v", requirements)
	}
	if len(result.Evidence.Assumptions) < 4 {
		t.Fatalf("defaults were not recorded as assumptions: %#v", result.Evidence)
	}
}
