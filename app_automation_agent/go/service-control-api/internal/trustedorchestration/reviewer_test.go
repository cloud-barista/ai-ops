package trustedorchestration

import (
	"context"
	"strings"
	"testing"

	"kyunghee-aiops/service-control-api/internal/llmop"
)

func TestConfigReviewerRejectsMissingCandidateConfigPath(t *testing.T) {
	reviewer := ConfigReviewer{GuardPolicyPath: "guard.json"}
	_, err := reviewer.Review(context.Background(), llmop.Request{})
	if err == nil || !strings.Contains(err.Error(), "candidate config path") {
		t.Fatalf("missing candidate path error = %v", err)
	}
}

func TestConfigReviewerRejectsMissingGuardPolicyPath(t *testing.T) {
	reviewer := ConfigReviewer{CandidateConfigPath: "candidates.json"}
	_, err := reviewer.Review(context.Background(), llmop.Request{})
	if err == nil || !strings.Contains(err.Error(), "guard policy path") {
		t.Fatalf("missing guard path error = %v", err)
	}
}

func TestConfigReviewerDisablesLiveCompletionByDefault(t *testing.T) {
	reviewer := ConfigReviewer{}
	if reviewer.AllowLiveCompletion {
		t.Fatal("zero-value ConfigReviewer must keep live completion disabled")
	}
}
