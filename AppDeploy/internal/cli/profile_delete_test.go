package cli

import (
	"bytes"
	"context"
	"net/http"
	"strings"
	"testing"
)

func TestProfileDeleteRequiresConfirmationBeforeAPICall(t *testing.T) {
	tests := [][]string{
		{"runtimes", "delete", "rt-delete-me"},
		{"runtimes", "remove", "rt-delete-me", "--force"},
		{"targets", "delete", "target-delete-me"},
		{"targets", "rm", "target-delete-me", "-f"},
	}
	for _, args := range tests {
		t.Run(strings.Join(args, "_"), func(t *testing.T) {
			called := false
			api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true })
			err := New(api, strings.NewReader(""), &bytes.Buffer{}).Run(context.Background(), args)
			if err == nil || !strings.Contains(err.Error(), "delete <") || !strings.Contains(err.Error(), "--yes") {
				t.Fatalf("error = %v, want confirmed delete usage", err)
			}
			if called {
				t.Fatal("API was called without --yes or -y")
			}
		})
	}
}

func TestProfileDeleteAliasesCallEscapedDeleteEndpoints(t *testing.T) {
	tests := []struct {
		command      string
		operation    string
		confirmation string
		path         string
		profileType  string
	}{
		{command: "runtimes", operation: "delete", confirmation: "--yes", path: "/api/v1/runtime-profiles/rt%20delete", profileType: "runtime"},
		{command: "runtimes", operation: "rm", confirmation: "-y", path: "/api/v1/runtime-profiles/rt%20delete", profileType: "runtime"},
		{command: "targets", operation: "remove", confirmation: "--yes", path: "/api/v1/target-profiles/target%20delete", profileType: "target"},
		{command: "targets", operation: "delete", confirmation: "-y", path: "/api/v1/target-profiles/target%20delete", profileType: "target"},
	}
	for _, test := range tests {
		t.Run(test.command+"_"+test.operation, func(t *testing.T) {
			api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodDelete {
					t.Fatalf("method = %s, want DELETE", r.Method)
				}
				if path := r.URL.EscapedPath(); path != test.path {
					t.Fatalf("escaped path = %q, want %q", path, test.path)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"profile_type":"` + test.profileType + `","profile_id":"deleted-profile","name":"Deleted profile","deleted":true,"inventory_deleted":false,"deleted_at":"2026-07-13T00:00:00Z"}`))
			})

			id := "rt delete"
			if test.command == "targets" {
				id = "target delete"
			}
			var output bytes.Buffer
			err := New(api, strings.NewReader(""), &output).Run(context.Background(), []string{test.command, test.operation, id, test.confirmation})
			if err != nil {
				t.Fatal(err)
			}
			for _, expected := range []string{`"profile_type": "` + test.profileType + `"`, `"deleted": true`} {
				if !strings.Contains(output.String(), expected) {
					t.Fatalf("delete output does not contain %q:\n%s", expected, output.String())
				}
			}
		})
	}
}

func TestHelpListsConfirmedProfileDeletion(t *testing.T) {
	var output bytes.Buffer
	if err := New(http.NotFoundHandler(), strings.NewReader(""), &output).Run(context.Background(), []string{"help"}); err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		"targets delete <target-id> --yes",
		"참조 배포가 없거나 모두 STOPPED인 Target Profile과 readiness inventory 삭제",
	} {
		if !strings.Contains(output.String(), expected) {
			t.Fatalf("help does not include %q:\n%s", expected, output.String())
		}
	}
}
