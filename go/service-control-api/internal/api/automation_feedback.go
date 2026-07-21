package api

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

var errAutomationFeedbackNotFound = errors.New("automation feedback was not found")

type automationFeedbackStore struct {
	mu       sync.RWMutex
	expected map[string]string
	records  map[string]AutomationFeedbackRecord
}

func newAutomationFeedbackStore() *automationFeedbackStore {
	return &automationFeedbackStore{
		expected: map[string]string{},
		records:  map[string]AutomationFeedbackRecord{},
	}
}

func (store *automationFeedbackStore) register(correlationID string, executor string) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.expected[correlationID] = executor
}

func (store *automationFeedbackStore) record(request AutomationFeedbackRequest) (AutomationFeedbackRecord, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	executor, ok := store.expected[request.CorrelationID]
	if !ok {
		return AutomationFeedbackRecord{}, fmt.Errorf("unknown automation correlation id")
	}
	if executor != request.Executor {
		return AutomationFeedbackRecord{}, fmt.Errorf("feedback executor does not match the approved handoff")
	}
	record := AutomationFeedbackRecord{
		AutomationFeedbackRequest: request,
		ReceivedAt:                time.Now().UTC().Format(time.RFC3339),
	}
	store.records[request.CorrelationID] = record
	return record, nil
}

func (store *automationFeedbackStore) list() []AutomationFeedbackRecord {
	store.mu.RLock()
	defer store.mu.RUnlock()
	records := make([]AutomationFeedbackRecord, 0, len(store.records))
	for _, record := range store.records {
		records = append(records, record)
	}
	sort.Slice(records, func(left, right int) bool {
		if records[left].ReceivedAt == records[right].ReceivedAt {
			return records[left].CorrelationID < records[right].CorrelationID
		}
		return records[left].ReceivedAt > records[right].ReceivedAt
	})
	return records
}

func (store *automationFeedbackStore) remove(correlationID string) (AutomationFeedbackRecord, bool) {
	store.mu.Lock()
	defer store.mu.Unlock()
	record, ok := store.records[correlationID]
	if ok {
		delete(store.records, correlationID)
	}
	return record, ok
}

func (store *automationFeedbackStore) clear() int {
	store.mu.Lock()
	defer store.mu.Unlock()
	deleted := len(store.records)
	store.records = map[string]AutomationFeedbackRecord{}
	return deleted
}

func (service Service) RecordAutomationFeedback(ctx context.Context, request AutomationFeedbackRequest) (AutomationFeedbackRecord, error) {
	if err := ensureContext(ctx); err != nil {
		return AutomationFeedbackRecord{}, err
	}
	if !contains([]string{"accepted", "running", "succeeded", "failed", "stopped"}, request.Status) {
		return AutomationFeedbackRecord{}, fmt.Errorf("unsupported automation feedback status")
	}
	if containsCredentialMarker(request.Message) {
		return AutomationFeedbackRecord{}, fmt.Errorf("feedback message contains credential-like content")
	}
	return service.automationFeedback.record(request)
}

func (service Service) ListAutomationFeedback(ctx context.Context) ([]AutomationFeedbackRecord, error) {
	if err := ensureContext(ctx); err != nil {
		return nil, err
	}
	return service.automationFeedback.list(), nil
}

func (service Service) DeleteAutomationFeedback(ctx context.Context, correlationID string) (AutomationFeedbackRecord, error) {
	if err := ensureContext(ctx); err != nil {
		return AutomationFeedbackRecord{}, err
	}
	correlationID = strings.TrimSpace(correlationID)
	if correlationID == "" {
		return AutomationFeedbackRecord{}, errAutomationFeedbackNotFound
	}
	record, ok := service.automationFeedback.remove(correlationID)
	if !ok {
		return AutomationFeedbackRecord{}, errAutomationFeedbackNotFound
	}
	return record, nil
}

func (service Service) ClearAutomationFeedback(ctx context.Context) (int, error) {
	if err := ensureContext(ctx); err != nil {
		return 0, err
	}
	return service.automationFeedback.clear(), nil
}

func containsCredentialMarker(value string) bool {
	value = strings.ToLower(value)
	markers := []string{
		"aws_secret_access_key",
		"api_key",
		"apikey",
		"password",
		"private key",
		"access token",
		"credential",
	}
	for _, marker := range markers {
		if strings.Contains(value, marker) {
			return true
		}
	}
	return false
}
