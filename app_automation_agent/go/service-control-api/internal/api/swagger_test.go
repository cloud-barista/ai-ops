package api

import (
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
	"go.yaml.in/yaml/v3"
)

func TestSwaggerServesActiveContract(t *testing.T) {
	config := NewServerConfig()
	for name, server := range map[string]*echo.Echo{
		"focused": NewFocusedServer(config),
		"legacy":  NewServer(config),
		"poc":     NewAppDeployPoCServer(config),
	} {
		t.Run(name, func(t *testing.T) {
			for _, path := range []string{"/", "/llm-op-demo", "/assets/app.js"} {
				response := httptest.NewRecorder()
				server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
				if response.Code != http.StatusNotFound {
					t.Errorf("GET %s: status=%d, want 404", path, response.Code)
				}
			}
			for _, path := range []string{"/swagger", "/swagger/", "/swagger/index.html"} {
				response := httptest.NewRecorder()
				server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
				if response.Code != http.StatusOK || !strings.HasPrefix(response.Header().Get("Content-Type"), "text/html") ||
					!strings.Contains(response.Body.String(), "SwaggerUIBundle") || !strings.Contains(response.Body.String(), "url: '/openapi.yaml'") {
					t.Fatalf("GET %s: status=%d body=%s", path, response.Code, response.Body.String())
				}
			}
			response := httptest.NewRecorder()
			server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, pathOpenAPI, nil))
			if response.Code != http.StatusOK {
				t.Fatalf("OpenAPI status=%d", response.Code)
			}
			var document struct {
				Info struct{ Title string }
			}
			if err := yaml.Unmarshal(response.Body.Bytes(), &document); err != nil {
				t.Fatal(err)
			}
			wantTitle := "AI Service Control API"
			if name != "legacy" {
				wantTitle = "AI-MCMP Deployment Agent API"
			}
			if document.Info.Title != wantTitle {
				t.Fatalf("active contract title=%q, want %q", document.Info.Title, wantTitle)
			}
		})
	}
}

func TestGeneratedSwaggerIncludesPlanningRequestFields(t *testing.T) {
	config := NewServerConfig()
	for _, filename := range []string{"swagger.json", "swagger.yaml"} {
		content, err := os.ReadFile(config.path("go", "service-control-api", "docs", "swagger", filename))
		if err != nil {
			t.Fatal(err)
		}
		var document struct {
			Definitions map[string]struct {
				Properties map[string]any `yaml:"properties"`
			} `yaml:"definitions"`
		}
		if err := yaml.Unmarshal(content, &document); err != nil {
			t.Fatal(err)
		}
		for _, field := range []string{"correlation_id", "trace_id", "application_profile", "resource_recommendation"} {
			if document.Definitions["api.DeploymentPlanningRequest"].Properties[field] == nil {
				t.Errorf("%s planning request is missing %s", filename, field)
			}
		}
	}
}

func TestFocusedOpenAPIMatchesRegisteredRoutes(t *testing.T) {
	config := NewServerConfig()
	config.AppDeployBaseURL = ""
	server := NewFocusedServer(config)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, pathOpenAPI, nil))
	var document struct {
		Paths map[string]map[string]any `yaml:"paths"`
	}
	if err := yaml.Unmarshal(response.Body.Bytes(), &document); err != nil {
		t.Fatal(err)
	}
	parameter := regexp.MustCompile(`:([a-z_]+)`)
	registered := make(map[string]bool)
	for _, route := range server.Routes() {
		if strings.HasPrefix(route.Path, "/swagger") {
			continue
		}
		path := parameter.ReplaceAllString(route.Path, `{$1}`)
		method := strings.ToLower(route.Method)
		if document.Paths[path][method] == nil {
			t.Errorf("undocumented route: %s %s", route.Method, path)
		}
		registered[method+" "+path] = true
	}
	for path, methods := range document.Paths {
		for method := range methods {
			if !registered[method+" "+path] {
				t.Errorf("documented route is not registered: %s %s", method, path)
			}
		}
	}
}
