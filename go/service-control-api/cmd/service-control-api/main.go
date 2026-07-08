package main

import (
	"net/http"

	"kyunghee-aiops/service-control-api/internal/api"

	"github.com/rs/zerolog/log"
	"github.com/spf13/viper"
)

func main() {
	viper.SetDefault("PORT", "8080")
	_ = viper.BindEnv("PORT")
	port := viper.GetString("PORT")

	server := api.NewServer(api.NewServerConfig())
	log.Info().Str("port", port).Msg("starting service-control-api")
	if err := server.Start(":" + port); err != nil && err != http.ErrServerClosed {
		log.Fatal().Err(err).Str("port", port).Msg("service-control-api stopped")
	}
}
