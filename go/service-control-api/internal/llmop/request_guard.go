package llmop

import (
	"encoding/json"
	"regexp"
	"strings"
	"unicode"

	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

const (
	maxParameterBytes = 64 << 10
	maxParameterDepth = 16
	maxParameterNodes = 1000
	maxParameterKeyRunes = 128
)

var sensitiveParameterKeyMarkers = []string{
	"authorization",
	"auth",
	"apikey",
	"api_key",
	"accesskey",
	"access_key",
	"token",
	"password",
	"secret",
	"credential",
	"privatekey",
	"private_key",
}

var (
	requestIdentifierPattern = regexp.MustCompile(
		`^[A-Za-z0-9][A-Za-z0-9._:/@-]{1,126}[A-Za-z0-9]$`,
	)
	responsibilityParameterKeyMarkers = []string{
		"runtime",
		"runtime_adapter",
		"endpoint",
		"url",
		"command",
		"shell",
		"target",
		"target_profile",
		"target_vm",
		"vm_id",
		"cloud",
		"provider",
		"container",
		"kubernetes",
		"docker",
		"ssh",
	}
)

func ValidateRequest(request Request, policy plannerguard.Policy) plannerguard.Decision {
	if policy.MaxRequestLength > 0 &&
		len([]rune(request.Application.UserRequest)) > policy.MaxRequestLength {
		return plannerguard.Decision{
			Valid:         false,
			Status:        "rejected",
			PolicyVersion: policy.Version,
			Reason:        "natural-language request exceeds the configured length limit",
			Checks: []plannerguard.Check{{
				Name:   "request_length",
				Passed: false,
				Reason: "natural-language request exceeds the configured length limit",
			}},
		}
	}
	if !operationContextEnvelopeBounded(request.OperationContext) {
		return plannerguard.Decision{
			Valid:         false,
			Status:        "rejected",
			PolicyVersion: policy.Version,
			Reason:        "operation context exceeds the bounded observation envelope",
			Checks: []plannerguard.Check{{
				Name:   "bounded_operation_context",
				Passed: false,
				Reason: "operation context exceeds the bounded observation envelope",
			}},
		}
	}
	if !parametersBounded(request.Application.Parameters) {
		return plannerguard.Decision{
			Valid:         false,
			Status:        "rejected",
			PolicyVersion: policy.Version,
			Reason:        "request parameters exceed the bounded JSON envelope",
			Checks: []plannerguard.Check{{
				Name:   "bounded_parameters",
				Passed: false,
				Reason: "request parameters exceed the bounded JSON envelope",
			}},
		}
	}
	if !requestIdentifiersBounded(request) {
		return plannerguard.Decision{
			Valid:         false,
			Status:        "rejected",
			PolicyVersion: policy.Version,
			Reason:        "request identifiers do not satisfy the bounded identifier contract",
			Checks: []plannerguard.Check{{
				Name:   "bounded_identifiers",
				Passed: false,
				Reason: "request identifiers do not satisfy the bounded identifier contract",
			}},
		}
	}
	if !planningConstraintsValid(request.Application.PlanningConstraints) {
		return plannerguard.Decision{
			Valid:         false,
			Status:        "rejected",
			PolicyVersion: policy.Version,
			Reason:        "planning constraints do not satisfy the bounded integration contract",
			Checks: []plannerguard.Check{{
				Name:   "planning_constraints",
				Passed: false,
				Reason: "planning constraints do not satisfy the bounded integration contract",
			}},
		}
	}
	guardUserRequest, _ := redactTrustedIdentifiers(
		request.Application.UserRequest,
		request,
	)

	decision := plannerguard.ValidateRequest(plannerguard.Request{
		NaturalLanguageRequest: guardUserRequest,
		AppVersionID:           request.Application.AppVersionID,
		CandidateID:            request.CandidateID,
		RequestedBy:            request.RequestedBy,
		Parameters:             request.Application.Parameters,
	}, policy)
	if !decision.Valid {
		stabilizeGuardDecision(&decision)
		return decision
	}

	addCheck := func(name string, passed bool, success string, failure string) {
		reason := success
		if !passed {
			reason = failure
		}
		decision.Checks = append(decision.Checks, plannerguard.Check{
			Name:   name,
			Passed: passed,
			Reason: reason,
		})
		if passed || !decision.Valid {
			return
		}
		decision.Valid = false
		decision.Status = "rejected"
		decision.Reason = reason
	}

	addCheck(
		"llm_operation_api_version",
		request.APIVersion == APIVersion,
		"LLM operation API version is supported",
		"api_version must be "+APIVersion,
	)
	addCheck(
		"trace_identifiers",
		strings.TrimSpace(request.RequestID) != "" &&
			strings.TrimSpace(request.CorrelationID) != "",
		"request_id and correlation_id are present",
		"request_id and correlation_id are required",
	)
	addCheck(
		"bounded_identifiers",
		requestIdentifiersBounded(request),
		"request identifiers are bounded ASCII identifiers",
		"request identifiers must use the bounded identifier format",
	)
	addCheck(
		"prepare_only_mode",
		request.Policy.Mode == ModePrepareOnly &&
			strings.TrimSpace(request.Policy.ApprovalReference) == "",
		"request is prepare-only and cannot submit a deployment",
		"only prepare_only without approval_reference is supported until an approval verifier exists",
	)
	_, sensitiveUserValues := redactSensitiveText(request.Application.UserRequest)
	addCheck(
		"sensitive_user_text",
		sensitiveUserValues == 0,
		"user request does not contain credential-like values",
		"user request contains a credential-like value",
	)
	addCheck(
		"responsibility_user_text",
		!forbiddenProposalText.MatchString(guardUserRequest) &&
			!forbiddenOperationalText.MatchString(guardUserRequest) &&
			!forbiddenParameterValueText.MatchString(guardUserRequest),
		"user request stays within the bounded planning responsibility",
		"user request asks for a forbidden runtime, target, endpoint, command, or credential detail",
	)
	addCheck(
		"bounded_parameters",
		parametersBounded(request.Application.Parameters),
		"request parameters are within the bounded JSON envelope",
		"request parameters exceed the JSON byte, depth, or item limit",
	)
	addCheck(
		"sensitive_parameter_keys",
		findSensitiveParameterKey(request.Application.Parameters) == "",
		"request parameters do not contain credential-bearing keys",
		"request parameters contain a credential-bearing key",
	)
	addCheck(
		"sensitive_parameter_values",
		sensitiveValueCount(request.Application.Parameters) == 0,
		"request parameters do not contain credential-like values",
		"request parameters contain a credential-like value",
	)
	addCheck(
		"responsibility_parameter_keys",
		findResponsibilityParameterKey(request.Application.Parameters) == "",
		"request parameters do not select runtime, target, endpoint, or commands",
		"request parameters contain a forbidden responsibility field",
	)
	addCheck(
		"responsibility_parameter_values",
		!containsResponsibilityParameterValue(request.Application.Parameters) &&
			!containsConfiguredForbiddenParameterValue(
				request.Application.Parameters,
				policy.ForbiddenRequestTerms,
			),
		"request parameter values stay within the planning boundary",
		"request parameter values contain a forbidden responsibility term",
	)
	addCheck(
		"observation_scope",
		observationScopeValid(request),
		"observation deployment identifiers match the trusted application scope",
		"observation deployment_id must match application.deployment_id",
	)
	stabilizeGuardDecision(&decision)
	return decision
}

func operationContextEnvelopeBounded(context OperationContext) bool {
	if context.ResourceSnapshot != nil &&
		len(context.ResourceSnapshot.Targets) > defaultMaxTargets {
		return false
	}
	if context.MonitoringSummary != nil {
		if len(context.MonitoringSummary.Summary.RuntimeHealth) > defaultMaxRuntimeHealth ||
			len(context.MonitoringSummary.Summary.Alarms) > defaultMaxAlarms ||
			len(context.MonitoringSummary.Summary.Deployments.ByStatus) > defaultMaxStatusBuckets {
			return false
		}
	}
	return context.DeploymentLogs == nil ||
		len(context.DeploymentLogs.Items) <= defaultMaxLogInputs
}

func parametersBounded(parameters map[string]any) bool {
	if parameters == nil {
		return true
	}
	nodes := 0
	if !parameterValueBounded(parameters, 0, &nodes) {
		return false
	}
	content, err := json.Marshal(parameters)
	return err == nil && len(content) <= maxParameterBytes
}

func parameterValueBounded(value any, depth int, nodes *int) bool {
	if depth > maxParameterDepth || *nodes >= maxParameterNodes {
		return false
	}
	*nodes = *nodes + 1

	switch typed := value.(type) {
	case nil, bool, string,
		float32, float64,
		int, int8, int16, int32, int64,
		uint, uint8, uint16, uint32, uint64,
		json.Number:
		return true
	case map[string]any:
		for key, item := range typed {
			if !boundedParameterKey(key) ||
				!parameterValueBounded(item, depth+1, nodes) {
				return false
			}
		}
		return true
	case map[string]string:
		for key, item := range typed {
			if !boundedParameterKey(key) ||
				!parameterValueBounded(item, depth+1, nodes) {
				return false
			}
		}
		return true
	case []any:
		for _, item := range typed {
			if !parameterValueBounded(item, depth+1, nodes) {
				return false
			}
		}
		return true
	case []string:
		for _, item := range typed {
			if !parameterValueBounded(item, depth+1, nodes) {
				return false
			}
		}
		return true
	case []map[string]any:
		for _, item := range typed {
			if !parameterValueBounded(item, depth+1, nodes) {
				return false
			}
		}
		return true
	case []map[string]string:
		for _, item := range typed {
			if !parameterValueBounded(item, depth+1, nodes) {
				return false
			}
		}
		return true
	default:
		return false
	}
}

func boundedParameterKey(value string) bool {
	if value != strings.TrimSpace(value) || value == "" ||
		len([]rune(value)) > maxParameterKeyRunes {
		return false
	}
	for _, character := range value {
		if unicode.IsControl(character) {
			return false
		}
	}
	return true
}

func containsResponsibilityParameterValue(value any) bool {
	switch typed := value.(type) {
	case string:
		return forbiddenProposalText.MatchString(typed) ||
			forbiddenOperationalText.MatchString(typed) ||
			forbiddenParameterValueText.MatchString(typed)
	case map[string]any:
		for _, item := range typed {
			if containsResponsibilityParameterValue(item) {
				return true
			}
		}
	case map[string]string:
		for _, item := range typed {
			if containsResponsibilityParameterValue(item) {
				return true
			}
		}
	case []any:
		for _, item := range typed {
			if containsResponsibilityParameterValue(item) {
				return true
			}
		}
	case []string:
		for _, item := range typed {
			if containsResponsibilityParameterValue(item) {
				return true
			}
		}
	case []map[string]any:
		for _, item := range typed {
			if containsResponsibilityParameterValue(item) {
				return true
			}
		}
	case []map[string]string:
		for _, item := range typed {
			if containsResponsibilityParameterValue(item) {
				return true
			}
		}
	}
	return false
}

func containsConfiguredForbiddenParameterValue(value any, terms []string) bool {
	switch typed := value.(type) {
	case string:
		return containsConfiguredForbiddenTerm(typed, terms)
	case map[string]any:
		for _, item := range typed {
			if containsConfiguredForbiddenParameterValue(item, terms) {
				return true
			}
		}
	case map[string]string:
		for _, item := range typed {
			if containsConfiguredForbiddenParameterValue(item, terms) {
				return true
			}
		}
	case []any:
		for _, item := range typed {
			if containsConfiguredForbiddenParameterValue(item, terms) {
				return true
			}
		}
	case []string:
		for _, item := range typed {
			if containsConfiguredForbiddenParameterValue(item, terms) {
				return true
			}
		}
	case []map[string]any:
		for _, item := range typed {
			if containsConfiguredForbiddenParameterValue(item, terms) {
				return true
			}
		}
	case []map[string]string:
		for _, item := range typed {
			if containsConfiguredForbiddenParameterValue(item, terms) {
				return true
			}
		}
	}
	return false
}

func stabilizeGuardDecision(decision *plannerguard.Decision) {
	if decision == nil || decision.Valid {
		return
	}
	firstFailure := "request failed the bounded planner guard"
	for index := range decision.Checks {
		if decision.Checks[index].Passed {
			continue
		}
		decision.Checks[index].Reason = stableGuardFailureReason(
			decision.Checks[index].Name,
		)
		if firstFailure == "request failed the bounded planner guard" {
			firstFailure = decision.Checks[index].Reason
		}
	}
	decision.Reason = firstFailure
}

func stableGuardFailureReason(name string) string {
	switch name {
	case "required_fields":
		return "required planner fields are missing"
	case "request_length":
		return "natural-language request exceeds the configured length limit"
	case "requester_allowlist":
		return "requester is not allowed by the planner guard policy"
	case "vm_only_scope":
		return "request is outside the bounded planner scope"
	case "sensitive_parameters", "sensitive_parameter_keys", "sensitive_parameter_values":
		return "request parameters contain forbidden credential material"
	case "llm_operation_api_version":
		return "LLM operation API version is not supported"
	case "trace_identifiers", "bounded_identifiers":
		return "request identifiers do not satisfy the bounded identifier contract"
	case "prepare_only_mode":
		return "request is outside the prepare-only approval boundary"
	case "sensitive_user_text":
		return "user request contains credential-like material"
	case "responsibility_user_text":
		return "user request crosses the bounded planning responsibility"
	case "bounded_parameters":
		return "request parameters exceed the bounded JSON envelope"
	case "responsibility_parameter_keys", "responsibility_parameter_values":
		return "request parameters cross the bounded planning responsibility"
	case "observation_scope":
		return "operation observations do not match the trusted deployment scope"
	case "planning_constraints":
		return "planning constraints do not satisfy the bounded integration contract"
	default:
		return "request failed the bounded planner guard"
	}
}

func requestIdentifiersBounded(request Request) bool {
	strongIdentifiers := []string{
		request.RequestID,
		request.CorrelationID,
		request.TraceID,
		request.CandidateID,
		request.RequestedBy,
		request.Application.AppVersionID,
		request.Application.DeploymentID,
	}
	if constraints := request.Application.PlanningConstraints; constraints != nil {
		strongIdentifiers = append(
			strongIdentifiers,
			constraints.SourceProfileID,
			constraints.SourceRecommendationID,
		)
	}
	for _, identifier := range strongIdentifiers {
		if identifier != "" &&
			(!boundedRequestIdentifier(identifier) || len([]rune(identifier)) < 8) {
			return false
		}
	}
	if identifier := request.Application.TargetProfileID; identifier != "" &&
		!boundedRequestIdentifier(identifier) {
		return false
	}
	return true
}

func planningConstraintsValid(constraints *PlanningConstraints) bool {
	if constraints == nil {
		return true
	}
	if strings.TrimSpace(constraints.SourceProfileID) == "" ||
		strings.TrimSpace(constraints.SourceRecommendationID) == "" ||
		!constraints.RecommendationFeasible ||
		constraints.CPUCoresMin == 0 || constraints.CPUCoresMin > maxCPUCount ||
		constraints.MemoryMiBMin == 0 || constraints.MemoryMiBMin > maxMemoryMi ||
		constraints.GPUCountMin > maxGPUCount ||
		constraints.StorageGiBMin == 0 ||
		constraints.StorageGiBMin > maxStorageMi/1024 {
		return false
	}
	if constraints.GPUCountMin > 0 {
		if constraints.Accelerator != "nvidia" {
			return false
		}
	} else if constraints.Accelerator != "none" {
		return false
	}
	return recommendedResourcesValid(constraints)
}

func recommendedResourcesValid(constraints *PlanningConstraints) bool {
	recommended := constraints.RecommendedResources
	if recommended == nil {
		return true
	}
	if recommended.CPUCores == 0 || recommended.CPUCores > maxCPUCount ||
		recommended.MemoryMiB == 0 || recommended.MemoryMiB > maxMemoryMi ||
		recommended.GPUCount > maxGPUCount ||
		recommended.StorageGiB == 0 || recommended.StorageGiB > maxStorageMi/1024 ||
		recommended.CPUCores < constraints.CPUCoresMin ||
		recommended.MemoryMiB < constraints.MemoryMiBMin ||
		recommended.GPUCount < constraints.GPUCountMin ||
		recommended.StorageGiB < constraints.StorageGiBMin {
		return false
	}
	if recommended.GPUCount > 0 {
		return recommended.Accelerator == "nvidia" && constraints.Accelerator == "nvidia"
	}
	return recommended.Accelerator == "none" && constraints.Accelerator == "none"
}

func boundedRequestIdentifier(value string) bool {
	return value == strings.TrimSpace(value) && requestIdentifierPattern.MatchString(value)
}

func findSensitiveParameterKey(value any) string {
	return findParameterKey(value, sensitiveParameterKey)
}

func findResponsibilityParameterKey(value any) string {
	return findParameterKey(value, responsibilityParameterKey)
}

func findParameterKey(value any, forbidden func(string) bool) string {
	switch typed := value.(type) {
	case map[string]any:
		for key, item := range typed {
			if forbidden(key) {
				return key
			}
			if found := findParameterKey(item, forbidden); found != "" {
				return found
			}
		}
	case map[string]string:
		for key := range typed {
			if forbidden(key) {
				return key
			}
		}
	case []any:
		for _, item := range typed {
			if found := findParameterKey(item, forbidden); found != "" {
				return found
			}
		}
	case []map[string]any:
		for _, item := range typed {
			if found := findParameterKey(item, forbidden); found != "" {
				return found
			}
		}
	case []map[string]string:
		for _, item := range typed {
			if found := findParameterKey(item, forbidden); found != "" {
				return found
			}
		}
	}
	return ""
}

func responsibilityParameterKey(key string) bool {
	normalized := normalizeSensitiveParameterKey(key)
	if normalized == "" {
		return false
	}
	padded := "_" + normalized + "_"
	for _, marker := range responsibilityParameterKeyMarkers {
		if strings.Contains(padded, "_"+marker+"_") {
			return true
		}
	}
	return false
}

func sensitiveParameterKey(key string) bool {
	normalized := normalizeSensitiveParameterKey(key)
	if normalized == "" {
		return false
	}
	padded := "_" + normalized + "_"
	for _, marker := range sensitiveParameterKeyMarkers {
		if strings.Contains(padded, "_"+marker+"_") {
			return true
		}
	}
	return false
}

func normalizeSensitiveParameterKey(value string) string {
	var builder strings.Builder
	previousWasSeparator := true
	previousWasLowerOrDigit := false
	for _, character := range strings.TrimSpace(value) {
		if unicode.IsLetter(character) || unicode.IsDigit(character) {
			if unicode.IsUpper(character) && previousWasLowerOrDigit && !previousWasSeparator {
				builder.WriteByte('_')
			}
			builder.WriteRune(unicode.ToLower(character))
			previousWasSeparator = false
			previousWasLowerOrDigit = unicode.IsLower(character) || unicode.IsDigit(character)
			continue
		}
		if !previousWasSeparator && builder.Len() > 0 {
			builder.WriteByte('_')
		}
		previousWasSeparator = true
		previousWasLowerOrDigit = false
	}
	return strings.Trim(builder.String(), "_")
}

func sensitiveValueCount(value any) int {
	switch typed := value.(type) {
	case string:
		_, count := redactSensitiveText(typed)
		return count
	case map[string]any:
		total := 0
		for _, item := range typed {
			total += sensitiveValueCount(item)
		}
		return total
	case map[string]string:
		total := 0
		for _, item := range typed {
			total += sensitiveValueCount(item)
		}
		return total
	case []any:
		total := 0
		for _, item := range typed {
			total += sensitiveValueCount(item)
		}
		return total
	case []string:
		total := 0
		for _, item := range typed {
			total += sensitiveValueCount(item)
		}
		return total
	default:
		return 0
	}
}

func observationScopeValid(request Request) bool {
	trustedDeploymentID := strings.TrimSpace(request.Application.DeploymentID)
	if trustedDeploymentID == "" {
		if request.OperationContext.MetricsSummary != nil {
			return false
		}
		if logs := request.OperationContext.DeploymentLogs; logs != nil && len(logs.Items) > 0 {
			return false
		}
		if monitoring := request.OperationContext.MonitoringSummary; monitoring != nil &&
			len(monitoring.Summary.Alarms) > 0 {
			return false
		}
		return true
	}
	if metrics := request.OperationContext.MetricsSummary; metrics != nil {
		deploymentID := strings.TrimSpace(metrics.DeploymentID)
		if deploymentID != trustedDeploymentID {
			return false
		}
	}
	if logs := request.OperationContext.DeploymentLogs; logs != nil {
		for _, item := range logs.Items {
			deploymentID := strings.TrimSpace(item.DeploymentID)
			if deploymentID != trustedDeploymentID {
				return false
			}
		}
	}
	if monitoring := request.OperationContext.MonitoringSummary; monitoring != nil {
		for _, alarm := range monitoring.Summary.Alarms {
			deploymentID := strings.TrimSpace(alarm.LatestDeploymentID)
			if deploymentID != trustedDeploymentID {
				return false
			}
		}
	}
	return true
}
