package llmop

import (
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

var (
	cpuResourceLabel = regexp.MustCompile(`(?i)(?:CPU|씨피유)`)
	explicitCPUExact = regexp.MustCompile(
		`(?i)(?:CPU|씨피유)\s*(?:은|는|이|가|을|를)?(?:\s*[:=]\s*|\s+)([0-9]+)(?:\s*(?:개|코어|cores?))?(?:이고|이며|은|는|이|가|을|를|와|과)?`,
	)
	gpuResourceLabel = regexp.MustCompile(`(?i)(?:GPU|지피유)`)
	explicitGPUExact = regexp.MustCompile(
		`(?i)(?:NVIDIA\s+)?(?:GPU|지피유)\s*(?:은|는|이|가|을|를)?(?:\s*[:=]\s*|\s+)([0-9]+)(?:\s*(?:개|장|devices?))?(?:이고|이며|은|는|이|가|을|를|와|과)?`,
	)
	memoryResourceLabel = regexp.MustCompile(`(?i)(?:memory|메모리)`)
	explicitMemoryExact = regexp.MustCompile(
		`(?i)(?:memory|메모리)\s*(?:은|는|이|가|을|를)?(?:\s*[:=]\s*|\s+)([0-9]+)\s*(Mi|Gi|Ti)(?:이고|이며|은|는|이|가|을|를|와|과)?`,
	)
	storageResourceLabel = regexp.MustCompile(`(?i)(?:storage|스토리지|저장소)`)
	explicitStorageExact = regexp.MustCompile(
		`(?i)(?:storage|스토리지|저장소)\s*(?:은|는|이|가|을|를)?(?:\s*[:=]\s*|\s+)([0-9]+)\s*(Mi|Gi|Ti)(?:이고|이며|은|는|이|가|을|를|와|과)?`,
	)
	approximateResourcePrefix = regexp.MustCompile(
		`(?i)(?:약|대략|최소한|최대한|최소|최대|적어도|minimum|maximum|approximately|roughly|about|around|at\s+least|at\s+most|up\s+to)\s*$`,
	)
	approximateResourceSuffix = regexp.MustCompile(
		`(?i)^\s*(?:~\s*[0-9]|[-–—]\s*[0-9]|\.\s*[0-9]|,\s*[0-9]|/\s*[0-9]|\+|정도|내외|가량|쯤|부터|이상|이하|초과|미만|또는|approximately\b|roughly\b|about\b|around\b|or\b|to\b)`,
	)
)

type exactUint struct {
	Set   bool
	Value uint64
}

type explicitResourceIntent struct {
	CPU       exactUint
	GPU       exactUint
	MemoryMi  exactUint
	StorageMi exactUint
}

type semanticGuardError struct {
	code  string
	field string
}

func (err *semanticGuardError) Error() string {
	return "proposal failed deterministic request-alignment safeguards"
}

func validateProposalSemantics(
	request Request,
	normalized NormalizedContext,
	proposal Proposal,
) error {
	if proposal.Action != ActionCreateManifest {
		return nil
	}
	if proposal.Resources == nil {
		return &semanticGuardError{code: "missing_resources", field: "resources"}
	}
	intent, err := extractExactResourceIntent(request.Application.UserRequest)
	if err != nil {
		return err
	}
	if request.Application.PlanningConstraints == nil && !intent.complete() {
		return &semanticGuardError{code: "incomplete_explicit_intent", field: "resources"}
	}
	actual, err := resourceValues(*proposal.Resources)
	if err != nil {
		return err
	}
	if constraints := request.Application.PlanningConstraints; constraints != nil {
		if err := compareMinimumIntent(intent, actual); err != nil {
			return err
		}
		if actual.CPU.Value < constraints.CPUCoresMin ||
			actual.MemoryMi.Value < constraints.MemoryMiBMin ||
			actual.GPU.Value < constraints.GPUCountMin ||
			actual.StorageMi.Value < constraints.StorageGiBMin*1024 {
			return &semanticGuardError{code: "below_planning_minimum", field: "resources"}
		}
		if proposal.Accelerator != constraints.Accelerator {
			return &semanticGuardError{code: "accelerator_mismatch", field: "accelerator"}
		}
		if err := compareRecommendedResources(constraints.RecommendedResources, actual, proposal.Accelerator); err != nil {
			return err
		}
	} else if err := compareExactIntent(intent, actual); err != nil {
		return err
	}
	if (actual.GPU.Value > 0 && proposal.Accelerator != "nvidia") ||
		(actual.GPU.Value == 0 && proposal.Accelerator != "none") {
		return &semanticGuardError{code: "accelerator_mismatch", field: "accelerator"}
	}
	return validateFreshResourceReadiness(
		normalized,
		request.Application.TargetProfileID,
		actual.GPU.Value > 0,
	)
}

func (intent explicitResourceIntent) complete() bool {
	return intent.CPU.Set && intent.GPU.Set &&
		intent.MemoryMi.Set && intent.StorageMi.Set
}

func extractExactResourceIntent(text string) (explicitResourceIntent, error) {
	cpu, err := extractExactCount(text, cpuResourceLabel, explicitCPUExact, "cpu")
	if err != nil {
		return explicitResourceIntent{}, err
	}
	gpu, err := extractExactCount(text, gpuResourceLabel, explicitGPUExact, "gpu")
	if err != nil {
		return explicitResourceIntent{}, err
	}
	memory, err := extractExactQuantity(
		text,
		memoryResourceLabel,
		explicitMemoryExact,
		"memory",
	)
	if err != nil {
		return explicitResourceIntent{}, err
	}
	storage, err := extractExactQuantity(
		text,
		storageResourceLabel,
		explicitStorageExact,
		"storage",
	)
	if err != nil {
		return explicitResourceIntent{}, err
	}
	return explicitResourceIntent{
		CPU:       cpu,
		GPU:       gpu,
		MemoryMi:  memory,
		StorageMi: storage,
	}, nil
}

func extractExactCount(
	text string,
	labelPattern *regexp.Regexp,
	exactPattern *regexp.Regexp,
	field string,
) (exactUint, error) {
	result := exactUint{}
	matches := exactPattern.FindAllStringSubmatchIndex(text, -1)
	if standaloneResourceLabelCount(text, labelPattern) != len(matches) {
		return exactUint{}, &semanticGuardError{code: "ambiguous_explicit_intent", field: field}
	}
	for _, indexes := range matches {
		if len(indexes) < 4 || !standaloneResourceMatch(text, indexes[0], indexes[1]) ||
			resourceMatchIsApproximate(text, indexes[0], indexes[1]) {
			return exactUint{}, &semanticGuardError{code: "ambiguous_explicit_intent", field: field}
		}
		value, err := strconv.ParseUint(text[indexes[2]:indexes[3]], 10, 64)
		if err != nil {
			return exactUint{}, &semanticGuardError{code: "invalid_explicit_intent", field: field}
		}
		if result.Set && result.Value != value {
			return exactUint{}, &semanticGuardError{code: "conflicting_explicit_intent", field: field}
		}
		result = exactUint{Set: true, Value: value}
	}
	return result, nil
}

func extractExactQuantity(
	text string,
	labelPattern *regexp.Regexp,
	exactPattern *regexp.Regexp,
	field string,
) (exactUint, error) {
	result := exactUint{}
	matches := exactPattern.FindAllStringSubmatchIndex(text, -1)
	if standaloneResourceLabelCount(text, labelPattern) != len(matches) {
		return exactUint{}, &semanticGuardError{code: "ambiguous_explicit_intent", field: field}
	}
	for _, indexes := range matches {
		if len(indexes) < 6 || !standaloneResourceMatch(text, indexes[0], indexes[1]) ||
			resourceMatchIsApproximate(text, indexes[0], indexes[1]) {
			return exactUint{}, &semanticGuardError{code: "ambiguous_explicit_intent", field: field}
		}
		amount, err := strconv.ParseUint(text[indexes[2]:indexes[3]], 10, 64)
		if err != nil {
			return exactUint{}, &semanticGuardError{code: "invalid_explicit_intent", field: field}
		}
		value, err := amountWithUnitToMi(amount, text[indexes[4]:indexes[5]])
		if err != nil {
			return exactUint{}, &semanticGuardError{code: "invalid_explicit_intent", field: field}
		}
		if result.Set && result.Value != value {
			return exactUint{}, &semanticGuardError{code: "conflicting_explicit_intent", field: field}
		}
		result = exactUint{Set: true, Value: value}
	}
	return result, nil
}

func standaloneResourceLabelCount(text string, pattern *regexp.Regexp) int {
	count := 0
	for _, indexes := range pattern.FindAllStringIndex(text, -1) {
		if indexes[0] > 0 {
			character, _ := utf8.DecodeLastRuneInString(text[:indexes[0]])
			if unicode.IsLetter(character) || unicode.IsDigit(character) || character == '_' {
				continue
			}
		}
		count++
	}
	return count
}

func standaloneResourceMatch(text string, start int, end int) bool {
	if start > 0 {
		character, _ := utf8.DecodeLastRuneInString(text[:start])
		if unicode.IsLetter(character) || unicode.IsDigit(character) || character == '_' {
			return false
		}
	}
	if end < len(text) {
		character, _ := utf8.DecodeRuneInString(text[end:])
		if unicode.IsLetter(character) || unicode.IsDigit(character) || character == '_' {
			return false
		}
	}
	return true
}

func resourceMatchIsApproximate(text string, start int, end int) bool {
	// The request is already size-bounded by the request guard. Inspect the
	// complete prefix and suffix so byte offsets from regexp never split a
	// multi-byte Korean rune while creating a look-around window.
	return approximateResourcePrefix.MatchString(text[:start]) ||
		approximateResourceSuffix.MatchString(text[end:])
}

func resourceValues(resources appdeploy.ResourceRequirements) (explicitResourceIntent, error) {
	cpu, err := strconv.ParseUint(resources.CPU, 10, 64)
	if err != nil {
		return explicitResourceIntent{}, &semanticGuardError{code: "invalid_proposal_resource", field: "cpu"}
	}
	gpu, err := strconv.ParseUint(resources.GPU, 10, 64)
	if err != nil {
		return explicitResourceIntent{}, &semanticGuardError{code: "invalid_proposal_resource", field: "gpu"}
	}
	memory, err := quantityToMi(resources.Memory)
	if err != nil {
		return explicitResourceIntent{}, err
	}
	storage, err := quantityToMi(resources.Storage)
	if err != nil {
		return explicitResourceIntent{}, err
	}
	return explicitResourceIntent{
		CPU:       exactUint{Set: true, Value: cpu},
		GPU:       exactUint{Set: true, Value: gpu},
		MemoryMi:  exactUint{Set: true, Value: memory},
		StorageMi: exactUint{Set: true, Value: storage},
	}, nil
}

func quantityToMi(value string) (uint64, error) {
	matches := quantityPattern.FindStringSubmatch(value)
	if len(matches) != 3 {
		return 0, &semanticGuardError{code: "invalid_proposal_resource", field: "quantity"}
	}
	amount, err := strconv.ParseUint(matches[1], 10, 64)
	if err != nil {
		return 0, &semanticGuardError{code: "invalid_proposal_resource", field: "quantity"}
	}
	return amountWithUnitToMi(amount, matches[2])
}

func amountWithUnitToMi(amount uint64, unit string) (uint64, error) {
	factor := uint64(1)
	switch strings.ToLower(unit) {
	case "mi":
	case "gi":
		factor = 1024
	case "ti":
		factor = 1024 * 1024
	default:
		return 0, &semanticGuardError{code: "invalid_resource_unit", field: "quantity"}
	}
	if amount > ^uint64(0)/factor {
		return 0, &semanticGuardError{code: "resource_overflow", field: "quantity"}
	}
	return amount * factor, nil
}

func compareExactIntent(expected explicitResourceIntent, actual explicitResourceIntent) error {
	checks := []struct {
		field    string
		expected exactUint
		actual   exactUint
	}{
		{field: "cpu", expected: expected.CPU, actual: actual.CPU},
		{field: "gpu", expected: expected.GPU, actual: actual.GPU},
		{field: "memory", expected: expected.MemoryMi, actual: actual.MemoryMi},
		{field: "storage", expected: expected.StorageMi, actual: actual.StorageMi},
	}
	for _, check := range checks {
		if check.expected.Set && check.expected.Value != check.actual.Value {
			return &semanticGuardError{code: "explicit_intent_mismatch", field: check.field}
		}
	}
	return nil
}

func compareMinimumIntent(expected explicitResourceIntent, actual explicitResourceIntent) error {
	checks := []struct {
		field    string
		expected exactUint
		actual   exactUint
	}{
		{field: "cpu", expected: expected.CPU, actual: actual.CPU},
		{field: "memory", expected: expected.MemoryMi, actual: actual.MemoryMi},
		{field: "storage", expected: expected.StorageMi, actual: actual.StorageMi},
	}
	for _, check := range checks {
		if check.expected.Set && check.expected.Value == 0 {
			return &semanticGuardError{code: "invalid_explicit_intent", field: check.field}
		}
		if check.expected.Set && check.actual.Value < check.expected.Value {
			return &semanticGuardError{code: "explicit_intent_below_request", field: check.field}
		}
	}
	if expected.GPU.Set {
		if expected.GPU.Value == 0 && actual.GPU.Value != 0 {
			return &semanticGuardError{code: "explicit_intent_mismatch", field: "gpu"}
		}
		if actual.GPU.Value < expected.GPU.Value {
			return &semanticGuardError{code: "explicit_intent_below_request", field: "gpu"}
		}
	}
	return nil
}

func compareRecommendedResources(
	recommended *RecommendedResources,
	actual explicitResourceIntent,
	accelerator string,
) error {
	if recommended == nil {
		return nil
	}
	if actual.CPU.Value != recommended.CPUCores ||
		actual.MemoryMi.Value != recommended.MemoryMiB ||
		actual.GPU.Value != recommended.GPUCount ||
		actual.StorageMi.Value != recommended.StorageGiB*1024 {
		return &semanticGuardError{code: "recommended_resource_mismatch", field: "resources"}
	}
	if accelerator != recommended.Accelerator {
		return &semanticGuardError{code: "recommended_resource_mismatch", field: "accelerator"}
	}
	return nil
}

func validateFreshResourceReadiness(
	normalized NormalizedContext,
	targetProfileID string,
	gpuRequired bool,
) error {
	snapshot := normalized.ResourceSnapshot
	if snapshot == nil {
		return nil
	}
	for _, target := range snapshot.Targets {
		if targetProfileID != "" && target.TargetProfileID != targetProfileID {
			continue
		}
		if target.CPUAvailable && target.MemoryAvailable && target.StorageAvailable &&
			(!gpuRequired || target.GPUAvailable) {
			return nil
		}
	}
	return &semanticGuardError{code: "fresh_resource_readiness_conflict", field: "resource_snapshot"}
}
