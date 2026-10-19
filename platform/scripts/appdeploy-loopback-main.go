package main

import (
    "github.com/khu/ai-app-deployer/internal/server"
    "github.com/rs/zerolog/log"
)

// Built through a Go overlay in the selected AppDeploy module; its source is unchanged.
func main() {
    srv, err := server.New()
    if err != nil { log.Fatal().Err(err).Msg("server initialization failed") }
    if err := srv.Start("127.0.0.1:18150"); err != nil { log.Fatal().Err(err).Msg("server stopped") }
}
