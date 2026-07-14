package cli

import (
	"bytes"
	"context"
	"net/http"
	"strings"
	"testing"
)

func TestAppsDeleteRequiresConfirmationFlagBeforeAPICall(t *testing.T) {
	tests := [][]string{
		{"apps", "delete", "app-delete-me"},
		{"apps", "remove", "app-delete-me", "--force"},
		{"apps", "rm", "app-delete-me", "-f"},
	}
	for _, args := range tests {
		t.Run(strings.Join(args, "_"), func(t *testing.T) {
			called := false
			api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				called = true
			})
			var output bytes.Buffer
			shell := New(api, strings.NewReader(""), &output)
			err := shell.Run(context.Background(), args)
			if err == nil || !strings.Contains(err.Error(), "apps delete <app-id> --yes") {
				t.Fatalf("error = %v", err)
			}
			if called {
				t.Fatal("API was called without --yes or -y")
			}
		})
	}
}

func TestAppsDeleteAliasesCallEscapedDeleteEndpoint(t *testing.T) {
	tests := []struct {
		operation    string
		confirmation string
	}{
		{operation: "delete", confirmation: "--yes"},
		{operation: "remove", confirmation: "-y"},
		{operation: "rm", confirmation: "--yes"},
	}
	for _, test := range tests {
		t.Run(test.operation, func(t *testing.T) {
			api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodDelete {
					t.Fatalf("method = %s, want DELETE", r.Method)
				}
				if path := r.URL.EscapedPath(); path != "/api/v1/apps/app%20delete" {
					t.Fatalf("escaped path = %q", path)
				}
				w.Header().Set("Content-Type", "application/json")
				if _, err := w.Write([]byte(`{"app_id":"app delete","deleted":true}`)); err != nil {
					t.Fatal(err)
				}
			})

			var output bytes.Buffer
			shell := New(api, strings.NewReader(""), &output)
			if err := shell.Run(context.Background(), []string{"apps", test.operation, "app delete", test.confirmation}); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(output.String(), `"deleted": true`) {
				t.Fatalf("delete output:\n%s", output.String())
			}
		})
	}
}

func TestHelpListsConfirmedAppDeletion(t *testing.T) {
	var output bytes.Buffer
	shell := New(http.NotFoundHandler(), strings.NewReader(""), &output)
	if err := shell.Run(context.Background(), []string{"help"}); err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{"apps delete <app-id> --yes", "참조 배포가 없거나 모두 STOPPED인 App 등록 삭제"} {
		if !strings.Contains(output.String(), expected) {
			t.Fatalf("help does not include %q:\n%s", expected, output.String())
		}
	}
}
