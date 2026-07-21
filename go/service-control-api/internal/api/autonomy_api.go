package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/autonomy"
)

const maxAutonomyConfigBytes = 64 << 10

func (handler restHandler) RestGetAutonomyStatus(context echo.Context) error {
	return context.JSON(http.StatusOK, handler.service.autonomyManager.Status())
}

func (handler restHandler) RestPutAutonomyConfig(context echo.Context) error {
	config, err := decodeAutonomyConfig(context)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Autonomy configuration is invalid", err)
	}
	if err := handler.service.autonomyManager.Configure(config); err != nil {
		return jsonError(context, http.StatusBadRequest, "Autonomy configuration could not be applied", err)
	}
	return context.JSON(http.StatusOK, handler.service.autonomyManager.Status())
}

func (handler restHandler) RestPostAutonomyStart(context echo.Context) error {
	status, err := handler.service.autonomyManager.Start()
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Autonomy loop could not be started", err)
	}
	return context.JSON(http.StatusOK, status)
}

func (handler restHandler) RestPostAutonomyStop(context echo.Context) error {
	return context.JSON(http.StatusOK, handler.service.autonomyManager.Stop())
}

func (handler restHandler) RestPostAutonomyEmergencyStop(context echo.Context) error {
	return context.JSON(http.StatusOK, handler.service.autonomyManager.EmergencyStop())
}

func (handler restHandler) RestPostAutonomyCycle(context echo.Context) error {
	return context.JSON(http.StatusOK, handler.service.autonomyManager.RunCycle(context.Request().Context()))
}

func (handler restHandler) RestGetAutonomyEvents(context echo.Context) error {
	events := handler.service.autonomyManager.Events()
	return context.JSON(http.StatusOK, map[string]any{"count": len(events), "events": events})
}

func decodeAutonomyConfig(context echo.Context) (autonomy.Config, error) {
	var config autonomy.Config
	content, err := io.ReadAll(io.LimitReader(context.Request().Body, maxAutonomyConfigBytes+1))
	if err != nil {
		return config, err
	}
	if len(content) > maxAutonomyConfigBytes {
		return config, fmt.Errorf("configuration exceeds %d bytes", maxAutonomyConfigBytes)
	}
	var raw map[string]any
	if err := json.Unmarshal(content, &raw); err != nil {
		return config, err
	}
	if containsSecretLikeKey(raw) {
		return config, fmt.Errorf("secret-shaped fields are forbidden")
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return config, err
	}
	if err := ensureJSONDecoderEOF(decoder); err != nil {
		return config, err
	}
	return config, config.Validate()
}

func ensureJSONDecoderEOF(decoder *json.Decoder) error {
	var extra any
	err := decoder.Decode(&extra)
	if err == io.EOF {
		return nil
	}
	if err == nil {
		return fmt.Errorf("multiple JSON values are not allowed")
	}
	return err
}
