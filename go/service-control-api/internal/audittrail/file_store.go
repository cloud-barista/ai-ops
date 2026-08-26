package audittrail

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var (
	ErrPersistence      = errors.New("execution audit persistence failed")
	ErrIdentityMismatch = errors.New("execution audit identity mismatch")
)

type FileStore struct {
	root string
}

type Session struct {
	mu                sync.Mutex
	auditID           string
	kind              string
	startedAt         time.Time
	request           RequestSummary
	identity          Identity
	runDirectory      string
	relativeDirectory string
	eventsPath        string
	summaryPath       string
	relativeEvents    string
	relativeSummary   string
	events            []Event
	lastEventSHA256   string
	degraded          bool
	finalized         bool
}

type contextKey struct{}

type Recorder interface {
	Record(Event) error
	BindIdentity(Identity) error
}

type bestEffortRecorder struct {
	recorder Recorder
}

func NewFileStore(root string) *FileStore {
	return &FileStore{root: strings.TrimSpace(root)}
}

func (store *FileStore) Start(metadata StartMetadata) (*Session, Reference, error) {
	auditID, err := NewAuditID()
	if err != nil {
		return nil, Reference{SchemaVersion: SchemaVersion, PersistenceStatus: PersistenceDegraded}, fmt.Errorf("%w: %v", ErrPersistence, err)
	}
	reference := Reference{
		SchemaVersion:     SchemaVersion,
		AuditID:           auditID,
		PersistenceStatus: PersistenceDegraded,
	}
	if store == nil || store.root == "" {
		return nil, reference, fmt.Errorf("%w: audit root is not configured", ErrPersistence)
	}
	root, err := filepath.Abs(store.root)
	if err != nil {
		return nil, reference, fmt.Errorf("%w: resolve audit root", ErrPersistence)
	}
	startedAt := metadata.StartedAt.UTC()
	if startedAt.IsZero() {
		startedAt = time.Now().UTC()
	}
	relativeDirectory := filepath.Join(startedAt.Format("2006-01-02"), auditID)
	runDirectory := filepath.Join(root, relativeDirectory)
	if err := ensureWithinRoot(root, runDirectory); err != nil {
		return nil, reference, fmt.Errorf("%w: invalid audit directory", ErrPersistence)
	}
	if err := os.MkdirAll(runDirectory, 0o700); err != nil {
		return nil, reference, fmt.Errorf("%w: create audit directory", ErrPersistence)
	}
	if err := os.Chmod(runDirectory, 0o700); err != nil {
		return nil, reference, fmt.Errorf("%w: protect audit directory", ErrPersistence)
	}
	eventsPath := filepath.Join(runDirectory, "events.jsonl")
	eventsFile, err := os.OpenFile(eventsPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, reference, fmt.Errorf("%w: create audit event file", ErrPersistence)
	}
	if closeErr := eventsFile.Close(); closeErr != nil {
		return nil, reference, fmt.Errorf("%w: close audit event file", ErrPersistence)
	}
	if err := os.Chmod(eventsPath, 0o600); err != nil {
		return nil, reference, fmt.Errorf("%w: protect audit event file", ErrPersistence)
	}

	session := &Session{
		auditID:           auditID,
		kind:              KindTrustedAutomation,
		startedAt:         startedAt,
		request:           metadata.Request,
		runDirectory:      runDirectory,
		relativeDirectory: filepath.ToSlash(relativeDirectory),
		eventsPath:        eventsPath,
		summaryPath:       filepath.Join(runDirectory, "summary.json"),
		relativeEvents:    filepath.ToSlash(filepath.Join(relativeDirectory, "events.jsonl")),
		relativeSummary:   filepath.ToSlash(filepath.Join(relativeDirectory, "summary.json")),
		events:            make([]Event, 0, 8),
	}
	if err := session.Record(Event{
		Stage:   StageRequest,
		Action:  "request_received",
		Outcome: "accepted_for_binding",
		Evidence: Evidence{
			InputSHA256: metadata.Request.PayloadSHA256,
		},
	}); err != nil {
		return nil, session.reference(PersistenceDegraded, false, ""), err
	}
	return session, session.reference(PersistenceRecording, false, ""), nil
}

func (session *Session) BindIdentity(identity Identity) error {
	if session == nil {
		return fmt.Errorf("%w: audit session is unavailable", ErrPersistence)
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if session.finalized {
		return fmt.Errorf("%w: audit session is finalized", ErrPersistence)
	}
	for _, field := range []struct {
		name     string
		current  *string
		incoming string
	}{
		{name: "request_id", current: &session.identity.RequestID, incoming: identity.RequestID},
		{name: "message_id", current: &session.identity.MessageID, incoming: identity.MessageID},
		{name: "correlation_id", current: &session.identity.CorrelationID, incoming: identity.CorrelationID},
		{name: "trace_id", current: &session.identity.TraceID, incoming: identity.TraceID},
		{name: "run_id", current: &session.identity.RunID, incoming: identity.RunID},
	} {
		if field.incoming == "" {
			continue
		}
		if *field.current != "" && *field.current != field.incoming {
			return fmt.Errorf("%w: %s changed", ErrIdentityMismatch, field.name)
		}
		*field.current = field.incoming
	}
	return nil
}

func (session *Session) Record(event Event) error {
	if session == nil {
		return fmt.Errorf("%w: audit session is unavailable", ErrPersistence)
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if session.finalized {
		return fmt.Errorf("%w: audit session is finalized", ErrPersistence)
	}
	return session.appendEventLocked(event)
}

func (session *Session) Finalize(metadata FinalizeMetadata) (Reference, error) {
	if session == nil {
		return Reference{SchemaVersion: SchemaVersion, PersistenceStatus: PersistenceDegraded}, fmt.Errorf("%w: audit session is unavailable", ErrPersistence)
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if session.finalized {
		return session.reference(PersistenceComplete, true, ""), fmt.Errorf("%w: audit session is already finalized", ErrPersistence)
	}
	completedAt := metadata.CompletedAt.UTC()
	if completedAt.IsZero() {
		completedAt = time.Now().UTC()
	}
	if completedAt.Before(session.startedAt) {
		completedAt = session.startedAt
	}
	terminalOutcome := metadata.TerminalOutcome
	if terminalOutcome == "" {
		terminalOutcome = "completed"
	}
	if err := session.appendEventLocked(Event{
		Stage:     StageTerminalResponse,
		Action:    "response_finalized",
		Outcome:   terminalOutcome,
		ErrorCode: metadata.ErrorCode,
		Evidence: Evidence{
			OrchestrationStatus: metadata.Result.FinalStatus,
		},
	}); err != nil {
		return session.reference(PersistenceDegraded, false, ""), err
	}

	persistenceStatus := PersistenceComplete
	complete := true
	if session.degraded {
		persistenceStatus = PersistenceDegraded
		complete = false
	}
	summary := Summary{
		SchemaVersion: SchemaVersion,
		AuditID:       session.auditID,
		Kind:          session.kind,
		Status:        metadata.Result.FinalStatus,
		PersistenceStatus: persistenceStatus,
		Complete:      complete,
		StartedAt:     session.startedAt.Format(time.RFC3339Nano),
		CompletedAt:   completedAt.Format(time.RFC3339Nano),
		DurationMS:    durationMilliseconds(session.startedAt, completedAt),
		Identity:      session.identity,
		Request:       session.request,
		Timeline:      append([]Event(nil), session.events...),
		Result:        metadata.Result,
		Failure:       metadata.Failure,
		Integrity: SummaryIntegrity{
			Algorithm:       "sha256",
			LastEventSHA256: session.lastEventSHA256,
			Authenticated:   false,
		},
		Artifacts: Artifacts{
			EventsPath:  session.relativeEvents,
			SummaryPath: session.relativeSummary,
		},
	}
	if summary.Status == "" {
		summary.Status = "ERROR"
	}
	canonical, err := json.Marshal(summary)
	if err != nil {
		return session.reference(PersistenceDegraded, false, ""), fmt.Errorf("%w: encode audit summary", ErrPersistence)
	}
	summary.Integrity.PayloadSHA256 = DigestBytes(canonical)
	encoded, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		return session.reference(PersistenceDegraded, false, ""), fmt.Errorf("%w: format audit summary", ErrPersistence)
	}
	encoded = append(encoded, '\n')
	if err := writeAtomic(session.runDirectory, session.summaryPath, encoded); err != nil {
		return session.reference(PersistenceDegraded, false, ""), fmt.Errorf("%w: write audit summary", ErrPersistence)
	}
	session.finalized = true
	return session.reference(persistenceStatus, complete, DigestBytes(encoded)), nil
}

func (session *Session) appendEventLocked(event Event) error {
	if event.Stage == "" || event.Action == "" || event.Outcome == "" {
		return fmt.Errorf("%w: audit event stage, action, and outcome are required", ErrPersistence)
	}
	event.SchemaVersion = SchemaVersion
	event.AuditID = session.auditID
	event.Sequence = uint64(len(session.events) + 1)
	event.RecordedAt = time.Now().UTC().Format(time.RFC3339Nano)
	event.Identity = session.identity
	event.Integrity = EventIntegrity{
		Algorithm:           "sha256",
		PreviousEventSHA256: session.lastEventSHA256,
		Authenticated:       false,
	}
	canonical, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("%w: encode audit event", ErrPersistence)
	}
	event.Integrity.PayloadSHA256 = DigestBytes(canonical)
	encoded, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("%w: format audit event", ErrPersistence)
	}
	encoded = append(encoded, '\n')
	file, err := os.OpenFile(session.eventsPath, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return fmt.Errorf("%w: open audit event file", ErrPersistence)
	}
	_, writeErr := file.Write(encoded)
	if writeErr == nil {
		writeErr = file.Sync()
	}
	closeErr := file.Close()
	if writeErr != nil {
		return fmt.Errorf("%w: append audit event", ErrPersistence)
	}
	if closeErr != nil {
		return fmt.Errorf("%w: close audit event file", ErrPersistence)
	}
	session.events = append(session.events, event)
	session.lastEventSHA256 = event.Integrity.PayloadSHA256
	return nil
}

func (session *Session) reference(status string, complete bool, summarySHA256 string) Reference {
	if session == nil {
		return Reference{SchemaVersion: SchemaVersion, PersistenceStatus: PersistenceDegraded}
	}
	return Reference{
		SchemaVersion:     SchemaVersion,
		AuditID:           session.auditID,
		PersistenceStatus: status,
		Complete:          complete,
		EventCount:        len(session.events),
		RelativeDirectory: session.relativeDirectory,
		EventsPath:        session.relativeEvents,
		SummaryPath:       session.relativeSummary,
		LastEventSHA256:   session.lastEventSHA256,
		SummarySHA256:     summarySHA256,
	}
}

func WithRecorder(ctx context.Context, recorder Recorder) context.Context {
	if ctx == nil || recorder == nil {
		return ctx
	}
	return context.WithValue(ctx, contextKey{}, recorder)
}

func BestEffort(recorder Recorder) Recorder {
	if recorder == nil {
		return nil
	}
	return bestEffortRecorder{recorder: recorder}
}

func (recorder bestEffortRecorder) Record(event Event) error {
	if err := recorder.recorder.Record(event); err != nil {
		recorder.markDegraded()
	}
	return nil
}

func (recorder bestEffortRecorder) BindIdentity(identity Identity) error {
	err := recorder.recorder.BindIdentity(identity)
	if err == nil {
		return nil
	}
	if errors.Is(err, ErrPersistence) {
		recorder.markDegraded()
		return nil
	}
	return err
	}

func (recorder bestEffortRecorder) markDegraded() {
	if session, ok := recorder.recorder.(*Session); ok {
		session.mu.Lock()
		session.degraded = true
		session.mu.Unlock()
	}
}

func Record(ctx context.Context, event Event) error {
	if ctx == nil {
		return nil
	}
	recorder, _ := ctx.Value(contextKey{}).(Recorder)
	if recorder == nil {
		return nil
	}
	return recorder.Record(event)
}

func BindIdentity(ctx context.Context, identity Identity) error {
	if ctx == nil {
		return nil
	}
	recorder, _ := ctx.Value(contextKey{}).(Recorder)
	if recorder == nil {
		return nil
	}
	return recorder.BindIdentity(identity)
}

func ensureWithinRoot(root string, target string) error {
	relative, err := filepath.Rel(root, target)
	if err != nil {
		return err
	}
	if relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
		return fmt.Errorf("target escapes configured root")
	}
	return nil
}

func writeAtomic(directory string, target string, contents []byte) error {
	temporary, err := os.CreateTemp(directory, ".summary-*.tmp")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	cleanup := func() {
		_ = temporary.Close()
		_ = os.Remove(temporaryPath)
	}
	if err := temporary.Chmod(0o600); err != nil {
		cleanup()
		return err
	}
	if _, err := temporary.Write(contents); err != nil {
		cleanup()
		return err
	}
	if err := temporary.Sync(); err != nil {
		cleanup()
		return err
	}
	if err := temporary.Close(); err != nil {
		_ = os.Remove(temporaryPath)
		return err
	}
	if err := os.Rename(temporaryPath, target); err != nil {
		_ = os.Remove(temporaryPath)
		return err
	}
	return os.Chmod(target, 0o600)
}

func durationMilliseconds(start time.Time, end time.Time) int64 {
	duration := end.Sub(start)
	if duration <= 0 {
		return 0
	}
	return duration.Milliseconds()
}
