package webui

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestWebConsoleRoutes(t *testing.T) {
	e := echo.New()
	Register(e)

	tests := []struct {
		path        string
		contentType string
		contains    string
	}{
		{path: "/", contentType: "text/html", contains: "AI App Deployer"},
		{path: "/console", contentType: "text/html", contains: "view-dashboard"},
		{path: "/web/app.css", contentType: "text/css", contains: ".app-shell"},
		{path: "/web/app.js", contentType: "application/javascript", contains: "refreshAll"},
	}

	for _, test := range tests {
		t.Run(test.path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, test.path, nil)
			rec := httptest.NewRecorder()
			e.ServeHTTP(rec, req)
			if rec.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
			}
			if !strings.HasPrefix(rec.Header().Get("Content-Type"), test.contentType) {
				t.Fatalf("content type = %q, want prefix %q", rec.Header().Get("Content-Type"), test.contentType)
			}
			if !strings.Contains(rec.Body.String(), test.contains) {
				t.Fatalf("body does not contain %q", test.contains)
			}
			if rec.Header().Get("Content-Security-Policy") == "" {
				t.Fatal("Content-Security-Policy header is missing")
			}
			if rec.Header().Get("Cache-Control") != "no-cache" {
				t.Fatalf("cache control = %q, want no-cache", rec.Header().Get("Cache-Control"))
			}
		})
	}
}

func TestWebFormRetainsCurrentTargetBeforeAsyncRequest(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	if !strings.Contains(script, "const formElement = event.currentTarget") {
		t.Fatal("submitForm must retain the form before awaiting the API request")
	}
	if strings.Contains(script, "closeDialog(event.currentTarget") || strings.Contains(script, "event.currentTarget.reset()") {
		t.Fatal("submitForm uses an expired event.currentTarget after awaiting the API request")
	}
	if !strings.Contains(script, "void refreshAll()") {
		t.Fatal("post-create refresh should not block successful form completion")
	}
}
