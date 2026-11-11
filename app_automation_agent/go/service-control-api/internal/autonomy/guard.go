package autonomy

import (
	"fmt"
	"strings"
)

func ValidateExecution(input GuardInput) GuardDecision {
	reject := func(status GuardStatus, reason string) GuardDecision {
		return GuardDecision{Status: status, Reason: reason}
	}
	if !input.Action.Valid() {
		return reject(GuardRejected, "the proposed Action is outside the bounded automation policy")
	}
	if !input.RegistryApproved {
		reason := strings.TrimSpace(input.RegistryReason)
		if reason == "" {
			reason = "the Agent Registry does not authorize this Action"
		}
		return reject(GuardRejected, reason)
	}
	if input.Evaluation.Status != EvaluationViolated {
		return reject(GuardBlocked, "the latest evaluation does not contain a confirmed violation")
	}
	if !input.Evaluation.EvidenceFresh && !input.Evaluation.FailureEvidence {
		return reject(GuardBlocked, "fresh metric or explicit failure evidence is required")
	}
	if input.Evaluation.ConsecutiveViolations < input.RequiredConsecutive {
		return reject(GuardBlocked, fmt.Sprintf("violation confirmation requires %d consecutive cycles", input.RequiredConsecutive))
	}
	if input.CooldownUntil.After(input.Now) {
		return reject(GuardBlocked, "the deployment is in cooldown")
	}
	if input.MaxActions > 0 && input.ActionCount >= input.MaxActions {
		return reject(GuardBlocked, "the automatic Action budget is exhausted")
	}
	if input.ActionAlreadyExecuted {
		return reject(GuardBlocked, "one state-changing Action is allowed per cycle")
	}
	if input.Action == ActionScaleOut && strings.TrimSpace(input.StandbyTargetProfileID) == "" {
		return reject(GuardBlocked, "scale-out requires an explicitly configured standby Target Profile")
	}
	if input.Action == ActionRollback && strings.TrimSpace(input.RollbackAppVersionID) == "" {
		return reject(GuardBlocked, "rollback requires an explicitly configured previous App Version")
	}
	if input.Mode == ModeMonitorOnly {
		return GuardDecision{Status: GuardWouldExecute, Approved: true, Reason: "the Action passed policy but Monitor Only mode prevents state changes"}
	}
	if input.Mode != ModeGuardedAuto {
		return reject(GuardBlocked, "unknown autonomy mode")
	}
	return GuardDecision{Status: GuardApproved, Approved: true, Executable: true, Reason: "the Action passed evidence, Registry, cooldown, and budget gates"}
}
