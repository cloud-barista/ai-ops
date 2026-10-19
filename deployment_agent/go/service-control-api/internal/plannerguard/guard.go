package plannerguard

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

type Policy struct {
	Version                string   `json:"version"`
	MaxRequestLength       int      `json:"max_request_length"`
	AllowedRequesters      []string `json:"allowed_requesters"`
	ForbiddenRequestTerms  []string `json:"forbidden_request_terms"`
	ForbiddenParameterKeys []string `json:"forbidden_parameter_keys"`
}

type Request struct {
	NaturalLanguageRequest string         `json:"natural_language_request"`
	AppVersionID           string         `json:"app_version_id"`
	CandidateID            string         `json:"candidate_id"`
	RequestedBy            string         `json:"requested_by"`
	Parameters             map[string]any `json:"parameters,omitempty"`
}

type Check struct {
	Name   string `json:"name"`
	Passed bool   `json:"passed"`
	Reason string `json:"reason"`
}

type Decision struct {
	Valid         bool    `json:"valid"`
	Status        string  `json:"status"`
	PolicyVersion string  `json:"policy_version"`
	Reason        string  `json:"reason"`
	Checks        []Check `json:"checks"`
}

func LoadPolicy(path string) (Policy, error) {
	contents, err := os.ReadFile(path)
	if err != nil {
		return Policy{}, fmt.Errorf("read planner guard policy: %w", err)
	}

	var policy Policy
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&policy); err != nil {
		return Policy{}, fmt.Errorf("decode planner guard policy: %w", err)
	}
	if strings.TrimSpace(policy.Version) == "" {
		return Policy{}, fmt.Errorf("planner guard policy version is required")
	}
	if policy.MaxRequestLength <= 0 {
		return Policy{}, fmt.Errorf("planner guard max_request_length must be positive")
	}
	if len(policy.AllowedRequesters) == 0 {
		return Policy{}, fmt.Errorf("planner guard allowed_requesters must not be empty")
	}
	return policy, nil
}

func ValidateRequest(request Request, policy Policy) Decision {
	decision := Decision{
		Valid:         true,
		Status:        "approved",
		PolicyVersion: policy.Version,
		Reason:        "request satisfies the configured planner boundary",
	}

	addCheck := func(name string, passed bool, reason string) {
		decision.Checks = append(decision.Checks, Check{Name: name, Passed: passed, Reason: reason})
		if passed || !decision.Valid {
			return
		}
		decision.Valid = false
		decision.Status = "rejected"
		decision.Reason = reason
	}

	requestText := strings.TrimSpace(request.NaturalLanguageRequest)
	requiredFieldsValid := requestText != "" && strings.TrimSpace(request.AppVersionID) != "" && strings.TrimSpace(request.CandidateID) != ""
	addCheck("required_fields", requiredFieldsValid, checkReason(requiredFieldsValid, "required planner fields are present", "natural_language_request, app_version_id, and candidate_id are required"))
	requestLengthValid := len([]rune(requestText)) <= policy.MaxRequestLength
	addCheck("request_length", requestLengthValid, checkReason(requestLengthValid, "request is within the configured length limit", "natural-language request exceeds the configured length limit"))
	requesterAllowed := containsFold(policy.AllowedRequesters, request.RequestedBy)
	addCheck("requester_allowlist", requesterAllowed, checkReason(requesterAllowed, "requester is allowed by the planner guard policy", "requester is not allowed by the planner guard policy"))

	forbiddenTerm := findForbiddenTerm(requestText, policy.ForbiddenRequestTerms)
	addCheck("vm_only_scope", forbiddenTerm == "", forbiddenTermReason(forbiddenTerm))

	forbiddenKey := findForbiddenParameterKey(request.Parameters, policy.ForbiddenParameterKeys)
	addCheck("sensitive_parameters", forbiddenKey == "", forbiddenParameterReason(forbiddenKey))
	return decision
}

func containsFold(values []string, target string) bool {
	target = strings.TrimSpace(target)
	for _, value := range values {
		if strings.EqualFold(strings.TrimSpace(value), target) {
			return true
		}
	}
	return false
}

func findForbiddenTerm(request string, terms []string) string {
	normalizedRequest := strings.ToLower(request)
	for _, term := range terms {
		normalizedTerm := strings.ToLower(strings.TrimSpace(term))
		if normalizedTerm != "" && strings.Contains(normalizedRequest, normalizedTerm) {
			return term
		}
	}
	return ""
}

func findForbiddenParameterKey(parameters map[string]any, forbiddenKeys []string) string {
	for key, value := range parameters {
		if parameterKeyForbidden(key, forbiddenKeys) {
			return key
		}
		switch nested := value.(type) {
		case map[string]any:
			if found := findForbiddenParameterKey(nested, forbiddenKeys); found != "" {
				return found
			}
		case []any:
			for _, item := range nested {
				if object, ok := item.(map[string]any); ok {
					if found := findForbiddenParameterKey(object, forbiddenKeys); found != "" {
						return found
					}
				}
			}
		}
	}
	return ""
}

func parameterKeyForbidden(key string, forbiddenKeys []string) bool {
	normalizedKey := normalizeKey(key)
	paddedKey := "_" + normalizedKey + "_"
	for _, forbiddenKey := range forbiddenKeys {
		normalizedForbidden := normalizeKey(forbiddenKey)
		if normalizedForbidden != "" && strings.Contains(paddedKey, "_"+normalizedForbidden+"_") {
			return true
		}
	}
	return false
}

func normalizeKey(value string) string {
	replacer := strings.NewReplacer("-", "_", " ", "_")
	return strings.ToLower(replacer.Replace(strings.TrimSpace(value)))
}

func checkReason(passed bool, successReason string, failureReason string) string {
	if passed {
		return successReason
	}
	return failureReason
}

func forbiddenTermReason(term string) string {
	if term == "" {
		return "request remains within the first-year VM-only scope"
	}
	return fmt.Sprintf("request contains an out-of-scope or unsafe term: %s", term)
}

func forbiddenParameterReason(key string) string {
	if key == "" {
		return "request parameters do not contain forbidden credential fields"
	}
	return fmt.Sprintf("request parameters contain a forbidden credential field: %s", key)
}
