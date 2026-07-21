package autonomy

import (
	"testing"
	"time"
)

func TestValidateExecutionSafetyGates(t *testing.T) {
	now := time.Date(2026, 7, 21, 3, 10, 0, 0, time.UTC)
	base := GuardInput{
		Mode: ModeGuardedAuto, Action: ActionRestart, RegistryApproved: true,
		Evaluation:          Evaluation{Status: EvaluationViolated, EvidenceFresh: true, ConsecutiveViolations: 2},
		RequiredConsecutive: 2, MaxActions: 3, Now: now,
	}

	if got := ValidateExecution(base); !got.Approved || !got.Executable || got.Status != GuardApproved {
		t.Fatalf("expected approved execution: %#v", got)
	}

	monitor := base
	monitor.Mode = ModeMonitorOnly
	if got := ValidateExecution(monitor); !got.Approved || got.Executable || got.Status != GuardWouldExecute {
		t.Fatalf("monitor-only must become would_execute: %#v", got)
	}

	tests := []struct {
		name string
		edit func(*GuardInput)
		want GuardStatus
	}{
		{name: "registry rejection", edit: func(input *GuardInput) { input.RegistryApproved = false }, want: GuardRejected},
		{name: "stale evidence", edit: func(input *GuardInput) {
			input.Evaluation.EvidenceFresh = false
			input.Evaluation.FailureEvidence = false
		}, want: GuardBlocked},
		{name: "below consecutive threshold", edit: func(input *GuardInput) { input.Evaluation.ConsecutiveViolations = 1 }, want: GuardBlocked},
		{name: "cooldown active", edit: func(input *GuardInput) { input.CooldownUntil = now.Add(time.Minute) }, want: GuardBlocked},
		{name: "action budget exhausted", edit: func(input *GuardInput) { input.ActionCount = 3 }, want: GuardBlocked},
		{name: "action already executed", edit: func(input *GuardInput) { input.ActionAlreadyExecuted = true }, want: GuardBlocked},
		{name: "scale out target missing", edit: func(input *GuardInput) { input.Action = ActionScaleOut }, want: GuardBlocked},
		{name: "rollback version missing", edit: func(input *GuardInput) { input.Action = ActionRollback }, want: GuardBlocked},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := base
			test.edit(&input)
			if got := ValidateExecution(input); got.Status != test.want || got.Executable {
				t.Fatalf("unexpected guard result: %#v", got)
			}
		})
	}
}

func TestValidateExecutionActionRequirements(t *testing.T) {
	base := GuardInput{
		Mode: ModeGuardedAuto, RegistryApproved: true,
		Evaluation:          Evaluation{Status: EvaluationViolated, EvidenceFresh: true, ConsecutiveViolations: 1},
		RequiredConsecutive: 1, MaxActions: 1, Now: time.Now(),
	}
	for _, test := range []struct {
		action Action
		edit   func(*GuardInput)
	}{
		{action: ActionScaleOut, edit: func(input *GuardInput) { input.StandbyTargetProfileID = "target-standby" }},
		{action: ActionRollback, edit: func(input *GuardInput) { input.RollbackAppVersionID = "appver-old" }},
		{action: ActionObserve},
		{action: ActionStop},
	} {
		input := base
		input.Action = test.action
		if test.edit != nil {
			test.edit(&input)
		}
		if got := ValidateExecution(input); !got.Executable {
			t.Fatalf("action %s should pass with required input: %#v", test.action, got)
		}
	}
}
