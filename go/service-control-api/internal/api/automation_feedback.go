package api

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"
)

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
