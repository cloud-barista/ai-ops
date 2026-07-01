package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"

	appsvc "github.com/khu/ai-app-deployer/internal/app"
	depsvc "github.com/khu/ai-app-deployer/internal/deployment"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	infsvc "github.com/khu/ai-app-deployer/internal/inference"
	"github.com/khu/ai-app-deployer/internal/model"
	monsvc "github.com/khu/ai-app-deployer/internal/monitoring"
	profilesvc "github.com/khu/ai-app-deployer/internal/profile"
	"github.com/khu/ai-app-deployer/internal/requestid"
	ressvc "github.com/khu/ai-app-deployer/internal/resource"
	"github.com/labstack/echo/v4"
)

type API struct {
	apps        *appsvc.Service
	profiles    *profilesvc.Service
	deployments *depsvc.Service
	resources   *ressvc.Service
	monitoring  *monsvc.Service
	inference   *infsvc.Service
}

func New(apps *appsvc.Service, profiles *profilesvc.Service, deployments *depsvc.Service, resources *ressvc.Service, monitoring *monsvc.Service, inference *infsvc.Service) *API {
	return &API{apps: apps, profiles: profiles, deployments: deployments, resources: resources, monitoring: monitoring, inference: inference}
}

func (a *API) Register(e *echo.Echo) {
	e.GET("/openapi.yaml", a.openAPIYAML)
	e.GET("/swagger", a.swagger)
	e.GET("/swagger/", a.swagger)

	v1 := e.Group("/api/v1")
	v1.GET("/healthz", a.healthz)
	v1.GET("/readiness", a.readiness)
	v1.POST("/apps", a.createApp)
	v1.GET("/apps", a.listApps)
	v1.GET("/apps/:app_id", a.getApp)
	v1.POST("/runtime-profiles", a.createRuntimeProfile)
	v1.GET("/runtime-profiles", a.listRuntimeProfiles)
	v1.POST("/target-profiles", a.createTargetProfile)
	v1.GET("/target-profiles", a.listTargetProfiles)
	v1.POST("/resources/check", a.checkResource)
	v1.GET("/resources/inventory", a.listInventory)
	v1.POST("/deployments", a.createDeployment)
	v1.GET("/deployments", a.listDeployments)
	v1.GET("/deployments/:deployment_id", a.getDeployment)
	v1.GET("/deployments/:deployment_id/logs", a.getDeploymentLogs)
	v1.POST("/deployments/:deployment_id/metrics", a.createDeploymentMetric)
	v1.GET("/deployments/:deployment_id/metrics", a.listDeploymentMetrics)
	v1.POST("/deployments/:deployment_id/stop", a.stopDeployment)
	v1.GET("/inference/:deployment_id/health", a.inferenceHealth)
	v1.POST("/inference/:deployment_id/invoke", a.invokeInference)
	v1.GET("/monitoring/summary", a.monitoringSummary)
	v1.GET("/monitoring/runtime-health", a.monitoringRuntimeHealth)
	v1.GET("/monitoring/alarms", a.monitoringAlarms)
	v1.GET("/monitoring/metrics", a.monitoringMetrics)
}

func (a *API) openAPIYAML(c echo.Context) error {
	return c.File(projectFile("contracts/openapi/openapi.yaml"))
}

func (a *API) swagger(c echo.Context) error {
	return c.File(projectFile("docs/api/openapi.html"))
}

func (a *API) healthz(c echo.Context) error {
	return c.JSON(http.StatusOK, model.HealthResponse{
		RequestID: requestID(c),
		Status:    "ok",
	})
}

func (a *API) readiness(c echo.Context) error {
	return c.JSON(http.StatusOK, model.ReadinessResponse{
		RequestID: requestID(c),
		Status:    "ready",
		Checks: map[string]string{
			"repository":      "ready",
			"runtime_adapter": "mock,cpu_vm,gpu_vm,etri_aiinfra",
			"external_api":    "etri_mock",
		},
	})
}

func (a *API) createApp(c echo.Context) error {
	req, err := bindAppCreateRequest(c)
	if err != nil {
		return a.error(c, apperrors.New(model.ErrAppSpecInvalid, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.apps.Register(c.Request().Context(), req)
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusCreated, resp)
}

func (a *API) listApps(c echo.Context) error {
	items, err := a.apps.List(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "apps", items))
}

func (a *API) getApp(c echo.Context) error {
	resp, err := a.apps.Get(c.Request().Context(), c.Param("app_id"))
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusOK, resp)
}

func (a *API) createRuntimeProfile(c echo.Context) error {
	var req model.RuntimeProfile
	if err := c.Bind(&req); err != nil {
		return a.error(c, apperrors.New(model.ErrRuntimeProfileInvalid, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.profiles.CreateRuntime(c.Request().Context(), req)
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusCreated, profileResponse(c, resp.RuntimeProfileID, resp))
}

func (a *API) listRuntimeProfiles(c echo.Context) error {
	items, err := a.profiles.ListRuntimes(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "runtime_profiles", items))
}

func (a *API) createTargetProfile(c echo.Context) error {
	var req model.TargetProfile
	if err := c.Bind(&req); err != nil {
		return a.error(c, apperrors.New(model.ErrTargetProfileInvalid, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.profiles.CreateTarget(c.Request().Context(), req)
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusCreated, profileResponse(c, resp.TargetProfileID, resp))
}

func (a *API) listTargetProfiles(c echo.Context) error {
	items, err := a.profiles.ListTargets(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "target_profiles", items))
}

func (a *API) checkResource(c echo.Context) error {
	var req model.ResourceCheckRequest
	if err := c.Bind(&req); err != nil {
		return a.error(c, apperrors.New(model.ErrTargetProfileInvalid, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.resources.Check(c.Request().Context(), req.TargetProfileID)
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	resp.RuntimeProfileID = req.RuntimeProfileID
	return c.JSON(http.StatusOK, resp)
}

func (a *API) listInventory(c echo.Context) error {
	items, err := a.resources.ListInventory(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "inventory", items))
}

func (a *API) createDeployment(c echo.Context) error {
	var req model.DeploymentCreateRequest
	if err := c.Bind(&req); err != nil {
		return a.error(c, apperrors.New(model.ErrDeploymentFailed, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.deployments.Create(c.Request().Context(), req)
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusAccepted, resp)
}

func (a *API) listDeployments(c echo.Context) error {
	items, err := a.deployments.List(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "deployments", items))
}

func (a *API) getDeployment(c echo.Context) error {
	resp, err := a.deployments.Get(c.Request().Context(), c.Param("deployment_id"))
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusOK, resp)
}

func (a *API) getDeploymentLogs(c echo.Context) error {
	items, err := a.deployments.Logs(c.Request().Context(), c.Param("deployment_id"), c.QueryParam("stage"))
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, map[string]any{
		"request_id":    requestID(c),
		"deployment_id": c.Param("deployment_id"),
		"items":         items,
		"logs":          items,
	})
}

func (a *API) createDeploymentMetric(c echo.Context) error {
	var req model.InferenceMetricCreateRequest
	if err := c.Bind(&req); err != nil {
		return a.error(c, apperrors.New(model.ErrDeploymentFailed, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.monitoring.RecordMetric(c.Request().Context(), c.Param("deployment_id"), req)
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusCreated, resp)
}

func (a *API) listDeploymentMetrics(c echo.Context) error {
	items, err := a.monitoring.Metrics(c.Request().Context(), c.Param("deployment_id"))
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "metrics", items))
}

func (a *API) stopDeployment(c echo.Context) error {
	resp, err := a.deployments.Stop(c.Request().Context(), c.Param("deployment_id"))
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusAccepted, resp)
}

func (a *API) inferenceHealth(c echo.Context) error {
	resp, err := a.inference.Health(c.Request().Context(), c.Param("deployment_id"))
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusOK, resp)
}

func (a *API) invokeInference(c echo.Context) error {
	var req model.InferenceInvokeRequest
	if err := c.Bind(&req); err != nil {
		return a.error(c, apperrors.New(model.ErrRuntimeFailed, "invalid JSON request body", http.StatusBadRequest, false))
	}
	resp, err := a.inference.Invoke(c.Request().Context(), c.Param("deployment_id"), req)
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusOK, resp)
}

func (a *API) monitoringSummary(c echo.Context) error {
	resp, err := a.monitoring.Summary(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	resp.RequestID = requestID(c)
	return c.JSON(http.StatusOK, resp)
}

func (a *API) monitoringRuntimeHealth(c echo.Context) error {
	items, err := a.monitoring.RuntimeHealth(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "runtime_health", items))
}

func (a *API) monitoringAlarms(c echo.Context) error {
	items, err := a.monitoring.Alarms(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "alarms", items))
}

func (a *API) monitoringMetrics(c echo.Context) error {
	items, err := a.monitoring.Metrics(c.Request().Context(), "")
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, listResponse(c, "metrics", items))
}

func (a *API) error(c echo.Context, err error) error {
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		appErr = apperrors.New("UNKNOWN", err.Error(), http.StatusInternalServerError, false)
	}
	status := appErr.HTTPStatus
	if status == 0 {
		status = http.StatusInternalServerError
	}
	return c.JSON(status, model.ErrorResponse{
		RequestID: requestid.FromContext(c.Request().Context()),
		Error: model.ErrorObject{
			Code:      appErr.Code,
			Message:   appErr.Message,
			Details:   appErr.Details,
			Retryable: appErr.Retryable,
		},
	})
}

func bindAppCreateRequest(c echo.Context) (model.AppCreateRequest, error) {
	raw, err := io.ReadAll(c.Request().Body)
	if err != nil {
		return model.AppCreateRequest{}, err
	}
	var wrapped model.AppCreateRequest
	if err := json.Unmarshal(raw, &wrapped); err != nil {
		return model.AppCreateRequest{}, err
	}
	if wrapped.AppSpec.SchemaVersion != "" || wrapped.AppSpec.Kind != "" {
		return wrapped, nil
	}
	var direct model.AppSpec
	if err := json.Unmarshal(raw, &direct); err != nil {
		return model.AppCreateRequest{}, err
	}
	return model.AppCreateRequest{AppSpec: direct}, nil
}

func requestID(c echo.Context) string {
	return requestid.FromContext(c.Request().Context())
}

func listResponse(c echo.Context, alias string, items any) map[string]any {
	return map[string]any{
		"request_id": requestID(c),
		"items":      items,
		alias:        items,
	}
}

func profileResponse(c echo.Context, id string, profile any) map[string]any {
	resp := map[string]any{
		"request_id": requestID(c),
		"profile_id": id,
	}
	raw, err := json.Marshal(profile)
	if err != nil {
		return resp
	}
	var fields map[string]any
	if err := json.Unmarshal(raw, &fields); err != nil {
		return resp
	}
	for key, value := range fields {
		resp[key] = value
	}
	return resp
}

func projectFile(rel string) string {
	wd, err := os.Getwd()
	if err != nil {
		return rel
	}
	for {
		candidate := filepath.Join(wd, rel)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(wd)
		if parent == wd {
			return rel
		}
		wd = parent
	}
}
