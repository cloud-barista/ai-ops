package server

import (
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/store"
)

func TestProviderSelectionLocalAndETRIConfiguration(t *testing.T) {
	resourceProvider, placementProvider, err := buildProviders(config.Settings{ResourceProvider: "local", PlacementProvider: "local"}, store.NewMemory())
	if err != nil || resourceProvider == nil || placementProvider == nil {
		t.Fatalf("local provider selection failed: resource=%T placement=%T err=%v", resourceProvider, placementProvider, err)
	}
	_, _, err = buildProviders(config.Settings{ResourceProvider: "etri", PlacementProvider: "local"}, store.NewMemory())
	if err == nil || !strings.Contains(err.Error(), "ETRI") {
		t.Fatalf("missing ETRI resource configuration error = %v", err)
	}
	_, _, err = buildProviders(config.Settings{ResourceProvider: "local", PlacementProvider: "etri"}, store.NewMemory())
	if err == nil || !strings.Contains(err.Error(), "ETRI") {
		t.Fatalf("missing ETRI placement configuration error = %v", err)
	}
}
