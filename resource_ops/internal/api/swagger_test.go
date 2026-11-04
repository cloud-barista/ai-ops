package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/goccy/go-yaml"
)

func TestSwaggerAndOpenAPIMatchRegisteredRoutes(t *testing.T) {
	router, err := NewRouterWithSource(stubSource{raw: apiSnapshot()})
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/swagger", "/swagger/", "/swagger/index.html"} {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		if response.Code != http.StatusOK || !strings.HasPrefix(response.Header().Get("Content-Type"), "text/html") ||
			!strings.Contains(response.Body.String(), "SwaggerUIBundle") || !strings.Contains(response.Body.String(), "url: '/openapi.yaml'") {
			t.Fatalf("GET %s: status=%d body=%s", path, response.Code, response.Body.String())
		}
	}
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/openapi.yaml", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("OpenAPI status=%d", response.Code)
	}
	var document struct {
		Paths map[string]map[string]any `yaml:"paths" json:"paths"`
	}
	if err := yaml.Unmarshal(response.Body.Bytes(), &document); err != nil {
		t.Fatal(err)
	}
	registered := make(map[string]bool)
	for _, route := range router.Routes() {
		if strings.HasPrefix(route.Path, "/swagger") {
			continue
		}
		method := strings.ToLower(route.Method)
		if document.Paths[route.Path][method] == nil {
			t.Errorf("undocumented route: %s %s", route.Method, route.Path)
		}
		registered[method+" "+route.Path] = true
	}
	for path, methods := range document.Paths {
		for method := range methods {
			if !registered[method+" "+path] {
				t.Errorf("documented route is not registered: %s %s", method, path)
			}
		}
	}
	content, err := os.ReadFile("../../docs/swagger/swagger.json")
	if err != nil {
		t.Fatal(err)
	}
	var jsonDocument struct {
		Paths map[string]map[string]any `json:"paths"`
	}
	if err := json.Unmarshal(content, &jsonDocument); err != nil {
		t.Fatal(err)
	}
	if len(jsonDocument.Paths) != len(document.Paths) {
		t.Fatal("generated JSON and YAML document different paths")
	}
}
