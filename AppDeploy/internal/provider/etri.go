package provider

import (
	"context"
	"net/http"
	"strings"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

// ETRIConfig intentionally contains only transport-level configuration. No
// endpoint path or request schema is invented until the ETRI contract is
// supplied by the partner team.
type ETRIConfig struct {
	ResourceEndpoint  string
	PlacementEndpoint string
	CredentialRef     string
	Timeout           time.Duration
}

type ETRIResourceMetadataAdapter struct{ config ETRIConfig }

func NewETRIResourceMetadataAdapter(config ETRIConfig) (*ETRIResourceMetadataAdapter, error) {
	if strings.TrimSpace(config.ResourceEndpoint) == "" {
		return nil, missingETRIConfig("ETRI_RESOURCE_ENDPOINT")
	}
	return &ETRIResourceMetadataAdapter{config: normalizeETRIConfig(config)}, nil
}

func (a *ETRIResourceMetadataAdapter) ListResources(ctx context.Context, filter model.ResourceFilter) ([]model.NodeProfile, error) {
	return nil, etriStubError("resource metadata API contract is not implemented")
}

func (a *ETRIResourceMetadataAdapter) GetResource(ctx context.Context, id string) (model.NodeProfile, error) {
	return model.NodeProfile{}, etriStubError("resource metadata API contract is not implemented")
}

type ETRIPlacementAdapter struct{ config ETRIConfig }

func NewETRIPlacementAdapter(config ETRIConfig) (*ETRIPlacementAdapter, error) {
	if strings.TrimSpace(config.PlacementEndpoint) == "" {
		return nil, missingETRIConfig("ETRI_PLACEMENT_ENDPOINT")
	}
	return &ETRIPlacementAdapter{config: normalizeETRIConfig(config)}, nil
}

func (a *ETRIPlacementAdapter) Place(ctx context.Context, req model.PlacementRequest) (model.PlacementDecision, error) {
	return model.PlacementDecision{}, etriStubError("placement API contract is not implemented")
}

func missingETRIConfig(name string) error {
	return apperrors.WithDetails(model.ErrAIInfraAPIFailed, "ETRI provider configuration is incomplete", http.StatusBadGateway, false, map[string]any{"missing": name})
}

func etriStubError(message string) error {
	return apperrors.WithDetails(model.ErrAIInfraAPIFailed, message, http.StatusBadGateway, true, map[string]any{"provider": "etri"})
}

func normalizeETRIConfig(config ETRIConfig) ETRIConfig {
	if config.Timeout <= 0 {
		config.Timeout = 30 * time.Second
	}
	return config
}
