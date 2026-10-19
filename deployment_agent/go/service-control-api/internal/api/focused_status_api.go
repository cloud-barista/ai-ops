package api

import (
	"net/http"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"
)

func (handler restHandler) RestGetFocusedHealthz(context echo.Context) error {
	return context.JSON(http.StatusOK, map[string]any{
		"status":             "ok",
		"service":            "deployment-agent",
		"role":               "validated_deployment_decision_and_planning",
		"input":              "application_profile_plus_resource_recommendation",
		"legacy_api_enabled": false,
	})
}

func (handler restHandler) RestGetFocusedOpenAPI(context echo.Context) error {
	return context.File(handler.config.FocusedOpenAPIPath)
}

func logFocusedError(context echo.Context, status int, code string, err error) {
	log.Error().Err(err).
		Str("method", context.Request().Method).
		Str("path", context.Path()).
		Int("status", status).
		Str("code", code).
		Msg("focused deployment-agent request rejected")
}
