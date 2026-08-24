package llmop

import (
	"encoding/json"
	"regexp"
	"strings"
	"unicode"

	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

const (
	maxUserRequestRunes = 8000
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
	promptInjectionText = regexp.MustCompile(
		`(?i)(?:\b(?:ignore|disregard|override|bypass)\b[\s\S]{0,80}\b(?:previous|prior|system|developer)\b[\s\S]{0,40}\b(?:instructions?|prompts?|rules?)\b|\b(?:reveal|show|print|expose)\b[\s\S]{0,60}\b(?:system|developer)\b[\s\S]{0,20}\bprompt\b|(?:이전|앞선|기존|시스템|개발자)[\s\S]{0,40}(?:지시|명령|규칙|프롬프트)[\s\S]{0,20}(?:무시|우회|덮어쓰|재정의)|(?:시스템|개발자)[\s\S]{0,20}프롬프트[\s\S]{0,20}(?:공개|출력|보여|노출))`,
	)
	resourceNegationAfterText = regexp.MustCompile(
		`(?i)(?:CPU|GPU|memory|storage|메모리|저장소)[^\r\n,;]{0,40}(?:do\s+not\s+use|not\s+needed|instead\s+of|rather\s+than|exclude|사용하지|쓰지|제외|아닌|아니라|없이|말고|대신|불필요|필요\s*없)`,
	)
	resourceNegationBeforeText = regexp.MustCompile(
		`(?i)(?:do\s+not\s+use|don't\s+use|without|instead\s+of|rather\s+than|말고|대신|아닌|아니라)[^\r\n,;]{0,40}(?:CPU|GPU|memory|storage|메모리|저장소)`,
	)
	resourceConditionalText = regexp.MustCompile(
		`(?i)(?:CPU|GPU|memory|storage|메모리|저장소)[^\r\n,;]{0,40}(?:if\s+possible|prefer(?:red)?|ideally|optional|가능하면|되면|좋겠|선호|가급적|여유되면|있으면)|(?:if\s+possible|prefer(?:red)?|ideally|optional|가능하면|가급적|여유되면)[^\r\n,;]{0,40}(?:CPU|GPU|memory|storage|메모리|저장소)`,
	)
	unsupportedResourceText = regexp.MustCompile(
		`(?i)\b(?:NPU|TPU|FPGA|ASIC)\b|\b(?:AMD|ROCm|MI[0-9]{2,4}|Intel|Gaudi)[^\r\n,;]{0,20}\b(?:GPU|accelerator)\b|\b(?:GPU|accelerator)[^\r\n,;]{0,20}\b(?:AMD|ROCm|MI[0-9]{2,4}|Intel|Gaudi)\b|\b(?:H100|H200|H800|A100|A800|A40|A30|L4|L40S?|T4|V100|P100|P40)\b|\bRTX[ -]?[0-9]{3,4}(?:[ -]?Ti)?\b|\bTesla(?:[ -]+[A-Za-z0-9.-]+)?\b|\b(?:Xeon|EPYC|ARM64|AARCH64|X86_64|CUDA|cuDNN)\b|\bcompute[ _-]+capability\b|\b(?:GPU[ _-]+)?driver[ _-]+version\b|(?:엔피유|티피유|에프피지에이|드라이버\s*버전)`,
	)
	unsupportedRequirementText = regexp.MustCompile(
		`(?i)\b(?:replicas?|VRAM|GPU\s+memory|device\s+memory|SLO|cost|budget|throughput|RPS|TPS|p95|p99)\b|(?:레플리카|복제본|GPU\s*메모리|비용|예산|처리량)|(?:latency|response(?:\s+time)?|지연(?:시간)?|응답(?:시간)?)[^\r\n,;]{0,40}(?:낮|줄|최대|최소|이하|미만|이내|안쪽|보장|유지|목표|limit|under|below|less|max|min)|(?:초당\s*[0-9]+\s*(?:건|요청)|requests?\s+per\s+second)`,
	)
	unsupportedTopologyText = regexp.MustCompile(
		`(?i)\b(?:[2-9]|[1-9][0-9]+)\s*(?:instances?|nodes?|vms?)\b|\b(?:two|three|four|five|multiple)\s+(?:instances?|nodes?|vms?)\b|(?:인스턴스|노드|VM|가상\s*머신)\s*(?:[2-9][0-9]*|두|세|네|여러)\s*(?:개|대)?|(?:[2-9][0-9]*|두|세|네|여러)\s*(?:개|대)의?\s*(?:인스턴스|노드|VM|가상\s*머신)|\bscale[ -]?out\b|\bhigh[ -]?availability\b|\bmulti[ -]?node\b|스케일\s*아웃|고가용성|다중\s*노드`,
	)
	unsupportedDeploymentDetailText = regexp.MustCompile(
		`(?i)\b(?:Ubuntu|Debian|Windows\s+Server|RHEL|Rocky\s+Linux|AlmaLinux|operating\s+system|OS|port|TCP|UDP|VPC|subnet|firewall|region|availability\s+zone|volume|mount|affinity|topology)\b|\b(?:container|machine|boot|OS)[ _-]+image\b|(?:운영체제|OS\s*이미지|컨테이너\s*이미지|포트|네트워크|서브넷|방화벽|리전|가용\s*영역|볼륨|마운트|어피니티|토폴로지)`,
	)
	targetSelectionText = regexp.MustCompile(
		`(?i)(?:\btarget[-_](?:[A-Za-z0-9._:@/-]*[0-9][A-Za-z0-9._:@/-]*|[A-Za-z0-9]+[-_.:/@][A-Za-z0-9._:@/-]+)|\b(?:target(?:[ _-]+profile)?|vm|virtual[ _-]+machine)[ \t]+(?:[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9._:@/-]*|[A-Za-z0-9]+[-_.:/@][A-Za-z0-9._:@/-]+)|(?:타겟|대상\s*(?:프로파일|VM|가상\s*머신)|가상\s*머신)[ \t]*(?:[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9._:@/-]*|[A-Za-z0-9]+[-_.:/@][A-Za-z0-9._:@/-]+))[^\r\n,;]{0,32}(?:\b(?:select|choose|pick|use|assign|pin)\b|선택|지정|골라|사용)|(?:\b(?:select|choose|pick|use|assign|pin)\b|선택|지정|골라|사용)[^\r\n,;]{0,32}(?:\btarget[-_](?:[A-Za-z0-9._:@/-]*[0-9][A-Za-z0-9._:@/-]*|[A-Za-z0-9]+[-_.:/@][A-Za-z0-9._:@/-]+)|\b(?:target(?:[ _-]+profile)?|vm|virtual[ _-]+machine)[ \t]+(?:[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9._:@/-]*|[A-Za-z0-9]+[-_.:/@][A-Za-z0-9._:@/-]+)|(?:타겟|대상\s*(?:프로파일|VM|가상\s*머신)|가상\s*머신)[ \t]*(?:[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9._:@/-]*|[A-Za-z0-9]+[-_.:/@][A-Za-z0-9._:@/-]+))`,
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
	requestLimit := maxUserRequestRunes
	if policy.MaxRequestLength > 0 && policy.MaxRequestLength < requestLimit {
		requestLimit = policy.MaxRequestLength
	}
	if len([]rune(request.Application.UserRequest)) > requestLimit {
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
		"prompt_injection",
		!promptInjectionText.MatchString(guardUserRequest) &&
			!containsPromptInjectionValue(request.Application.Parameters) &&
			!containsObservationPromptInjection(request.OperationContext),
		"request and observation text do not contain prompt-control instructions",
		"request or observation text contains prompt-control instructions",
	)
	addCheck(
		"input_text_hygiene",
		promptInputTextSafe(request.Application.UserRequest) &&
			!operationContextTextMatches(request.OperationContext, func(value string) bool {
				return !promptInputTextSafe(value)
			}),
		"request and observation text do not contain control or directional formatting characters",
		"request or observation text contains forbidden control or directional formatting characters",
	)
	addCheck(
		"resource_intent_grammar",
		!resourceNegationAfterText.MatchString(guardUserRequest) &&
			!resourceNegationBeforeText.MatchString(guardUserRequest) &&
			!resourceConditionalText.MatchString(guardUserRequest),
		"resource intent uses the supported affirmative exact-value grammar",
		"resource intent uses unsupported negation, contrast, conditional, or preference grammar",
	)
	addCheck(
		"supported_requirement_scope",
		!unsupportedResourceText.MatchString(guardUserRequest) &&
			!unsupportedRequirementText.MatchString(guardUserRequest) &&
			!unsupportedTopologyText.MatchString(guardUserRequest) &&
			!unsupportedDeploymentDetailText.MatchString(guardUserRequest),
		"request contains only requirements represented by the LLM operation contract",
		"request contains a hardware, topology, SLO, cost, or deployment requirement that cannot be represented",
	)
	addCheck(
		"responsibility_user_text",
		!forbiddenProposalText.MatchString(guardUserRequest) &&
			!forbiddenOperationalText.MatchString(guardUserRequest) &&
			!forbiddenParameterValueText.MatchString(guardUserRequest) &&
			!targetSelectionText.MatchString(guardUserRequest),
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
		"manifest_parameters_disabled",
		len(request.Application.Parameters) == 0,
		"no untyped manifest parameters were supplied",
		"untyped manifest parameters are disabled until AppDeploy publishes a field allowlist",
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
	remainingTextBytes := maxParameterBytes
	if !parameterTextBytesBounded(parameters, &remainingTextBytes) {
		return false
	}
	content, err := json.Marshal(parameters)
	return err == nil && len(content) <= maxParameterBytes
}

func parameterTextBytesBounded(value any, remaining *int) bool {
	consume := func(text string) bool {
		if len(text) > *remaining {
			return false
		}
		*remaining -= len(text)
		return true
	}
	switch typed := value.(type) {
	case string:
		return consume(typed)
	case json.Number:
		return consume(string(typed))
	case map[string]any:
		for key, item := range typed {
			if !consume(key) || !parameterTextBytesBounded(item, remaining) {
				return false
			}
		}
	case map[string]string:
		for key, item := range typed {
			if !consume(key) || !consume(item) {
				return false
			}
		}
	case []any:
		for _, item := range typed {
			if !parameterTextBytesBounded(item, remaining) {
				return false
			}
		}
	case []string:
		for _, item := range typed {
			if !consume(item) {
				return false
			}
		}
	case []map[string]any:
		for _, item := range typed {
			if !parameterTextBytesBounded(item, remaining) {
				return false
			}
		}
	case []map[string]string:
		for _, item := range typed {
			if !parameterTextBytesBounded(item, remaining) {
				return false
			}
		}
	}
	return true
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

func containsPromptInjectionValue(value any) bool {
	switch typed := value.(type) {
	case string:
		return promptInjectionText.MatchString(typed)
	case map[string]any:
		for _, item := range typed {
			if containsPromptInjectionValue(item) {
				return true
			}
		}
	case map[string]string:
		for _, item := range typed {
			if containsPromptInjectionValue(item) {
				return true
			}
		}
	case []any:
		for _, item := range typed {
			if containsPromptInjectionValue(item) {
				return true
			}
		}
	case []string:
		for _, item := range typed {
			if containsPromptInjectionValue(item) {
				return true
			}
		}
	case []map[string]any:
		for _, item := range typed {
			if containsPromptInjectionValue(item) {
				return true
			}
		}
	case []map[string]string:
		for _, item := range typed {
			if containsPromptInjectionValue(item) {
				return true
			}
		}
	}
	return false
}

func containsObservationPromptInjection(context OperationContext) bool {
	return operationContextTextMatches(context, promptInjectionText.MatchString)
}

func operationContextTextMatches(context OperationContext, matches func(string) bool) bool {
	contains := func(values ...string) bool {
		for _, value := range values {
			if matches(value) {
				return true
			}
		}
		return false
	}
	if snapshot := context.ResourceSnapshot; snapshot != nil {
		if contains(snapshot.Source) {
			return true
		}
		for _, target := range snapshot.Targets {
			if contains(target.TargetProfileID, target.Status, target.RuntimeHealth) {
				return true
			}
		}
	}
	if monitoring := context.MonitoringSummary; monitoring != nil {
		if contains(monitoring.Source, monitoring.Summary.Status, monitoring.Summary.RequestID) {
			return true
		}
		for status := range monitoring.Summary.Deployments.ByStatus {
			if contains(status) {
				return true
			}
		}
		for _, target := range monitoring.Summary.RuntimeHealth {
			if contains(target.TargetProfileID, target.Status, target.RuntimeHealth) {
				return true
			}
		}
		for _, alarm := range monitoring.Summary.Alarms {
			if contains(
				alarm.Severity,
				alarm.ErrorCode,
				alarm.LatestDeploymentID,
				alarm.LatestStage,
				alarm.LatestMessage,
			) {
				return true
			}
		}
	}
	if logs := context.DeploymentLogs; logs != nil {
		if contains(logs.Source) {
			return true
		}
		for _, item := range logs.Items {
			if contains(
				item.Timestamp,
				item.Level,
				item.RequestID,
				item.DeploymentID,
				item.Component,
				item.Stage,
				item.Message,
				item.ErrorCode,
			) {
				return true
			}
		}
	}
	if metrics := context.MetricsSummary; metrics != nil {
		return contains(metrics.Source, metrics.DeploymentID)
	}
	return false
}

func promptInputTextSafe(value string) bool {
	for _, character := range value {
		if (unicode.IsControl(character) && character != '\r' && character != '\n' && character != '\t') ||
			character == '\u2028' || character == '\u2029' ||
			(character >= '\u200b' && character <= '\u200f') ||
			(character >= '\u202a' && character <= '\u202e') ||
			(character >= '\u2060' && character <= '\u206f') ||
			character == '\ufeff' {
			return false
		}
	}
	return true
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
	case "prompt_injection":
		return "request contains prompt-control instructions"
	case "input_text_hygiene":
		return "request contains forbidden control or directional formatting characters"
	case "resource_intent_grammar":
		return "resource request uses unsupported negation or contrast grammar"
	case "supported_requirement_scope":
		return "request contains requirements that cannot be represented by the LLM operation contract"
	case "responsibility_user_text":
		return "user request crosses the bounded planning responsibility"
	case "bounded_parameters":
		return "request parameters exceed the bounded JSON envelope"
	case "manifest_parameters_disabled":
		return "untyped manifest parameters are not supported by the LLM operation contract"
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
