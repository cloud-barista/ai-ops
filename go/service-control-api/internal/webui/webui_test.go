package webui

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestRegisterServesEmbeddedControlApp(t *testing.T) {
	server := echo.New()
	Register(server)

	tests := []struct {
		path        string
		contentType string
		contains    string
	}{
		{path: "/", contentType: "text/html", contains: "geon Agent Control"},
		{path: "/assets/app.css", contentType: "text/css", contains: ":root"},
		{path: "/assets/app.js", contentType: "text/javascript", contains: "loadAgents"},
	}

	for _, test := range tests {
		t.Run(test.path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, test.path, nil)
			response := httptest.NewRecorder()
			server.ServeHTTP(response, request)

			if response.Code != http.StatusOK {
				t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
			}
			if !strings.Contains(response.Header().Get(echo.HeaderContentType), test.contentType) {
				t.Fatalf("expected content type %q, got %q", test.contentType, response.Header().Get(echo.HeaderContentType))
			}
			if !strings.Contains(response.Body.String(), test.contains) {
				t.Fatalf("expected body to contain %q", test.contains)
			}
		})
	}
}

func TestControlAppContainsOperationalViewsAndAPIContracts(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`data-view="overview"`,
		`data-view="planner"`,
		`data-view="agents"`,
		`data-view="feedback"`,
		`id="planner-form"`,
		`id="agent-registration-form"`,
		`id="action-form"`,
		`id="feedback-form"`,
		`<option value="succeeded">succeeded</option>`,
		`aria-label="Agents &amp; Guard"`,
		`data-close-dialog`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected dashboard HTML to contain %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`/healthz`,
		`/api/v1/agents`,
		`/api/v1/planner/deployments`,
		`/api/v1/automation/action-proposals`,
		`/api/v1/automation/feedback`,
		`[data-close-dialog]`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected dashboard JavaScript to contain API route %q", expected)
		}
	}
}

func TestControlAppContainsAutonomyView(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`data-view="autonomy"`, `id="autonomy-form"`, `name="mode"`,
		`id="autonomy-start"`, `id="autonomy-stop"`, `id="autonomy-emergency-stop"`, `id="autonomy-run-cycle"`,
		`id="autonomy-latency"`, `id="autonomy-throughput"`, `id="autonomy-error-rate"`,
		`id="autonomy-qwen-action"`, `id="autonomy-guard-status"`, `id="autonomy-execution-status"`,
		`id="autonomy-timeline"`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected autonomy HTML contract %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`/api/v1/autonomy/status`, `/api/v1/autonomy/config`, `/api/v1/autonomy/start`,
		`/api/v1/autonomy/stop`, `/api/v1/autonomy/emergency-stop`, `/api/v1/autonomy/cycles`, `/api/v1/autonomy/events`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected autonomy JavaScript route %q", expected)
		}
	}
}

func TestControlAppContainsGeonDeletionControls(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`id="clear-history"`,
		`id="clear-autonomy-events"`,
		`id="clear-feedback-records"`,
		`id="feedback-record-list"`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected deletion HTML contract %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`data-delete-agent`,
		`deleteRuntimeAgent`,
		`clearAutonomyEvents`,
		`data-delete-event`,
		`deleteAutonomyEvent`,
		`data-delete-history`,
		`deleteHistoryRecord`,
		`data-delete-feedback`,
		`deleteAutomationFeedback`,
		`clearAutomationFeedback`,
		`loadAutomationFeedback`,
		`method: "DELETE"`,
		`agent.source === "runtime"`,
		`window.confirm`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected deletion JavaScript contract %q", expected)
		}
	}
	for _, forbidden := range []string{`설정 보호`, `protected-label`} {
		if strings.Contains(javascript, forbidden) {
			t.Fatalf("configuration Agents must not render protection label %q", forbidden)
		}
	}
}

func requestBody(t *testing.T, server *echo.Echo, path string) string {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, path, nil)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200 for %s, got %d body=%s", path, response.Code, response.Body.String())
	}
	return response.Body.String()
}
