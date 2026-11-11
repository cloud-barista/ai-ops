package main

import (
	"net"
	"net/http"

	"kyunghee-aiops/service-control-api/internal/api"

	"github.com/rs/zerolog/log"
	"github.com/spf13/viper"
)

// @title AI-MCMP Deployment Agent API
// @version 1.0
// @description Accept an ApplicationProfile and ResourceRecommendation, validate their correlation, run a bounded deployment decision, and generate a guarded DeploymentPlan.
// @license.name Apache 2.0
// @license.url https://www.apache.org/licenses/LICENSE-2.0.html
// @BasePath /
// @securityDefinitions.apikey AIAppBearer
// @in header
// @name Authorization
// @description Bearer token forwarded by the AI-App gateway.
// @securityDefinitions.apikey AIAppEngineRegistryBearer
// @in header
// @name Authorization
// @description Dedicated Bearer token for the Innogrid engine registration.
// @securityDefinitions.apikey FrameworkPrincipalBearer
// @in header
// @name Authorization
// @description Bearer token for a principal in the framework access policy.
// @securityDefinitions.apikey AutonomyAdminToken
// @in header
// @name Authorization
// @description Operator Bearer token for approving a prepared AI-App request.
func main() {
	viper.SetDefault("PORT", "8080")
	_ = viper.BindEnv("PORT")
	port := viper.GetString("PORT")

	config := api.NewServerConfig()
	if err := api.ValidateServerConfig(config); err != nil {
		log.Fatal().Err(err).Msg("invalid service-control-api configuration")
	}
	address := net.JoinHostPort(config.BindAddress, port)
	buildServer, mode := api.NewFocusedServer, "focused"
	switch {
	case config.LegacyAPIEnabled:
		buildServer, mode = api.NewServer, "legacy"
	case config.AppDeployPoCEnabled:
		buildServer, mode = api.NewAppDeployPoCServer, "appdeploy-poc"
	}
	log.Info().Str("address", address).Str("api_mode", mode).Msg("starting deployment-agent")
	if err := buildServer(config).Start(address); err != nil && err != http.ErrServerClosed {
		log.Fatal().Err(err).Str("address", address).Msg("service-control-api stopped")
	}
}
