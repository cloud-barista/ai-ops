package server_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestCredentialAPIAcceptsSameOriginLoopback(t *testing.T) {
	e := newCredentialTestServer(t, "", false)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/credentials", nil)
	req.RemoteAddr = "127.0.0.1:4242"
	req.Host = "127.0.0.1:8080"
	req.Header.Set("Origin", "http://127.0.0.1:8080")
	req.Header.Set("X-Request-ID", "req-test-001")
	recorder := httptest.NewRecorder()
	e.ServeHTTP(recorder, req)
	assertStatus(t, recorder, http.StatusOK)
}

func TestCredentialAPIRemoteOptInStillRejectsCrossOriginBrowserRequest(t *testing.T) {
	e := newCredentialTestServer(t, "", true)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/credentials", nil)
	req.RemoteAddr = "198.51.100.7:4242"
	req.Host = "credentials.example.com"
	req.Header.Set("Origin", "https://attacker.example")
	req.Header.Set("X-Request-ID", "req-test-001")
	recorder := httptest.NewRecorder()
	e.ServeHTTP(recorder, req)
	assertAPIError(t, recorder, http.StatusForbidden, model.ErrGatewayAuthFailed)
}
