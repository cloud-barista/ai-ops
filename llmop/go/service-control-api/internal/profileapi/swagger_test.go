package profileapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

func TestSwaggerAndOpenAPIMatchRegisteredRoutes(t *testing.T) {
	server := newServer(NewConfig(), &generatorStub{})
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
		Paths map[string]map[string]any `yaml:"paths"`
	}
	if err := yaml.Unmarshal(response.Body.Bytes(), &document); err != nil {
		t.Fatal(err)
	}
	registered := make(map[string]bool)
	for _, route := range server.Routes() {
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
}
