package main

import (
	"net"
	"net/http"

	"kyunghee-aiops/llmop-api/internal/profileapi"

	"github.com/rs/zerolog/log"
	"github.com/spf13/viper"
)

// @title LLM_Op ApplicationProfile and Ops LLM API
// @version 1.0
// @description Generate a bounded ApplicationProfile, rank Ops LLM candidates, and interpret an operations request with a configured Ops LLM. Deployment planning and execution remain outside this service.
// @license.name Apache 2.0
// @license.url https://www.apache.org/licenses/LICENSE-2.0.html
// @BasePath /
// @securityDefinitions.apikey OpsLLMBearer
// @in header
// @name Authorization
// @description Bearer token configured for Ops LLM interpretation.
func main() {
	viper.SetDefault("PORT", "8080")
	_ = viper.BindEnv("PORT")
	port := viper.GetString("PORT")

	config := profileapi.NewConfig()
	if err := profileapi.ValidateConfig(config); err != nil {
		log.Fatal().Err(err).Msg("invalid llmop-api configuration")
	}
	address := net.JoinHostPort(config.BindAddress, port)
	server := profileapi.NewServer(config)
	log.Info().Str("address", address).Msg("starting llmop-api")
	if err := server.Start(address); err != nil && err != http.ErrServerClosed {
		log.Fatal().Err(err).Str("address", address).Msg("llmop-api stopped")
	}
}
