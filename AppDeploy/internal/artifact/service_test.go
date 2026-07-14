package artifact

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

func TestBuildAIOpsPackage(t *testing.T) {
	sourceRoot := createTestAIOpsSource(t)
	outputDir := filepath.Join(t.TempDir(), "packages")
	service := New(Config{AIOpsRoot: sourceRoot, OutputDir: outputDir, BuildTimeout: time.Minute})

	response, err := service.Build(context.Background(), model.PackageBuildRequest{
		Preset:      PresetAIOpsGeon,
		AppVersion:  "web-test-001",
		ServicePort: 18089,
	})
	if err != nil {
		t.Fatal(err)
	}
	if response.ArtifactURI == "" || !strings.HasPrefix(response.ArtifactURI, "file:") {
		t.Fatalf("artifact_uri = %q", response.ArtifactURI)
	}
	if response.SizeBytes <= 0 || !strings.HasPrefix(response.Checksum, "sha256:") {
		t.Fatalf("invalid package metadata: %+v", response)
	}
	if response.AppSpec.Artifact.Type != "package" || response.AppSpec.Artifact.URI != response.ArtifactURI {
		t.Fatalf("app spec artifact does not match package: %+v", response.AppSpec.Artifact)
	}
	if response.AppSpec.Metadata.Version != "web-test-001" {
		t.Fatalf("app version = %q", response.AppSpec.Metadata.Version)
	}
	if response.AppSpec.Healthcheck == nil || response.AppSpec.Healthcheck.Path != "/healthz" {
		t.Fatalf("healthcheck = %+v", response.AppSpec.Healthcheck)
	}
	if response.AppSpec.Network == nil || response.AppSpec.Network.Ports[0].AppPort != 18089 {
		t.Fatalf("network = %+v", response.AppSpec.Network)
	}
	if len(response.AppSpec.Entrypoint.Args) != 2 || !strings.Contains(response.AppSpec.Entrypoint.Args[1], response.ArchiveName) {
		t.Fatalf("entrypoint args = %#v", response.AppSpec.Entrypoint.Args)
	}

	archivePath := filepath.Join(outputDir, response.ArchiveName)
	entries := readArchiveEntries(t, archivePath)
	for _, name := range []string{
		aiopsBinaryName,
		"config/agent_registry.json",
		"config/ops_llm_benchmark.json",
		"config/inference_optimization.json",
		"docs/submission/openapi_service_control.yaml",
	} {
		if !entries[name] {
			t.Fatalf("archive is missing %s: %#v", name, entries)
		}
	}
}

func TestBuildUploadedGoPackage(t *testing.T) {
	outputDir := filepath.Join(t.TempDir(), "packages")
	service := New(Config{OutputDir: outputDir, BuildTimeout: time.Minute})
	source := sourceZip(t, map[string][]byte{
		"demo/go.mod":      []byte("module example.com/uploaded-go\n\ngo 1.23\n"),
		"demo/main.go":     []byte("package main\n\nfunc main() {}\n"),
		"demo/config.json": []byte("{}\n"),
	})

	response, err := service.BuildUploaded(context.Background(), model.PackageBuildRequest{
		PackageType: PackageTypeGo,
		AppName:     "uploaded-go",
		AppVersion:  "0.1.0",
		Entrypoint:  ".",
		RuntimeType: "cpu",
		ServicePort: 18080,
	}, "uploaded-go.zip", bytes.NewReader(source))
	if err != nil {
		t.Fatal(err)
	}
	if response.PackageType != PackageTypeGo || response.AppSpec.Metadata.Name != "uploaded-go" {
		t.Fatalf("unexpected package response: %+v", response)
	}
	if response.AppSpec.Network == nil || response.AppSpec.Network.Ports[0].AppPort != 18080 {
		t.Fatalf("network = %+v", response.AppSpec.Network)
	}
	if len(response.AppSpec.Entrypoint.Args) != 2 || !strings.Contains(response.AppSpec.Entrypoint.Args[1], genericBinaryName) {
		t.Fatalf("entrypoint = %+v", response.AppSpec.Entrypoint)
	}
	entries := readArchiveEntries(t, filepath.Join(outputDir, response.ArchiveName))
	for _, name := range []string{genericBinaryName, "go.mod", "main.go", "config.json"} {
		if !entries[name] {
			t.Fatalf("archive is missing %s: %#v", name, entries)
		}
	}
}

func TestBuildUploadedRuntimePackages(t *testing.T) {
	elfHeader := make([]byte, 20)
	copy(elfHeader, []byte("\x7fELF"))
	elfHeader[4] = 2
	elfHeader[5] = 1
	elfHeader[18] = 62
	tests := []struct {
		name        string
		packageType string
		fileName    string
		content     []byte
	}{
		{name: "python", packageType: PackageTypePy, fileName: "main.py", content: []byte("print('ok')\n")},
		{name: "node", packageType: PackageTypeNode, fileName: "index.js", content: []byte("console.log('ok');\n")},
		{name: "script", packageType: PackageTypeSh, fileName: "run.sh", content: []byte("#!/usr/bin/env bash\necho ok\n")},
		{name: "binary", packageType: PackageTypeBin, fileName: "server", content: elfHeader},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			outputDir := filepath.Join(t.TempDir(), "packages")
			service := New(Config{OutputDir: outputDir})
			response, err := service.BuildUploaded(context.Background(), model.PackageBuildRequest{
				PackageType: test.packageType,
				AppName:     "uploaded-" + test.name,
				AppVersion:  "0.1.0",
				Entrypoint:  test.fileName,
				RuntimeType: "cpu",
			}, test.fileName, bytes.NewReader(test.content))
			if err != nil {
				t.Fatal(err)
			}
			if response.PackageType != test.packageType || response.AppSpec.Network != nil {
				t.Fatalf("unexpected package response: %+v", response)
			}
			entries := readArchiveEntries(t, filepath.Join(outputDir, response.ArchiveName))
			if !entries[test.fileName] {
				t.Fatalf("archive is missing %s: %#v", test.fileName, entries)
			}
		})
	}
}

func TestBuildUploadedPreservesScriptPackageContract(t *testing.T) {
	outputDir := filepath.Join(t.TempDir(), "packages")
	service := New(Config{OutputDir: outputDir})
	started := time.Date(2026, time.July, 13, 10, 11, 12, 0, time.UTC)
	createdAt := started.Add(time.Second)
	nowCalls := 0
	service.now = func() time.Time {
		nowCalls++
		if nowCalls == 1 {
			return started
		}
		return createdAt
	}
	source := sourceZip(t, map[string][]byte{
		"bundle/run.sh":      []byte("#!/usr/bin/env bash\necho ok\n"),
		"bundle/config.json": []byte("{\"enabled\":true}\n"),
		"bundle/.git/config": []byte("must not be packaged\n"),
	})

	response, err := service.BuildUploaded(context.Background(), model.PackageBuildRequest{
		PackageType: " SCRIPT ",
		AppName:     " packaged-script ",
		Entrypoint:  " run.sh ",
		RuntimeType: " GPU ",
		ServicePort: 18080,
	}, "script.zip", bytes.NewReader(source))
	if err != nil {
		t.Fatal(err)
	}

	wantArchiveName := "packaged-script-script-linux-amd64-" + strconv.FormatInt(createdAt.UnixNano(), 10) + ".tar.gz"
	if response.PackageType != PackageTypeSh || response.ArchiveName != wantArchiveName {
		t.Fatalf("package metadata = type %q archive %q", response.PackageType, response.ArchiveName)
	}
	if !response.CreatedAt.Equal(createdAt) {
		t.Fatalf("created_at = %s, want %s", response.CreatedAt, createdAt)
	}
	if response.AppSpec.Metadata.Name != "packaged-script" || response.AppSpec.Metadata.Version != "web-20260713-101112-000" {
		t.Fatalf("metadata = %+v", response.AppSpec.Metadata)
	}
	if response.AppSpec.Runtime.Type != "gpu" || response.AppSpec.Runtime.Accelerator != "nvidia" {
		t.Fatalf("runtime = %+v", response.AppSpec.Runtime)
	}
	if response.AppSpec.Resources.Memory != "4Gi" || response.AppSpec.Resources.GPU != "1" {
		t.Fatalf("resources = %+v", response.AppSpec.Resources)
	}
	if response.AppSpec.Network == nil || response.AppSpec.Network.Ports[0].AppPort != 18080 || response.AppSpec.Healthcheck == nil || response.AppSpec.Healthcheck.Path != "/health" {
		t.Fatalf("network/healthcheck = %+v / %+v", response.AppSpec.Network, response.AppSpec.Healthcheck)
	}
	wantLaunch := "rm -rf ./run && mkdir -p ./run && tar -xzf " + response.ArchiveName + " -C ./run && chmod +x ./run/run.sh && cd ./run && exec env PORT=18080 bash ./run.sh"
	if response.AppSpec.Entrypoint.Command != "sh" || len(response.AppSpec.Entrypoint.Args) != 2 || response.AppSpec.Entrypoint.Args[1] != wantLaunch {
		t.Fatalf("entrypoint = %+v", response.AppSpec.Entrypoint)
	}

	archivePath := filepath.Join(outputDir, response.ArchiveName)
	archiveBytes, err := os.ReadFile(archivePath)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(archiveBytes)
	if response.SizeBytes != int64(len(archiveBytes)) || response.Checksum != "sha256:"+hex.EncodeToString(sum[:]) || response.AppSpec.Artifact.Checksum != response.Checksum {
		t.Fatalf("archive metadata = size %d checksum %q app checksum %q", response.SizeBytes, response.Checksum, response.AppSpec.Artifact.Checksum)
	}
	records := readArchiveRecords(t, archivePath)
	if len(records) != 2 || records[0].name != "config.json" || records[1].name != "run.sh" {
		t.Fatalf("archive records = %#v", records)
	}
	for _, record := range records {
		if record.mode != 0o644 || !record.modTime.Equal(createdAt) {
			t.Fatalf("archive record metadata = %+v", record)
		}
	}
	items, err := os.ReadDir(outputDir)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Name() != response.ArchiveName {
		t.Fatalf("output directory contains temporary files: %#v", items)
	}
}

func TestNormalizeUploadedBuildRequestContract(t *testing.T) {
	started := time.Date(2026, time.July, 13, 10, 11, 12, 0, time.UTC)
	options, err := normalizeUploadedBuildRequest(model.PackageBuildRequest{
		PackageType:     " SCRIPT ",
		AppName:         " sample-app ",
		Entrypoint:      " run.sh ",
		HealthcheckPath: "ignored-without-port",
	}, started)
	if err != nil {
		t.Fatal(err)
	}
	if options.packageType != PackageTypeSh || options.appName != "sample-app" || options.version != "web-20260713-101112-000" || options.runtimeType != "cpu" || options.port != 0 || options.healthPath != "" || options.entrypoint != "run.sh" {
		t.Fatalf("normalized options = %+v", options)
	}

	tests := []struct {
		name    string
		request model.PackageBuildRequest
		message string
	}{
		{name: "package type", request: model.PackageBuildRequest{}, message: "package_type must be one of go, python, node, binary, script"},
		{name: "app name", request: model.PackageBuildRequest{PackageType: PackageTypeSh}, message: "app_name must use 2-63 lowercase letters, numbers, or hyphens"},
		{name: "version", request: model.PackageBuildRequest{PackageType: PackageTypeSh, AppName: "sample-app", AppVersion: "bad version"}, message: "app_version must contain only letters, numbers, dot, underscore, or hyphen"},
		{name: "runtime", request: model.PackageBuildRequest{PackageType: PackageTypeSh, AppName: "sample-app", RuntimeType: "mock"}, message: "runtime_type must be cpu or gpu"},
		{name: "port", request: model.PackageBuildRequest{PackageType: PackageTypeSh, AppName: "sample-app", ServicePort: 65536}, message: "service_port must be between 1 and 65535"},
		{name: "health path", request: model.PackageBuildRequest{PackageType: PackageTypeSh, AppName: "sample-app", ServicePort: 8080, HealthcheckPath: "health"}, message: "healthcheck_path must be a relative HTTP path beginning with /"},
		{name: "entrypoint", request: model.PackageBuildRequest{PackageType: PackageTypeSh, AppName: "sample-app"}, message: "entrypoint is required"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := normalizeUploadedBuildRequest(test.request, started)
			appErr, ok := err.(*apperrors.AppError)
			if !ok || appErr.Code != model.ErrAppSpecInvalid || appErr.HTTPStatus != http.StatusBadRequest || appErr.Retryable || appErr.Message != test.message {
				t.Fatalf("error = %#v, want code=%s status=%d retryable=false message=%q", err, model.ErrAppSpecInvalid, http.StatusBadRequest, test.message)
			}
		})
	}
}

func TestBuildUploadedRejectsZipSymlinkAndCleansBuildDirectory(t *testing.T) {
	outputDir := filepath.Join(t.TempDir(), "packages")
	service := New(Config{OutputDir: outputDir})
	source := sourceZipSymlink(t, "run.sh", "outside.sh")
	_, err := service.BuildUploaded(context.Background(), model.PackageBuildRequest{
		PackageType: PackageTypeSh,
		AppName:     "unsafe-script",
		AppVersion:  "0.1.0",
		Entrypoint:  "run.sh",
	}, "unsafe.zip", bytes.NewReader(source))
	appErr, ok := err.(*apperrors.AppError)
	if !ok || appErr.Code != model.ErrAppSpecInvalid || appErr.HTTPStatus != http.StatusBadRequest || appErr.Message != "source ZIP is invalid, unsafe, or exceeds extraction limits" {
		t.Fatalf("error = %#v", err)
	}
	items, readErr := os.ReadDir(outputDir)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(items) != 0 {
		t.Fatalf("output directory contains partial files: %#v", items)
	}
}

func TestBuildUploadedRejectsUnsafeOrOversizedSource(t *testing.T) {
	t.Run("zip traversal", func(t *testing.T) {
		service := New(Config{OutputDir: filepath.Join(t.TempDir(), "packages")})
		source := sourceZip(t, map[string][]byte{"../escape.sh": []byte("echo unsafe\n")})
		_, err := service.BuildUploaded(context.Background(), model.PackageBuildRequest{
			PackageType: PackageTypeSh,
			AppName:     "unsafe-script",
			AppVersion:  "0.1.0",
			Entrypoint:  "escape.sh",
		}, "unsafe.zip", bytes.NewReader(source))
		assertAppErrorCode(t, err, model.ErrAppSpecInvalid)
	})

	t.Run("upload limit", func(t *testing.T) {
		service := New(Config{OutputDir: filepath.Join(t.TempDir(), "packages"), MaxUploadBytes: 4})
		_, err := service.BuildUploaded(context.Background(), model.PackageBuildRequest{
			PackageType: PackageTypeSh,
			AppName:     "large-script",
			AppVersion:  "0.1.0",
			Entrypoint:  "run.sh",
		}, "run.sh", strings.NewReader("12345"))
		assertAppErrorCode(t, err, model.ErrAppSpecInvalid)
	})
}

func TestBuildRejectsInvalidRequest(t *testing.T) {
	service := New(Config{})
	tests := []struct {
		name string
		req  model.PackageBuildRequest
	}{
		{name: "preset", req: model.PackageBuildRequest{Preset: "custom-command"}},
		{name: "port", req: model.PackageBuildRequest{Preset: PresetAIOpsGeon, ServicePort: 70000}},
		{name: "version", req: model.PackageBuildRequest{Preset: PresetAIOpsGeon, AppVersion: "bad version"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := service.Build(context.Background(), test.req)
			appErr, ok := err.(*apperrors.AppError)
			if !ok || appErr.Code != model.ErrAppSpecInvalid {
				t.Fatalf("error = %#v, want %s", err, model.ErrAppSpecInvalid)
			}
		})
	}
}

func TestBuildReportsMissingSource(t *testing.T) {
	service := New(Config{AIOpsRoot: filepath.Join(t.TempDir(), "missing"), OutputDir: t.TempDir()})
	_, err := service.Build(context.Background(), model.PackageBuildRequest{Preset: PresetAIOpsGeon})
	appErr, ok := err.(*apperrors.AppError)
	if !ok || appErr.Code != model.ErrAppArtifactNotFound {
		t.Fatalf("error = %#v, want %s", err, model.ErrAppArtifactNotFound)
	}
}

func assertAppErrorCode(t *testing.T, err error, code string) {
	t.Helper()
	appErr, ok := err.(*apperrors.AppError)
	if !ok || appErr.Code != code {
		t.Fatalf("error = %#v, want %s", err, code)
	}
}

func sourceZip(t *testing.T, files map[string][]byte) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for name, content := range files {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(content); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func sourceZipSymlink(t *testing.T, name, target string) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	header := &zip.FileHeader{Name: name, Method: zip.Store}
	header.SetMode(os.ModeSymlink | 0o777)
	entry, err := writer.CreateHeader(header)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := entry.Write([]byte(target)); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func createTestAIOpsSource(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	files := map[string]string{
		"go/service-control-api/go.mod":                          "module example.com/aiops-package-test\n\ngo 1.23\n",
		"go/service-control-api/cmd/service-control-api/main.go": "package main\n\nfunc main() {}\n",
		"config/agent_registry.json":                             "{}\n",
		"config/ops_llm_benchmark.json":                          "{}\n",
		"config/inference_optimization.json":                     "{}\n",
		"docs/submission/openapi_service_control.yaml":           "openapi: 3.0.3\n",
	}
	for name, content := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func readArchiveEntries(t *testing.T, path string) map[string]bool {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	gzipReader, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer gzipReader.Close()
	reader := tar.NewReader(gzipReader)
	entries := map[string]bool{}
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		entries[header.Name] = true
	}
	return entries
}

type archiveRecord struct {
	name    string
	mode    int64
	modTime time.Time
}

func readArchiveRecords(t *testing.T, path string) []archiveRecord {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	gzipReader, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer gzipReader.Close()
	reader := tar.NewReader(gzipReader)
	records := make([]archiveRecord, 0)
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) {
			return records
		}
		if err != nil {
			t.Fatal(err)
		}
		records = append(records, archiveRecord{name: header.Name, mode: header.Mode, modTime: header.ModTime})
	}
}
