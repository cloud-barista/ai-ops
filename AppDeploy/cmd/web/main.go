package main

import (
	"path/filepath"
	"strings"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/server"
	"github.com/rs/zerolog/log"
)

func main() {
	settings, err := config.Load()
	if err != nil {
		log.Fatal().Err(err).Str("component", "web-console").Msg("configuration loading failed")
	}
	if settings.StorePath == "" {
		settings.StorePath = filepath.Join("data", "appdeployer-store.json")
	}

	srv, err := server.NewWithConfig(settings)
	if err != nil {
		log.Fatal().Err(err).Str("component", "web-console").Msg("web console initialization failed")
	}
	address := srv.Server.Addr
	if address == "" {
		address = ":8080"
	}
	log.Info().
		Str("component", "web-console").
		Str("url", "http://localhost"+normalizeAddress(address)).
		Msg("web console ready")
	if err := srv.Start(address); err != nil {
		log.Fatal().Err(err).Str("component", "web-console").Msg("web console stopped")
	}
}

func normalizeAddress(address string) string {
	address = strings.TrimSpace(address)
	if strings.HasPrefix(address, ":") {
		return address
	}
	return ":" + address
}
