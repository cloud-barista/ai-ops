package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/spf13/viper"
)

const testApplicationAppSpec = `{
	"schema_version":"app.khu.ai/v1alpha1",
	"kind":"AIApp",
	"metadata":{"name":"demo","version":"0.1.0"},
	"artifact":{"type":"script","uri":"file:///packages/demo.tar.gz","checksum":"sha256:test"},
	"entrypoint":{"command":"run.sh"},
	"runtime":{"type":"cpu","accelerator":"none"},
	"resources":{"cpu":"2","memory":"4Gi","gpu":"0","storage":"10Gi"},
	"network":{"ports":[{"name":"http","app_port":8080,"protocol":"tcp"}]},
	"healthcheck":{"type":"http","path":"/healthz"}
}`

type applicationWorkflowAPIFake struct {
	packageStatus      int
	registrationStatus int
	packageCalls       int
	registrationCalls  int
	source             string
}

func (fake *applicationWorkflowAPIFake) handler(t *testing.T) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("content-type", "application/json")
		switch {
		case request.Method == http.MethodPost && request.URL.Path == "/api/v1/artifacts/packages":
			fake.packageCalls++
			if fake.packageStatus != http.StatusCreated {
				writer.WriteHeader(fake.packageStatus)
				_, _ = io.WriteString(writer, `{"message":"package build failed"}`)
				return
			}
			file, header, err := request.FormFile("source")
			if err != nil {
				t.Errorf("read forwarded source: %v", err)
				http.Error(writer, "missing source", http.StatusBadRequest)
				return
			}
			defer file.Close()
			content, err := io.ReadAll(file)
			if err != nil {
				t.Errorf("read forwarded source content: %v", err)
				http.Error(writer, "invalid source", http.StatusBadRequest)
				return
			}
			fake.source = string(content)
			if header.Filename != "run.sh" ||
				request.FormValue("package_type") != "script" ||
				request.FormValue("app_name") != "demo" ||
				request.FormValue("app_version") != "0.1.0" ||
				request.FormValue("entrypoint") != "run.sh" ||
				request.FormValue("runtime_type") != "cpu" ||
				request.FormValue("service_port") != "8080" ||
				request.FormValue("healthcheck_path") != "/healthz" {
				t.Errorf("unexpected forwarded package form")
				http.Error(writer, "invalid form", http.StatusBadRequest)
				return
			}
			writer.WriteHeader(http.StatusCreated)
			_, _ = fmt.Fprintf(writer, `{
				"package_type":"script",
				"artifact_uri":"file:///packages/demo.tar.gz",
				"archive_name":"demo.tar.gz",
				"size_bytes":18,
				"checksum":"sha256:test",
				"app_spec":%s
			}`, testApplicationAppSpec)
		case request.Method == http.MethodPost && request.URL.Path == "/api/v1/apps":
			fake.registrationCalls++
			if fake.registrationStatus != http.StatusCreated {
				writer.WriteHeader(fake.registrationStatus)
				_, _ = io.WriteString(writer, `{"message":"app registration failed"}`)
				return
			}
			var payload struct {
				AppSpec json.RawMessage `json:"app_spec"`
			}
			if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
				t.Errorf("decode registration payload: %v", err)
				http.Error(writer, "invalid payload", http.StatusBadRequest)
				return
			}
			if len(payload.AppSpec) == 0 {
				t.Errorf("registration payload omitted app_spec")
				http.Error(writer, "missing app_spec", http.StatusBadRequest)
				return
			}
			writer.WriteHeader(http.StatusCreated)
			_, _ = fmt.Fprintf(writer, `{
				"app_id":"app-001",
				"app_version_id":"appver-issued",
				"name":"demo",
				"version":"0.1.0",
				"app_spec":%s
			}`, testApplicationAppSpec)
		default:
			http.NotFound(writer, request)
		}
	})
}

func TestControlRunFromPackageSuccessReturnsCreatedManifestApproved(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()

	response := performMultipartControlRunFromPackage(
		t,
		server,
		defaultApplicationWorkflowFields(),
		"run.sh",
		"#!/bin/sh\necho ok\n",
	)

	if response.Code != http.StatusCreated {
		t.Fatalf("create package ControlRun: code=%d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["status"] != "MANIFEST_APPROVED" || result["run_id"] == "" {
		t.Fatalf("unexpected successful ControlRun: %#v", result)
	}
	request, _ := result["request"].(map[string]any)
	manifest, _ := result["manifest"].(map[string]any)
	spec, _ := manifest["spec"].(map[string]any)
	requirements, _ := spec["requirements"].(map[string]any)
	resources, _ := requirements["resources"].(map[string]any)
	if request["app_version_id"] != "appver-issued" ||
		requirements["runtime"] != "cpu" ||
		requirements["accelerator"] != "none" ||
		requirements["cost_policy"] != "min_cost" ||
		resources["cpu"] != "2" ||
		resources["memory"] != "4Gi" ||
		resources["gpu"] != "0" ||
		resources["storage"] != "10Gi" {
		t.Fatalf("trusted requirements were not bound: %#v", result)
	}
	if fake.packageCalls != 1 ||
		fake.registrationCalls != 1 ||
		fake.source != "#!/bin/sh\necho ok\n" {
		t.Fatalf("unexpected AppDeploy calls: %#v", fake)
	}
}

func TestControlRunFromPackageMissingSourceReturnsBadRequest(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()

	response := performMultipartControlRunFromPackage(
		t,
		server,
		defaultApplicationWorkflowFields(),
		"",
		"",
	)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("missing source: code=%d body=%s", response.Code, response.Body.String())
	}
	if fake.packageCalls != 0 || fake.registrationCalls != 0 {
		t.Fatalf("missing source reached AppDeploy: %#v", fake)
	}
}

func TestControlRunFromPackageEmptySourceReturnsBadRequest(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()

	response := performMultipartControlRunFromPackage(
		t,
		server,
		defaultApplicationWorkflowFields(),
		"run.sh",
		"",
	)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("empty source: code=%d body=%s", response.Code, response.Body.String())
	}
	if fake.packageCalls != 0 || fake.registrationCalls != 0 {
		t.Fatalf("empty source reached AppDeploy: %#v", fake)
	}
}

func TestControlRunFromPackageOversizedSourceReturnsContentTooLarge(t *testing.T) {
	viper.Reset()
	t.Cleanup(viper.Reset)
	t.Setenv("AIOPS_APP_UPLOAD_MAX_BYTES", "256")
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()

	response := performMultipartControlRunFromPackage(
		t,
		server,
		defaultApplicationWorkflowFields(),
		"run.sh",
		strings.Repeat("x", 512),
	)

	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized source: code=%d body=%s", response.Code, response.Body.String())
	}
	if fake.packageCalls != 0 || fake.registrationCalls != 0 {
		t.Fatalf("oversized source reached AppDeploy: %#v", fake)
	}
}

func TestControlRunFromPackageMultipartMessageTooLargeReturnsContentTooLarge(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()
	fields := defaultApplicationWorkflowFields()
	fields["natural_language_request"] = strings.Repeat("x", 19<<20)

	response := performMultipartControlRunFromPackage(
		t,
		server,
		fields,
		"run.sh",
		"echo ok",
	)

	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf(
			"multipart message too large: code=%d body=%s",
			response.Code,
			response.Body.String(),
		)
	}
	if fake.packageCalls != 0 || fake.registrationCalls != 0 {
		t.Fatalf("multipart message too large reached AppDeploy: %#v", fake)
	}
}

func TestControlRunFromPackageInvalidServicePortReturnsBadRequest(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()
	fields := defaultApplicationWorkflowFields()
	fields["service_port"] = "eighty"

	response := performMultipartControlRunFromPackage(t, server, fields, "run.sh", "echo ok")

	if response.Code != http.StatusBadRequest {
		t.Fatalf("invalid service_port: code=%d body=%s", response.Code, response.Body.String())
	}
	if fake.packageCalls != 0 || fake.registrationCalls != 0 {
		t.Fatalf("invalid service_port reached AppDeploy: %#v", fake)
	}
}

func TestControlRunFromPackageRejectsMissingRequiredFieldsBeforeAppDeploy(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()

	requiredFields := []string{
		"package_type",
		"app_name",
		"app_version",
		"entrypoint",
		"runtime_type",
		"natural_language_request",
		"candidate_id",
		"cpu",
		"memory",
		"gpu",
		"storage",
	}
	for _, field := range requiredFields {
		t.Run(field, func(t *testing.T) {
			fields := defaultApplicationWorkflowFields()
			fields[field] = "   "

			response := performMultipartControlRunFromPackage(
				t,
				server,
				fields,
				"run.sh",
				"echo ok",
			)

			if response.Code != http.StatusBadRequest {
				t.Fatalf(
					"missing %s: code=%d body=%s",
					field,
					response.Code,
					response.Body.String(),
				)
			}
			if fake.packageCalls != 0 || fake.registrationCalls != 0 {
				t.Fatalf("missing %s reached AppDeploy: %#v", field, fake)
			}
		})
	}
}

func TestControlRunFromPackageRejectsInconsistentResourcesBeforeAppDeploy(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusCreated,
	)
	defer closeServer()

	tests := []struct {
		name   string
		fields map[string]string
	}{
		{
			name:   "unknown runtime",
			fields: map[string]string{"runtime_type": "tpu"},
		},
		{
			name:   "non-positive cpu",
			fields: map[string]string{"cpu": "0"},
		},
		{
			name:   "invalid memory quantity",
			fields: map[string]string{"memory": "4GB"},
		},
		{
			name:   "negative gpu",
			fields: map[string]string{"gpu": "-1"},
		},
		{
			name:   "invalid storage quantity",
			fields: map[string]string{"storage": "10GB"},
		},
		{
			name:   "cpu runtime with gpu",
			fields: map[string]string{"runtime_type": "cpu", "gpu": "1"},
		},
		{
			name:   "gpu runtime without gpu",
			fields: map[string]string{"runtime_type": "gpu", "gpu": "0"},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fields := defaultApplicationWorkflowFields()
			for name, value := range test.fields {
				fields[name] = value
			}

			response := performMultipartControlRunFromPackage(
				t,
				server,
				fields,
				"run.sh",
				"echo ok",
			)

			if response.Code != http.StatusBadRequest {
				t.Fatalf(
					"inconsistent input: code=%d body=%s",
					response.Code,
					response.Body.String(),
				)
			}
			if fake.packageCalls != 0 || fake.registrationCalls != 0 {
				t.Fatalf("inconsistent input reached AppDeploy: %#v", fake)
			}
		})
	}
}

func TestControlRunFromPackagePackageFailureReturnsRun(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusInternalServerError,
		http.StatusCreated,
	)
	defer closeServer()

	response := performMultipartControlRunFromPackage(
		t,
		server,
		defaultApplicationWorkflowFields(),
		"run.sh",
		"echo ok",
	)

	if response.Code != http.StatusBadGateway {
		t.Fatalf("package failure: code=%d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["status"] != "PACKAGE_FAILED" || result["run_id"] == "" {
		t.Fatalf("package failure did not return Run evidence: %#v", result)
	}
	if fake.packageCalls != 1 || fake.registrationCalls != 0 {
		t.Fatalf("unexpected package failure calls: %#v", fake)
	}
}

func TestControlRunFromPackageRegistrationFailureReturnsRun(t *testing.T) {
	server, fake, closeServer := newApplicationWorkflowAPIServer(
		t,
		http.StatusCreated,
		http.StatusInternalServerError,
	)
	defer closeServer()

	response := performMultipartControlRunFromPackage(
		t,
		server,
		defaultApplicationWorkflowFields(),
		"run.sh",
		"echo ok",
	)

	if response.Code != http.StatusBadGateway {
		t.Fatalf("registration failure: code=%d body=%s", response.Code, response.Body.String())
	}
	result := decodeObject(t, response.Body.Bytes())
	if result["status"] != "APP_REGISTRATION_FAILED" || result["run_id"] == "" {
		t.Fatalf("registration failure did not return Run evidence: %#v", result)
	}
	if fake.packageCalls != 1 || fake.registrationCalls != 1 {
		t.Fatalf("unexpected registration failure calls: %#v", fake)
	}
}

func TestControlRunFromPackageUploadLimitConfiguration(t *testing.T) {
	tests := []struct {
		name  string
		value string
		want  int64
	}{
		{name: "default", value: "", want: 50 << 20},
		{name: "configured", value: "12345", want: 12345},
		{name: "zero falls back", value: "0", want: 50 << 20},
		{name: "negative falls back", value: "-1", want: 50 << 20},
		{name: "capped", value: strconv.FormatInt(2<<30, 10), want: 1 << 30},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			viper.Reset()
			t.Cleanup(viper.Reset)
			t.Setenv("AIOPS_APP_UPLOAD_MAX_BYTES", test.value)

			config := NewServerConfig()

			if config.AppUploadMaxBytes != test.want {
				t.Fatalf("AppUploadMaxBytes=%d want=%d", config.AppUploadMaxBytes, test.want)
			}
		})
	}
}

func newApplicationWorkflowAPIServer(
	t *testing.T,
	packageStatus int,
	registrationStatus int,
) (http.Handler, *applicationWorkflowAPIFake, func()) {
	t.Helper()
	fake := &applicationWorkflowAPIFake{
		packageStatus:      packageStatus,
		registrationStatus: registrationStatus,
	}
	appDeploy := httptest.NewServer(fake.handler(t))
	provider, closeProvider := automationProvider(t, validManifestJSON("appver-issued"))
	config := NewServerConfig()
	config.AppDeployBaseURL = appDeploy.URL + "/api/v1"
	config.LLMCandidatesPath = writeAutomationCandidateConfig(t, provider)
	server := NewServer(config)
	return server, fake, func() {
		closeProvider()
		appDeploy.Close()
	}
}

func defaultApplicationWorkflowFields() map[string]string {
	return map[string]string{
		"package_type":             "script",
		"app_name":                 "demo",
		"app_version":              "0.1.0",
		"entrypoint":               "run.sh",
		"runtime_type":             "cpu",
		"service_port":             "8080",
		"healthcheck_path":         "/healthz",
		"natural_language_request": "Deploy this CPU inference application.",
		"candidate_id":             "decision-model",
		"requested_by":             "ai-agent",
		"cpu":                      "2",
		"memory":                   "4Gi",
		"gpu":                      "0",
		"storage":                  "10Gi",
		"cost_policy":              "min_cost",
	}
}

func performMultipartControlRunFromPackage(
	t *testing.T,
	server http.Handler,
	fields map[string]string,
	filename string,
	source string,
) *httptest.ResponseRecorder {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for name, value := range fields {
		if err := writer.WriteField(name, value); err != nil {
			t.Fatalf("write multipart field %s: %v", name, err)
		}
	}
	if filename != "" {
		part, err := writer.CreateFormFile("source", filename)
		if err != nil {
			t.Fatalf("create source form file: %v", err)
		}
		if _, err := io.WriteString(part, source); err != nil {
			t.Fatalf("write source form file: %v", err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close multipart request: %v", err)
	}

	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/control-runs/from-package",
		&body,
	)
	request.Header.Set("content-type", writer.FormDataContentType())
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	return response
}
