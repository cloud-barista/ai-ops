package audittrail

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestFileStoreWritesChainedEventsAndAtomicSummary(t *testing.T) {
	startedAt := time.Date(2026, time.August, 26, 1, 2, 3, 0, time.UTC)
	store := NewFileStore(t.TempDir())
	session, reference, err := store.Start(StartMetadata{
		StartedAt: startedAt,
		Request: RequestSummary{
			InputType:             "natural_language",
			CandidateIDSHA256:     DigestString("qwen3.5-ops-planner"),
			PayloadSHA256:         DigestString("exact-untrusted-input"),
			UserRequestRuneCount:  21,
			RawPayloadStored:      false,
			RawModelContentStored: false,
		},
	})
	if err != nil {
		t.Fatalf("start audit: %v", err)
	}
	if reference.PersistenceStatus != PersistenceRecording || reference.Complete {
		t.Fatalf("start reference = %#v", reference)
	}
	identity := Identity{
		RequestID:     "request-audit-001",
		MessageID:     "msg-audit-001",
		CorrelationID: "flow-audit-001",
		TraceID:       "trace-audit-001",
		RunID:         "run-audit-001",
	}
	if err := session.BindIdentity(identity); err != nil {
		t.Fatalf("bind identity: %v", err)
	}
	if err := session.Record(Event{
		Stage:   StageSafeguard,
		Action:  "review_completed",
		Outcome: "approved",
		Evidence: Evidence{
			PolicyVersion:  "planner-guard/v1",
			Decision:       "allow_request",
			ReasonCode:     "BOUNDED_RESOURCE_PLAN",
			RequestBinding: DigestString("bound-request"),
		},
	}); err != nil {
		t.Fatalf("record safeguard: %v", err)
	}
	completedAt := startedAt.Add(1500 * time.Millisecond)
	reference, err = session.Finalize(FinalizeMetadata{
		CompletedAt:     completedAt,
		TerminalOutcome: "approved",
		Result: ResultSummary{
			FinalStatus:       "APPROVED_FLOW_READY",
			SafeguardApproved: true,
			FlowState:         "DEPLOY_APPROVED",
			RevisionCount:     1,
		},
	})
	if err != nil {
		t.Fatalf("finalize audit: %v", err)
	}
	if reference.PersistenceStatus != PersistenceComplete || !reference.Complete {
		t.Fatalf("final reference = %#v", reference)
	}
	if reference.EventCount != 3 || reference.LastEventSHA256 == "" || reference.SummarySHA256 == "" {
		t.Fatalf("final integrity reference = %#v", reference)
	}

	eventsContent, err := os.ReadFile(filepath.Join(store.root, filepath.FromSlash(reference.EventsPath)))
	if err != nil {
		t.Fatalf("read events: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(eventsContent)), "\n")
	if len(lines) != 3 {
		t.Fatalf("event lines = %d, want 3", len(lines))
	}
	var first Event
	var second Event
	if err := json.Unmarshal([]byte(lines[0]), &first); err != nil {
		t.Fatalf("decode first event: %v", err)
	}
	if err := json.Unmarshal([]byte(lines[1]), &second); err != nil {
		t.Fatalf("decode second event: %v", err)
	}
	if second.Integrity.PreviousEventSHA256 != first.Integrity.PayloadSHA256 {
		t.Fatalf("event hash chain is broken: first=%#v second=%#v", first.Integrity, second.Integrity)
	}

	summaryContent, err := os.ReadFile(filepath.Join(store.root, filepath.FromSlash(reference.SummaryPath)))
	if err != nil {
		t.Fatalf("read summary: %v", err)
	}
	var summary Summary
	if err := json.Unmarshal(summaryContent, &summary); err != nil {
		t.Fatalf("decode summary: %v", err)
	}
	if summary.Identity != identity || !summary.Complete || summary.Result.RevisionCount != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	if summary.Request.RawPayloadStored || summary.Request.RawModelContentStored {
		t.Fatalf("raw content persistence was enabled: %#v", summary.Request)
	}
	if strings.Contains(string(summaryContent), "exact-untrusted-input") {
		t.Fatal("raw request text was persisted")
	}
}

func TestSessionRejectsIdentityDrift(t *testing.T) {
	store := NewFileStore(t.TempDir())
	session, _, err := store.Start(StartMetadata{
		StartedAt: time.Now().UTC(),
		Request: RequestSummary{
			InputType:     "natural_language",
			CandidateIDSHA256: DigestString("candidate"),
			PayloadSHA256: DigestString("request"),
		},
	})
	if err != nil {
		t.Fatalf("start audit: %v", err)
	}
	if err := session.BindIdentity(Identity{CorrelationID: "flow-001"}); err != nil {
		t.Fatalf("bind first identity: %v", err)
	}
	err = session.BindIdentity(Identity{CorrelationID: "flow-002"})
	if !errors.Is(err, ErrIdentityMismatch) {
		t.Fatalf("identity drift error = %v", err)
	}
}

func TestFileStoreFailsWhenRootIsNotDirectory(t *testing.T) {
	rootFile := filepath.Join(t.TempDir(), "audit-root-file")
	if err := os.WriteFile(rootFile, []byte("not a directory"), 0o600); err != nil {
		t.Fatalf("write root file: %v", err)
	}
	_, reference, err := NewFileStore(rootFile).Start(StartMetadata{
		StartedAt: time.Now().UTC(),
		Request: RequestSummary{
			InputType:     "natural_language",
			CandidateIDSHA256: DigestString("candidate"),
			PayloadSHA256: DigestString("request"),
		},
	})
	if !errors.Is(err, ErrPersistence) {
		t.Fatalf("persistence error = %v", err)
	}
	if reference.PersistenceStatus != PersistenceDegraded || reference.AuditID == "" {
		t.Fatalf("degraded reference = %#v", reference)
	}
}

func TestFileStoreCreatesUniqueConcurrentRunDirectories(t *testing.T) {
	store := NewFileStore(t.TempDir())
	const count = 12
	identifiers := make(chan string, count)
	errorsChannel := make(chan error, count)
	var group sync.WaitGroup
	for index := 0; index < count; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			session, reference, err := store.Start(StartMetadata{
				StartedAt: time.Now().UTC(),
				Request: RequestSummary{
					InputType:     "structured",
					CandidateIDSHA256: DigestString("candidate"),
					PayloadSHA256: DigestString("request"),
				},
			})
			if err != nil {
				errorsChannel <- err
				return
			}
			identifiers <- reference.AuditID
			_, err = session.Finalize(FinalizeMetadata{
				CompletedAt:     time.Now().UTC(),
				TerminalOutcome: "completed",
				Result:          ResultSummary{FinalStatus: "SAFEGUARD_STOPPED"},
			})
			if err != nil {
				errorsChannel <- err
			}
		}()
	}
	group.Wait()
	close(identifiers)
	close(errorsChannel)
	for err := range errorsChannel {
		t.Fatalf("concurrent audit: %v", err)
	}
	unique := map[string]bool{}
	for identifier := range identifiers {
		if unique[identifier] {
			t.Fatalf("duplicate audit id: %s", identifier)
		}
		unique[identifier] = true
	}
	if len(unique) != count {
		t.Fatalf("unique audit ids = %d, want %d", len(unique), count)
	}
}

func TestBestEffortRecorderKeepsEvidenceExplicitlyDegraded(t *testing.T) {
	store := NewFileStore(t.TempDir())
	session, _, err := store.Start(StartMetadata{
		StartedAt: time.Now().UTC(),
		Request: RequestSummary{
			InputType:         "natural_language",
			CandidateIDSHA256: DigestString("candidate"),
			PayloadSHA256:     DigestString("request"),
		},
	})
	if err != nil {
		t.Fatalf("start audit: %v", err)
	}
	recorder := BestEffort(session)
	if err := recorder.Record(Event{}); err != nil {
		t.Fatalf("best-effort record returned an error: %v", err)
	}
	reference, err := session.Finalize(FinalizeMetadata{
		CompletedAt:     time.Now().UTC(),
		TerminalOutcome: "completed",
		Result:          ResultSummary{FinalStatus: "SAFEGUARD_STOPPED"},
	})
	if err != nil {
		t.Fatalf("finalize degraded audit: %v", err)
	}
	if reference.PersistenceStatus != PersistenceDegraded || reference.Complete {
		t.Fatalf("degraded reference = %#v", reference)
	}
	contents, err := os.ReadFile(filepath.Join(store.root, filepath.FromSlash(reference.SummaryPath)))
	if err != nil {
		t.Fatalf("read degraded summary: %v", err)
	}
	var summary Summary
	if err := json.Unmarshal(contents, &summary); err != nil {
		t.Fatalf("decode degraded summary: %v", err)
	}
	if summary.PersistenceStatus != PersistenceDegraded || summary.Complete {
		t.Fatalf("degraded summary = %#v", summary)
	}
}

func TestSafeLabelDropsUnboundedOrSecretLikeValues(t *testing.T) {
	if value := SafeLabel("qwen3.5/model-v1"); value != "qwen3.5/model-v1" {
		t.Fatalf("safe label = %q", value)
	}
	for _, unsafe := range []string{
		"Bearer secret-token",
		"line\nbreak",
		strings.Repeat("a", 129),
	} {
		if value := SafeLabel(unsafe); value != "" {
			t.Fatalf("unsafe label %q was retained as %q", unsafe, value)
		}
	}
}
