package api

import (
	"net/http"

	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
)

const (
	pathHealthz        = "/healthz"
	pathOpenAPI        = "/openapi.yaml"
	pathAgents         = "/api/v1/agents"
	pathOpsLLMSelect   = "/api/v1/ops-llm/select"
	pathAppPlacement   = "/api/v1/apps/placement"
	pathDeploymentPlan = "/api/v1/apps/deployment-plan"
	pathServiceOpsRun  = "/api/v1/service-operations/run"
)

func NewServer(config ServerConfig) *echo.Echo {
	service := NewService(config)
	server := echo.New()
	server.HideBanner = true
	server.Use(middleware.Recover())

	server.GET(pathHealthz, func(context echo.Context) error {
		return context.JSON(http.StatusOK, map[string]any{
			"status":  "ok",
			"service": "service-control-api",
		})
	})

	server.GET(pathOpenAPI, func(context echo.Context) error {
		return context.File(config.OpenAPIPath)
	})

	server.GET(pathAgents, func(context echo.Context) error {
		result, err := service.ListAgents()
		if err != nil {
			return jsonError(context, http.StatusInternalServerError, err)
		}
		return context.JSON(http.StatusOK, result)
	})

	server.POST(pathOpsLLMSelect, func(context echo.Context) error {
		var request OpsLLMSelectRequest
		if err := context.Bind(&request); err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		result, err := service.SelectOpsLLM(request.Policy)
		if err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		return context.JSON(http.StatusOK, result)
	})

	server.POST(pathAppPlacement, func(context echo.Context) error {
		var request WorkloadRequest
		if err := context.Bind(&request); err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		result, err := service.RecommendPlacement(request.Workload)
		if err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		return context.JSON(http.StatusOK, result)
	})

	server.POST(pathDeploymentPlan, func(context echo.Context) error {
		var request WorkloadRequest
		if err := context.Bind(&request); err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		result, err := service.BuildDeploymentPlan(request.Workload)
		if err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		return context.JSON(http.StatusOK, result)
	})

	server.POST(pathServiceOpsRun, func(context echo.Context) error {
		var request ServiceOperationsRequest
		if err := context.Bind(&request); err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		result, err := service.RunServiceOperations(request)
		if err != nil {
			return jsonError(context, http.StatusBadRequest, err)
		}
		return context.JSON(http.StatusOK, result)
	})

	return server
}

func jsonError(context echo.Context, status int, err error) error {
	return context.JSON(status, map[string]any{
		"valid": false,
		"error": err.Error(),
	})
}
