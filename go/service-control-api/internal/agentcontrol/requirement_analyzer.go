package agentcontrol

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

const (
	defaultCPUCores     = 2
	defaultMemoryMiB    = 4096
	defaultStorageGiB   = 20
	defaultGPUCount     = 1
	defaultGPUMemoryMiB = 16384
	defaultReplicasMin  = 1
	defaultReplicasMax  = 2
)

type RequirementAnalyzer interface {
	Analyze(context.Context, AutomationRunInput) (RequirementAnalysisResult, error)
}

type LocalRequirementAnalyzer struct{}

func (LocalRequirementAnalyzer) Analyze(
	ctx context.Context,
	input AutomationRunInput,
) (RequirementAnalysisResult, error) {
	if err := ctx.Err(); err != nil {
		return RequirementAnalysisResult{}, err
	}
	switch input.InputType {
	case InputTypeNaturalLanguage:
		return analyzeNaturalLanguage(input.Request)
	case InputTypeStructured:
		if input.AppSpec == nil {
			return RequirementAnalysisResult{}, fmt.Errorf("app_spec is required for structured input")
		}
		return analyzeStructuredAppSpec(*input.AppSpec), nil
	default:
		return RequirementAnalysisResult{}, fmt.Errorf("unsupported input_type: %s", input.InputType)
	}
}

func analyzeNaturalLanguage(request string) (RequirementAnalysisResult, error) {
	request = strings.TrimSpace(request)
	if request == "" {
		return RequirementAnalysisResult{}, fmt.Errorf("request is required for natural_language input")
	}
	if containsSensitiveRequestField(request) {
		return RequirementAnalysisResult{}, fmt.Errorf("request contains a forbidden sensitive field")
	}

	normalized := strings.ToLower(request)
	assumptions := make([]string, 0, 8)
	cpu, ok := extractCount(normalized, []string{"cpu cores?", "cpu core", "cpu", "코어"})
	if !ok {
		cpu = defaultCPUCores
		assumptions = append(assumptions, "CPU requirement defaulted to 2 cores.")
	}
	memory, ok := extractCapacityMiB(normalized, []string{"memory", "메모리"})
	if !ok {
		memory = defaultMemoryMiB
		assumptions = append(assumptions, "Memory requirement defaulted to 4096 MiB.")
	}
	storageMiB, ok := extractCapacityMiB(normalized, []string{"storage", "disk", "스토리지", "디스크"})
	storage := defaultStorageGiB
	if ok {
		storage = maxInt(1, storageMiB/1024)
	} else {
		assumptions = append(assumptions, "Storage requirement defaulted to 20 GiB.")
	}

	gpuRequired := strings.Contains(normalized, "gpu") ||
		strings.Contains(normalized, "그래픽 가속기")
	accelerator := AcceleratorRequirements{}
	if gpuRequired {
		gpuCount, found := extractCountAfterLabel(normalized, []string{"gpu"})
		if !found {
			gpuCount = defaultGPUCount
			assumptions = append(assumptions, "GPU count defaulted to 1.")
		}
		gpuMemory, found := extractCapacityMiB(normalized, []string{"vram", "gpu memory", "gpu 메모리"})
		if !found {
			gpuMemory = defaultGPUMemoryMiB
			assumptions = append(assumptions, "GPU memory requirement defaulted to 16384 MiB.")
		}
		accelerator = AcceleratorRequirements{
			Required:              true,
			Type:                  "GPU",
			CountMin:              gpuCount,
			MemoryMiBMinPerDevice: gpuMemory,
		}
	}

	replicas, ok := extractCount(normalized, []string{"replicas?", "replica", "레플리카"})
	if !ok {
		replicas = defaultReplicasMin
		assumptions = append(assumptions, "Minimum replica count defaulted to 1.")
	}

	appID := "ai-application"
	modelID := "unspecified-model"
	if strings.Contains(normalized, "qwen") {
		appID = "qwen-service"
		modelID = "qwen"
	}
	taskType := "AI_INFERENCE"
	if strings.Contains(normalized, "llm") || strings.Contains(normalized, "qwen") {
		taskType = "LLM_INFERENCE"
	}

	profile := ApplicationProfile{
		ProfileID:  "profile-" + appID,
		AppID:      appID,
		AppVersion: "1.0.0",
		Workload: WorkloadProfile{
			TaskType:       taskType,
			RequestPattern: "ONLINE",
		},
		Requirements: ApplicationRequirements{
			Compute: ComputeRequirements{
				CPUCoresMin:   cpu,
				MemoryMiBMin:  memory,
				StorageGiBMin: storage,
			},
			Accelerator: accelerator,
			Deployment: DeploymentRequirements{
				ReplicasMin: replicas,
				ReplicasMax: maxInt(defaultReplicasMax, replicas),
				Isolation:   "ONE_MAJOR_APP_PER_VM",
			},
		},
		Analysis: AnalysisSummary{
			Confidence:  0.8,
			Assumptions: append([]string(nil), assumptions...),
		},
	}
	return RequirementAnalysisResult{
		ApplicationProfile:  profile,
		ModelRecommendation: defaultModelRecommendation(modelID),
		Mode:                AnalysisModeLocalRule,
		Evidence: RequirementAnalysisEvidence{
			Mode:        AnalysisModeLocalRule,
			SourceInput: request,
			Assumptions: assumptions,
		},
	}, nil
}

func analyzeStructuredAppSpec(spec StructuredAppSpec) RequirementAnalysisResult {
	appID := strings.TrimSpace(spec.AppID)
	if appID == "" {
		appID = "ai-application"
	}
	appVersion := strings.TrimSpace(spec.AppVersion)
	if appVersion == "" {
		appVersion = "1.0.0"
	}
	workloadType := strings.TrimSpace(spec.WorkloadType)
	if workloadType == "" {
		workloadType = "AI_INFERENCE"
	}
	replicasMin := spec.ReplicasMin
	if replicasMin == 0 {
		replicasMin = defaultReplicasMin
	}
	replicasMax := spec.ReplicasMax
	if replicasMax == 0 {
		replicasMax = maxInt(defaultReplicasMax, replicasMin)
	}
	acceleratorRequired := spec.AcceleratorCount > 0 ||
		strings.TrimSpace(spec.AcceleratorType) != ""
	acceleratorType := strings.ToUpper(strings.TrimSpace(spec.AcceleratorType))
	if acceleratorRequired && acceleratorType == "" {
		acceleratorType = "GPU"
	}
	profile := ApplicationProfile{
		ProfileID:  "profile-" + sanitizeIdentifier(appID),
		AppID:      appID,
		AppVersion: appVersion,
		Workload: WorkloadProfile{
			TaskType:       workloadType,
			RequestPattern: "ONLINE",
		},
		Requirements: ApplicationRequirements{
			Compute: ComputeRequirements{
				CPUCoresMin:   spec.CPUCores,
				MemoryMiBMin:  spec.MemoryMiB,
				StorageGiBMin: spec.StorageGiB,
			},
			Accelerator: AcceleratorRequirements{
				Required:              acceleratorRequired,
				Type:                  acceleratorType,
				CountMin:              spec.AcceleratorCount,
				MemoryMiBMinPerDevice: spec.AcceleratorMemoryMiB,
			},
			Deployment: DeploymentRequirements{
				ReplicasMin: replicasMin,
				ReplicasMax: replicasMax,
				Isolation:   "ONE_MAJOR_APP_PER_VM",
			},
		},
		Analysis: AnalysisSummary{Confidence: 1},
	}
	return RequirementAnalysisResult{
		ApplicationProfile:  profile,
		ModelRecommendation: defaultModelRecommendation("unspecified-model"),
		Mode:                AnalysisModeStructured,
		Evidence: RequirementAnalysisEvidence{
			Mode: AnalysisModeStructured,
		},
	}
}

func defaultModelRecommendation(modelID string) ModelRecommendation {
	return ModelRecommendation{
		SelectedModel: SelectedModel{
			ModelID:      modelID,
			ModelVersion: "1",
			Source:       "USER_OR_ANALYZER",
		},
		InferenceConfiguration: InferenceConfiguration{
			RuntimeEngine:      "VLLM",
			Precision:          "FP16",
			MaxBatchSize:       8,
			MaxConcurrency:     20,
			TensorParallelSize: 1,
			Replicas:           1,
		},
	}
}

func containsSensitiveRequestField(value string) bool {
	value = strings.ToLower(value)
	for _, token := range []string{"password", "secret", "token", "credential", "private_key", "비밀번호", "시크릿"} {
		if strings.Contains(value, token) {
			return true
		}
	}
	return false
}

func extractCount(value string, labels []string) (int, bool) {
	if result, ok := extractCountAfterLabel(value, labels); ok {
		return result, true
	}
	labelPattern := strings.Join(labels, "|")
	expression := regexp.MustCompile(`(?i)(\d+)\s*(?:` + labelPattern + `)`)
	matches := expression.FindStringSubmatch(value)
	if len(matches) < 2 {
		return 0, false
	}
	result, err := strconv.Atoi(matches[1])
	return result, err == nil
}

func extractCountAfterLabel(value string, labels []string) (int, bool) {
	labelPattern := strings.Join(labels, "|")
	expression := regexp.MustCompile(`(?i)(?:` + labelPattern + `)\s*[:=]?\s*(\d+)`)
	matches := expression.FindStringSubmatch(value)
	if len(matches) < 2 {
		return 0, false
	}
	result, err := strconv.Atoi(matches[1])
	return result, err == nil
}

func extractCapacityMiB(value string, labels []string) (int, bool) {
	labelPattern := strings.Join(labels, "|")
	after := regexp.MustCompile(`(?i)(?:` + labelPattern + `)\s*[:=]?\s*(\d+)\s*(gib|gb|mib|mb)`)
	if result, ok := capacityMatch(after.FindStringSubmatch(value)); ok {
		return result, true
	}
	before := regexp.MustCompile(`(?i)(\d+)\s*(gib|gb|mib|mb)\s*(?:` + labelPattern + `)`)
	return capacityMatch(before.FindStringSubmatch(value))
}

func capacityMatch(matches []string) (int, bool) {
	if len(matches) < 3 {
		return 0, false
	}
	value, err := strconv.Atoi(matches[1])
	if err != nil {
		return 0, false
	}
	switch strings.ToLower(matches[2]) {
	case "gib", "gb":
		return value * 1024, true
	case "mib", "mb":
		return value, true
	default:
		return 0, false
	}
}

func sanitizeIdentifier(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = regexp.MustCompile(`[^a-z0-9-]+`).ReplaceAllString(value, "-")
	value = strings.Trim(value, "-")
	if value == "" {
		return "ai-application"
	}
	return value
}

func maxInt(left, right int) int {
	if left > right {
		return left
	}
	return right
}
