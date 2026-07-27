package app

import (
	"bytes"
	"context"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

func TestRegisterPreservesOriginalApplicationDocument(t *testing.T) {
	raw := []byte(`{"kind":"AIApp","schema_version":"appspec.khu.ai/v1alpha1","metadata":{"name":"raw-app","version":"1"},"artifact":{"type":"script","uri":"file:///tmp/raw.sh"},"entrypoint":{"command":"bash"},"runtime":{"type":"cpu"},"resources":{"cpu":"1"}}`)
	service := NewService(store.NewMemory())
	response, err := service.Register(context.Background(), model.AppCreateRequest{
		AppSpec: model.AppSpec{
			SchemaVersion: "appspec.khu.ai/v1alpha1", Kind: "AIApp",
			Metadata:   model.Metadata{Name: "raw-app", Version: "1"},
			Artifact:   model.Artifact{Type: "script", URI: "file:///tmp/raw.sh"},
			Entrypoint: model.Entrypoint{Command: "bash"}, Runtime: model.AppRuntime{Type: "cpu"},
		},
		RawApplication: raw,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(response.OriginalApplication, raw) {
		t.Fatalf("original application changed: got %q want %q", response.OriginalApplication, raw)
	}
}
