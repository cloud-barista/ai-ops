package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPackageUploadTemporaryBodyLifetime(t *testing.T) {
	sourceDir := t.TempDir()
	sourcePath := filepath.Join(sourceDir, "run.sh")
	if err := os.WriteFile(sourcePath, []byte("#!/usr/bin/env bash\necho lifecycle\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	tempDir := t.TempDir()
	setPackageTempDir(t, tempDir)

	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		matches, err := filepath.Glob(filepath.Join(tempDir, "appdeploy-cli-package-*.multipart"))
		if err != nil {
			t.Fatal(err)
		}
		if len(matches) != 1 {
			t.Fatalf("temporary multipart files during request = %v", matches)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Fatal(err)
		}
		file, _, err := r.FormFile("source")
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()
		raw, err := io.ReadAll(file)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(raw, []byte("echo lifecycle")) {
			t.Fatalf("source body = %q", raw)
		}

		renamedPath := sourcePath + ".moved"
		if err := os.Rename(sourcePath, renamedPath); err != nil {
			t.Fatalf("source must be closed before API call: %v", err)
		}
		if err := os.Rename(renamedPath, sourcePath); err != nil {
			t.Fatal(err)
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		if err := json.NewEncoder(w).Encode(packageResponseForTest("script")); err != nil {
			t.Fatal(err)
		}
	})

	var output bytes.Buffer
	shell := New(api, strings.NewReader(""), &output)
	if err := shell.Run(context.Background(), []string{"packages", "build", "--type", "script", "--source", sourcePath}); err != nil {
		t.Fatal(err)
	}
	matches, err := filepath.Glob(filepath.Join(tempDir, "appdeploy-cli-package-*.multipart"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatalf("temporary multipart files after request = %v", matches)
	}
}

func TestPackageUploadValidatesSourceBeforeTemporaryBody(t *testing.T) {
	sourcePath := filepath.Join(t.TempDir(), "empty.sh")
	if err := os.WriteFile(sourcePath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	tempDir := t.TempDir()
	setPackageTempDir(t, tempDir)

	called := false
	api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	})
	var output bytes.Buffer
	shell := New(api, strings.NewReader(""), &output)
	err := shell.Run(context.Background(), []string{"packages", "build", "--type", "script", "--source", sourcePath})
	if err == nil || !strings.Contains(err.Error(), "source는 비어 있지 않은 일반 파일이어야 합니다") {
		t.Fatalf("error = %v", err)
	}
	if called {
		t.Fatal("API was called for an invalid source")
	}
	matches, globErr := filepath.Glob(filepath.Join(tempDir, "appdeploy-cli-package-*.multipart"))
	if globErr != nil {
		t.Fatal(globErr)
	}
	if len(matches) != 0 {
		t.Fatalf("temporary multipart files for invalid source = %v", matches)
	}
	if err := os.Remove(sourcePath); err != nil {
		t.Fatalf("invalid source file was not closed: %v", err)
	}
}

func setPackageTempDir(t *testing.T, path string) {
	t.Helper()
	for _, name := range []string{"TMP", "TEMP", "TMPDIR"} {
		t.Setenv(name, path)
	}
}
