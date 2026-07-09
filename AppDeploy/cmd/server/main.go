package main

import (
	"github.com/khu/ai-app-deployer/internal/server"
	"github.com/rs/zerolog/log"
)

func main() {
	srv, err := server.New()
	if err != nil {
		log.Fatal().Err(err).Msg("server initialization failed")
	}
	port := srv.Server.Addr
	if port == "" {
		port = ":8080"
	}
	if err := srv.Start(port); err != nil {
		log.Fatal().Err(err).Msg("server stopped")
	}
}
