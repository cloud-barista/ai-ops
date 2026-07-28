package appdeploy

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClientBuildPackageStreamsMultipartContract(t *testing.T) {
	var gotFile string
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/api/v1/artifacts/packages" {
			t.Fatalf("request = %s %s", request.Method, request.URL.Path)
		}
		if request.Header.Get("accept") != "application/json" {
			t.Fatalf("accept = %q", request.Header.Get("accept"))
		}
		if err := request.ParseMultipartForm(1 << 20); err != nil {
			t.Fatal(err)
		}
		file, header, err := request.FormFile("source")
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()
		content, err := io.ReadAll(file)
		if err != nil {
			t.Fatal(err)
		}
		gotFile = string(content)
		if header.Filename != "run.sh" ||
			request.FormValue("package_type") != "script" ||
			request.FormValue("app_name") != "gpu-app" ||
			request.FormValue("app_version") != "0.1.0" ||
			request.FormValue("entrypoint") != "run.sh" ||
			request.FormValue("runtime_type") != "gpu" ||
			request.FormValue("service_port") != "8080" ||
			request.FormValue("healthcheck_path") != "/healthz" {
			t.Fatalf("unexpected multipart request")
		}
		writer.Header().Set("content-type", "application/json")
		writer.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(writer, `{
		  "package_type":"script",
		  "artifact_uri":"file:///packages/app.tar.gz",
		  "archive_name":"app.tar.gz",
		  "checksum":"sha256:test",
		  "app_spec":{"kind":"AIApp"}
		}`)
	}))
	defer server.Close()

	client, err := NewClient(server.URL+"/api/v1", server.Client())
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	response, err := client.BuildPackage(context.Background(), PackageUpload{
		Source:          strings.NewReader("#!/bin/bash\n"),
		Filename:        "run.sh",
		PackageType:     "script",
		AppName:         "gpu-app",
		AppVersion:      "0.1.0",
		Entrypoint:      "run.sh",
		RuntimeType:     "gpu",
		ServicePort:     8080,
		HealthcheckPath: "/healthz",
	})
	if err != nil || gotFile != "#!/bin/bash\n" || string(response.AppSpec) != `{"kind":"AIApp"}` {
		t.Fatalf("BuildPackage() response=%#v err=%v file=%q", response, err, gotFile)
	}
}

func TestClientRegisterAppSendsOnlyAppSpec(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/api/v1/apps" {
			t.Fatalf("request = %s %s", request.Method, request.URL.Path)
		}
		content, err := io.ReadAll(request.Body)
		if err != nil {
			t.Fatal(err)
		}
		var body map[string]json.RawMessage
		if err := json.Unmarshal(content, &body); err != nil {
			t.Fatal(err)
		}
		if len(body) != 1 || string(body["app_spec"]) != `{"kind":"AIApp"}` {
			t.Fatalf("body = %s", content)
		}
		writer.Header().Set("content-type", "application/json")
		_, _ = io.WriteString(writer, `{
		  "app_id":"app-1",
		  "app_version_id":"appver-1",
		  "name":"gpu-app",
		  "version":"0.1.0",
		  "app_spec":{"kind":"AIApp"}
		}`)
	}))
	defer server.Close()

	client, err := NewClient(server.URL+"/api/v1", server.Client())
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	response, err := client.RegisterApp(context.Background(), json.RawMessage(`{"kind":"AIApp"}`))
	if err != nil || response.AppID != "app-1" || response.AppVersionID != "appver-1" || string(response.AppSpec) != `{"kind":"AIApp"}` {
		t.Fatalf("RegisterApp() response=%#v err=%v", response, err)
	}
}

func TestClientRegisterAppRequiresResponseIdentifiers(t *testing.T) {
	for _, response := range []string{
		`{"app_version_id":"appver-1"}`,
		`{"app_id":"app-1"}`,
	} {
		t.Run(response, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				writer.Header().Set("content-type", "application/json")
				_, _ = io.WriteString(writer, response)
			}))
			defer server.Close()

			client, err := NewClient(server.URL+"/api/v1", server.Client())
			if err != nil {
				t.Fatalf("new client: %v", err)
			}
			if _, err := client.RegisterApp(context.Background(), json.RawMessage(`{"kind":"AIApp"}`)); err == nil {
				t.Fatal("expected response identifier validation failure")
			}
		})
	}
}
