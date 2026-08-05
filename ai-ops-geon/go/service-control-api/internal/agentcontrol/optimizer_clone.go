package agentcontrol

func cloneAutomationDecision(decision *AutomationDecision) *AutomationDecision {
	if decision == nil {
		return nil
	}
	value := *decision
	value.ReasonCodes = append([]string(nil), decision.ReasonCodes...)
	if decision.CorrectionRequest != nil {
		correction := *decision.CorrectionRequest
		correction.Issues = append([]ValidationIssue(nil), decision.CorrectionRequest.Issues...)
		value.CorrectionRequest = &correction
	}
	if decision.RetryPolicy != nil {
		retryPolicy := *decision.RetryPolicy
		value.RetryPolicy = &retryPolicy
	}
	return &value
}
