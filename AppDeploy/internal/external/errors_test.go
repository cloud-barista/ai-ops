package external

import (
	"errors"
	"testing"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

func TestNormalizeErrorMapsTimeout(t *testing.T) {
	err := NormalizeError(ProviderETRI, NewError(ProviderETRI, ErrorKindTimeout, "deadline exceeded", true))
	appErr := mustAppError(t, err)
	if appErr.Code != model.ErrAIInfraAPITimeout {
		t.Fatalf("code = %s, want %s", appErr.Code, model.ErrAIInfraAPITimeout)
	}
	if !appErr.Retryable {
		t.Fatal("timeout should be retryable")
	}
}

func TestNormalizeErrorMapsAuthFailure(t *testing.T) {
	err := NormalizeError(ProviderBespin, NewError(ProviderBespin, ErrorKindAuthFailed, "invalid gateway token", true))
	appErr := mustAppError(t, err)
	if appErr.Code != model.ErrGatewayAuthFailed {
		t.Fatalf("code = %s, want %s", appErr.Code, model.ErrGatewayAuthFailed)
	}
	if appErr.Retryable {
		t.Fatal("auth failure should not be retryable")
	}
}

func TestNormalizeErrorMapsBespinProviderFailure(t *testing.T) {
	err := NormalizeError(ProviderBespin, errors.New("bad upstream response"))
	appErr := mustAppError(t, err)
	if appErr.Code != model.ErrBespinAPIFailed {
		t.Fatalf("code = %s, want %s", appErr.Code, model.ErrBespinAPIFailed)
	}
}

func mustAppError(t *testing.T, err error) *apperrors.AppError {
	t.Helper()
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		t.Fatalf("expected AppError, got %T", err)
	}
	return appErr
}
