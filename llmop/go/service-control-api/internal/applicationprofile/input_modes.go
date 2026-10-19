package applicationprofile

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

const (
	defaultCPUCores    = 2
	defaultMemoryMiB   = 4096
	defaultStorageGiB  = 20
	defaultReplicasMin = 1
	defaultReplicasMax = 2
)

var negativeQuantityPattern = regexp.MustCompile(
	`(?i)(?:cpu|cores?|memory|mem|storage|disk|gpu|replicas?|코어|메모리|스토리지|디스크|레플리카)\s*[:=]?\s*-\s*\d+|` +
		`-\s*\d+\s*(?:cpu|cores?|gpus?|replicas?|코어|메모리|스토리지|디스크|레플리카)`,
)

func requestInputType(request GenerateRequest) (string, error) {
	inputType := strings.TrimSpace(request.InputType)
	if inputType == "" {
		switch {
		case request.AppSpec != nil && strings.TrimSpace(request.Request) == "":
			inputType = InputTypeStructured
		case request.AppSpec == nil && strings.TrimSpace(request.Request) != "":
			inputType = InputTypeNaturalLanguage
		default:
			return "", fmt.Errorf("exactly one of request and app_spec is required")
		}
	}
	switch inputType {
	case InputTypeNaturalLanguage:
		if strings.TrimSpace(request.Request) == "" || request.AppSpec != nil {
			return "", fmt.Errorf("natural_language input requires request and forbids app_spec")
		}
	case InputTypeStructured:
		if request.AppSpec == nil || strings.TrimSpace(request.Request) != "" {
			return "", fmt.Errorf("structured input requires app_spec and forbids request")
		}
		if strings.TrimSpace(request.CandidateID) != "" || strings.TrimSpace(request.SelectionPolicy) != "" {
			return "", fmt.Errorf("structured input does not use an LLM candidate or selection policy")
		}
	default:
		return "", fmt.Errorf("input_type must be natural_language or structured")
	}
	return inputType, nil
}

func profileFromStructuredInput(request GenerateRequest) (ApplicationProfile, error) {
	spec := request.AppSpec
	if spec == nil {
		return ApplicationProfile{}, fmt.Errorf("app_spec is required")
	}
	if strings.TrimSpace(spec.AppID) == "" || strings.TrimSpace(spec.AppVersion) == "" ||
		strings.TrimSpace(spec.WorkloadType) == "" {
		return ApplicationProfile{}, fmt.Errorf("app_spec app_id, app_version, and workload_type are required")
	}
	if spec.CPUCores <= 0 || spec.MemoryMiB <= 0 || spec.StorageGiB <= 0 {
		return ApplicationProfile{}, fmt.Errorf("app_spec compute quantities must be positive")
	}
	if (strings.TrimSpace(spec.AcceleratorType) == "") != (spec.AcceleratorCount == 0) {
		return ApplicationProfile{}, fmt.Errorf("app_spec accelerator_type and accelerator_count must be supplied together")
	}
	if spec.AcceleratorCount == 0 && spec.AcceleratorMemoryMiBPerDevice != 0 {
		return ApplicationProfile{}, fmt.Errorf("app_spec accelerator memory requires an accelerator")
	}

	assumptions := make([]string, 0, 3)
	requestPattern := strings.ToUpper(strings.TrimSpace(spec.RequestPattern))
	if requestPattern == "" {
		requestPattern = "ONLINE"
		assumptions = append(assumptions, "Request pattern defaulted to ONLINE.")
	}
	replicasMin := spec.ReplicasMin
	if replicasMin == 0 {
		replicasMin = defaultReplicasMin
		assumptions = append(assumptions, "Minimum replica count defaulted to 1.")
	}
	replicasMax := spec.ReplicasMax
	if replicasMax == 0 {
		replicasMax = maxInt(defaultReplicasMax, replicasMin)
		assumptions = append(assumptions, "Maximum replica count defaulted to at least 2.")
	}
	isolation := strings.TrimSpace(spec.Isolation)
	if isolation == "" {
		isolation = "ONE_MAJOR_APP_PER_VM"
	}

	profile := ApplicationProfile{
		AppID:      strings.TrimSpace(spec.AppID),
		AppVersion: strings.TrimSpace(spec.AppVersion),
		Artifact:   copyArtifact(spec.Artifact),
		Workload: WorkloadProfile{
			TaskType:       strings.TrimSpace(spec.WorkloadType),
			RequestPattern: requestPattern,
			ExpectedRPS:    spec.ExpectedRPS,
		},
		Requirements: ApplicationRequirements{
			Compute: ComputeRequirements{
				CPUCoresMin:   spec.CPUCores,
				MemoryMiBMin:  spec.MemoryMiB,
				StorageGiBMin: spec.StorageGiB,
			},
			Accelerator: AcceleratorRequirements{
				Required:              spec.AcceleratorCount > 0,
				Type:                  strings.ToUpper(strings.TrimSpace(spec.AcceleratorType)),
				CountMin:              spec.AcceleratorCount,
				MemoryMiBMinPerDevice: spec.AcceleratorMemoryMiBPerDevice,
			},
			Deployment: DeploymentRequirements{
				ReplicasMin: replicasMin,
				ReplicasMax: replicasMax,
				Isolation:   isolation,
			},
			SLO: SLORequirements{
				LatencyP95MSMax:  spec.LatencyP95MSMax,
				ThroughputRPSMin: spec.ThroughputRPSMin,
			},
			Cost: CostRequirements{
				Currency:       strings.ToUpper(strings.TrimSpace(spec.CostCurrency)),
				CostPerHourMax: spec.CostPerHourMax,
			},
		},
		Analysis: AnalysisSummary{
			Confidence:  1,
			Assumptions: assumptions,
		},
	}
	if err := bindApplicationMetadata(&profile, request.Application); err != nil {
		return ApplicationProfile{}, err
	}
	if err := validateProfile(profile); err != nil {
		return ApplicationProfile{}, err
	}
	encoded, err := json.Marshal(spec)
	if err != nil {
		return ApplicationProfile{}, fmt.Errorf("encode app_spec fingerprint: %w", err)
	}
	profile.ProfileID = profileID(profile.AppID, profile.AppVersion, string(encoded))
	return profile, nil
}

func profileFromLocalRules(request GenerateRequest) (ApplicationProfile, error) {
	value := strings.TrimSpace(request.Request)
	if value == "" {
		return ApplicationProfile{}, fmt.Errorf("request is required")
	}
	if negativeQuantityPattern.MatchString(value) {
		return ApplicationProfile{}, fmt.Errorf("negative resource quantities are not supported")
	}
	normalized := strings.ToLower(value)
	assumptions := make([]string, 0, 8)

	cpu, ok := extractInteger(normalized,
		`(?i)cpu(?:\s*cores?)?\s*[:=]?\s*(\d+)`,
		`(?i)(\d+)\s*(?:cpu\s*cores?|cores?|코어)`,
	)
	if !ok {
		cpu = defaultCPUCores
		assumptions = append(assumptions, "CPU requirement defaulted to 2 cores.")
	}
	memory, ok := extractCapacityMiB(normalized, "memory|mem|메모리")
	if !ok {
		memory = defaultMemoryMiB
		assumptions = append(assumptions, "Memory requirement defaulted to 4096 MiB.")
	}
	storageMiB, ok := extractCapacityMiB(normalized, "storage|disk|스토리지|디스크")
	storage := defaultStorageGiB
	if ok {
		storage = maxInt(1, storageMiB/1024)
	} else {
		assumptions = append(assumptions, "Storage requirement defaulted to 20 GiB.")
	}

	accelerator := AcceleratorRequirements{}
	gpuCount, countFound := extractInteger(normalized,
		`(?i)gpu\s*[:=]?\s*(\d+)`,
		`(?i)(\d+)\s*(?:gpus?|gpu)`,
	)
	noGPU := strings.Contains(normalized, "cpu-only") || strings.Contains(normalized, "cpu only") ||
		strings.Contains(normalized, "cpu 전용") || strings.Contains(normalized, "no gpu") ||
		strings.Contains(normalized, "without gpu") || strings.Contains(normalized, "gpu 없음") ||
		(countFound && gpuCount == 0)
	if strings.Contains(normalized, "gpu") && !noGPU {
		if !countFound {
			gpuCount = 1
			assumptions = append(assumptions, "GPU count defaulted to 1.")
		}
		gpuMemory, found := extractCapacityMiB(normalized, "vram|gpu\\s*memory|gpu\\s*메모리")
		if !found {
			gpuMemory = 16384
			assumptions = append(assumptions, "GPU memory requirement defaulted to 16384 MiB.")
		}
		accelerator = AcceleratorRequirements{
			Required:              true,
			Type:                  "GPU",
			CountMin:              gpuCount,
			MemoryMiBMinPerDevice: gpuMemory,
		}
	}

	replicas, ok := extractInteger(normalized,
		`(?i)(?:replicas?|레플리카)\s*[:=]?\s*(\d+)`,
		`(?i)(\d+)\s*(?:replicas?|레플리카)`,
	)
	if !ok {
		replicas = defaultReplicasMin
		assumptions = append(assumptions, "Minimum replica count defaulted to 1.")
	}

	appID := strings.TrimSpace(request.Application.AppID)
	if appID == "" {
		appID = "ai-application"
		assumptions = append(assumptions, "Application ID defaulted to ai-application.")
	}
	appVersion := strings.TrimSpace(request.Application.AppVersion)
	if appVersion == "" {
		appVersion = "1.0.0"
		assumptions = append(assumptions, "Application version defaulted to 1.0.0.")
	}
	taskType := "AI_INFERENCE"
	if strings.Contains(normalized, "llm") || strings.Contains(normalized, "qwen") ||
		strings.Contains(normalized, "llama") || strings.Contains(normalized, "gemma") ||
		strings.Contains(normalized, "kimi") || strings.Contains(normalized, "gpt") {
		taskType = "LLM_INFERENCE"
	}
	requestPattern := "ONLINE"
	if strings.Contains(normalized, "batch") || strings.Contains(normalized, "배치") {
		requestPattern = "BATCH"
	} else if strings.Contains(normalized, "stream") || strings.Contains(normalized, "스트리밍") {
		requestPattern = "STREAMING"
	}

	profile := ApplicationProfile{
		AppID:      appID,
		AppVersion: appVersion,
		Artifact:   copyArtifact(request.Application.Artifact),
		Workload: WorkloadProfile{
			TaskType:       taskType,
			RequestPattern: requestPattern,
			ExpectedRPS:    extractFloatOrZero(normalized, `(?i)(\d+(?:\.\d+)?)\s*(?:rps|requests?\s*per\s*second|초당\s*요청)`),
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
			SLO: SLORequirements{
				LatencyP95MSMax: extractFloatOrZero(normalized, `(?i)(?:p95\s*)?(\d+(?:\.\d+)?)\s*ms`),
			},
		},
		Analysis: AnalysisSummary{
			Confidence:  0.7,
			Assumptions: assumptions,
			Warnings:    []string{"Generated by bounded local rules after the configured LLM path was unavailable."},
		},
	}
	if err := validateProfile(profile); err != nil {
		return ApplicationProfile{}, err
	}
	profile.ProfileID = profileID(profile.AppID, profile.AppVersion, value)
	return profile, nil
}

func extractInteger(value string, patterns ...string) (int, bool) {
	for _, pattern := range patterns {
		matches := regexp.MustCompile(pattern).FindStringSubmatch(value)
		if len(matches) < 2 {
			continue
		}
		result, err := strconv.Atoi(matches[1])
		if err == nil {
			return result, true
		}
	}
	return 0, false
}

func extractCapacityMiB(value string, labels string) (int, bool) {
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`(?i)(?:` + labels + `)\s*[:=]?\s*(\d+)\s*(gib|gb|mib|mb)`),
		regexp.MustCompile(`(?i)(\d+)\s*(gib|gb|mib|mb)\s*(?:` + labels + `)`),
	}
	for _, pattern := range patterns {
		matches := pattern.FindStringSubmatch(value)
		if len(matches) < 3 {
			continue
		}
		quantity, err := strconv.Atoi(matches[1])
		if err != nil {
			continue
		}
		switch strings.ToLower(matches[2]) {
		case "gib", "gb":
			return quantity * 1024, true
		case "mib", "mb":
			return quantity, true
		}
	}
	return 0, false
}

func extractFloatOrZero(value string, pattern string) float64 {
	matches := regexp.MustCompile(pattern).FindStringSubmatch(value)
	if len(matches) < 2 {
		return 0
	}
	result, err := strconv.ParseFloat(matches[1], 64)
	if err != nil {
		return 0
	}
	return result
}

func copyArtifact(source *Artifact) *Artifact {
	if source == nil {
		return nil
	}
	copy := *source
	copy.Entrypoint = append([]string(nil), source.Entrypoint...)
	return &copy
}

func maxInt(left int, right int) int {
	if left > right {
		return left
	}
	return right
}
