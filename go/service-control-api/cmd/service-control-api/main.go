package main

import (
	"net/http"

	"kyunghee-aiops/service-control-api/internal/api"

	"github.com/rs/zerolog/log"
	"github.com/spf13/viper"
)

// @title AI-Ops Service Control API
// @version 0.1
// @description Go Echo API for AI LLM operation management, agent registry management, CPU/GPU VM placement, and AI application deployment-control planning.
// @license.name Apache 2.0
// @license.url https://www.apache.org/licenses/LICENSE-2.0.html
// @BasePath /
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
