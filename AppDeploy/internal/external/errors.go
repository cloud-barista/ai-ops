package external

import (
	"errors"
	"fmt"
	"net/http"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

type ErrorKind string

const (
	ErrorKindTimeout         ErrorKind = "timeout"
	ErrorKindAuthFailed      ErrorKind = "auth_failed"
	ErrorKindInvalidResponse ErrorKind = "invalid_response"
	ErrorKindProviderFailed  ErrorKind = "provider_failed"
)

type Error struct {
	Provider  Provider
	Kind      ErrorKind
	Message   string
	Retryable bool
}

func (e *Error) Error() string {
	if e.Message == "" {
		return fmt.Sprintf("%s external API %s", e.Provider, e.Kind)
	}
	return e.Message
}

func NewError(provider Provider, kind ErrorKind, message string, retryable bool) *Error {
	return &Error{
		Provider:  provider,
		Kind:      kind,
		Message:   message,
		Retryable: retryable,
	}
}

func NormalizeError(provider Provider, err error) error {
	if err == nil {
		return nil
	}
	var appErr *apperrors.AppError
	if errors.As(err, &appErr) {
		return appErr
	}

	kind := ErrorKindProviderFailed
	message := err.Error()
	retryable := true
	var externalErr *Error
	if errors.As(err, &externalErr) {
		if externalErr.Provider != "" {
			provider = externalErr.Provider
		}
		kind = externalErr.Kind
		message = externalErr.Error()
		retryable = externalErr.Retryable
	}

	code := model.ErrAIInfraAPIFailed
	status := http.StatusBadGateway
	switch kind {
	case ErrorKindTimeout:
		code = model.ErrAIInfraAPITimeout
		status = http.StatusGatewayTimeout
		retryable = true
	case ErrorKindAuthFailed:
		code = model.ErrGatewayAuthFailed
		status = http.StatusUnauthorized
		retryable = false
	case ErrorKindInvalidResponse, ErrorKindProviderFailed:
		if provider == ProviderBespin {
			code = model.ErrBespinAPIFailed
		}
	}

	return apperrors.WithDetails(code, message, status, retryable, map[string]any{
		"provider": string(provider),
		"kind":     string(kind),
	})
}
