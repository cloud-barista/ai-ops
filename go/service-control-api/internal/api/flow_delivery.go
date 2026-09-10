package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/labstack/echo/v4"
	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

type ExternalFlowRequest struct {
	ApplicationContext     agentcontrol.ApplicationContextEnvelope     `json:"application_context"`
	ResourceRecommendation agentcontrol.ResourceRecommendationEnvelope `json:"resource_recommendation"`
	DecisionAgent          string                                      `json:"decision_agent,omitempty"`
}

type FlowDeliveryRequest struct {
	AppVersionID           string `json:"app_version_id" validate:"required"`
	AcceptProjectionLimits bool   `json:"accept_projection_limits"`
}

type FlowDelivery struct {
	CorrelationID     string                               `json:"correlation_id"`
	DecisionID        string                               `json:"decision_id"`
	Revision          int                                  `json:"revision"`
	Status            string                               `json:"status"`
	ManifestSHA256    string                               `json:"manifest_sha256"`
	Request           appdeploy.DeploymentCreateRequest    `json:"request"`
	Deployment        *appdeploy.DeploymentResponse        `json:"deployment,omitempty"`
	Metrics           *appdeploy.DeploymentMetricsResponse `json:"metrics,omitempty"`
	Flow              *agentcontrol.Flow                   `json:"flow,omitempty"`
	UpdatedAt         string                               `json:"updated_at"`
	DestinationSHA256 string                               `json:"destination_sha256"`
	Limitations       []string                             `json:"limitations"`
}

type flowDeliveryStore struct{ mu sync.Mutex }

// RestPostExternalFlow godoc
// @Summary Receive analyzed external inputs atomically
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param request body ExternalFlowRequest true "Matching Common JSON input pair"
// @Success 202 {object} agentcontrol.Flow
// @Failure 400 {object} ErrorResponse
// @Router /api/v1/agent-control/external-flows [post]
func (h restHandler) RestPostExternalFlow(c echo.Context) error {
	var request ExternalFlowRequest
	if err := c.Bind(&request); err != nil {
		return jsonError(c, 400, "Invalid external input pair", err)
	}
	flow, err := h.service.agentControl.ReceiveExternalInputs(c.Request().Context(), request.ApplicationContext, request.ResourceRecommendation, request.DecisionAgent)
	if err != nil {
		return jsonError(c, 400, "External input pair was rejected", err)
	}
	return c.JSON(http.StatusAccepted, flow)
}

// RestPostFlowDelivery godoc
// @Summary Prepare or submit an approved initial Flow to AppDeployer
// @Description Server configuration enables submission. Requests are constructed from stored approved Revision 1 and an authoritative App Registry match. Attempts are persisted before POST; uncertain attempts are never automatically retried.
// @Tags AI Application Automation Agent
// @Accept json
// @Produce json
// @Param correlation_id path string true "Flow ID"
// @Param action path string true "prepare or submit"
// @Param request body FlowDeliveryRequest true "Registered app version"
// @Success 200 {object} FlowDelivery
// @Failure 409 {object} ErrorResponse
// @Router /api/v1/agent-control/flows/{correlation_id}/appdeploy/{action} [post]
func (h restHandler) RestPostFlowDelivery(c echo.Context) error {
	var request FlowDeliveryRequest
	if message, err := bindAndValidate(c, &request); err != nil {
		return jsonError(c, 400, message, err)
	}
	action := c.Param("action")
	if action != "prepare" && action != "submit" {
		return jsonError(c, 400, "Use prepare or submit", nil)
	}
	result, err := h.service.deliverFlow(c.Request().Context(), c.Param("correlation_id"), request, action == "submit")
	if err != nil {
		return jsonError(c, 409, "AppDeployer request was not confirmed. Check approval, app binding and delivery receipt before retrying.", err)
	}
	return c.JSON(200, result)
}

func deliveryHash(value any) string {
	data, _ := json.Marshal(value)
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func (s Service) deliveryPath(id string) string {
	dir := s.config.AppDeployDeliveryDir
	if dir == "" {
		dir = filepath.Join(s.config.RepoRoot, "runs", "appdeploy-deliveries")
	}
	if !filepath.IsAbs(dir) {
		dir = filepath.Join(s.config.RepoRoot, dir)
	}
	return filepath.Join(dir, deliveryHash(id)+".json")
}

func (s Service) readDelivery(id string) (FlowDelivery, error) {
	var record FlowDelivery
	data, err := os.ReadFile(s.deliveryPath(id))
	if err == nil {
		err = json.Unmarshal(data, &record)
	}
	return record, err
}

func saveDelivery(path string, record FlowDelivery, exclusive bool) error {
	data, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	if exclusive {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, writeErr := file.Write(data)
		syncErr := file.Sync()
		closeErr := file.Close()
		if writeErr != nil {
			return writeErr
		}
		if syncErr != nil {
			return syncErr
		}
		return closeErr
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".delivery-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(file.Name(), path)
}

func (s Service) deliverFlow(ctx context.Context, id string, request FlowDeliveryRequest, submit bool) (FlowDelivery, error) {
	s.flowDelivery.mu.Lock()
	defer s.flowDelivery.mu.Unlock()
	var result FlowDelivery
	if submit && !s.config.AppDeploySubmitEnabled {
		return result, fmt.Errorf("AppDeployer submission is disabled on this server")
	}
	if submit && !request.AcceptProjectionLimits {
		return result, fmt.Errorf("Review the prepared request limitations and explicitly accept the AppSpec execution settings and AppDeployer target selection")
	}
	client, err := appdeploy.NewClient(s.config.AppDeployBaseURL, nil)
	if err != nil {
		return result, fmt.Errorf("Configure AIOPS_APPDEPLOY_BASE_URL first")
	}
	if existing, readErr := s.readDelivery(id); readErr == nil {
		if existing.Request.Manifest.Spec.AppVersionID != request.AppVersionID || existing.DestinationSHA256 != deliveryHash(s.config.AppDeployBaseURL) {
			return result, fmt.Errorf("Flow already has a different delivery binding")
		}
		return existing, nil
	} else if !os.IsNotExist(readErr) {
		return result, fmt.Errorf("Delivery journal is unreadable; submission blocked")
	}
	err = s.agentControl.WithApprovedInitialFlow(id, func(flow agentcontrol.Flow) error {
		apps, err := client.ListApps(ctx)
		if err != nil {
			return fmt.Errorf("AppDeployer App Registry is unavailable")
		}
		profile := flow.ApplicationContext.Data.ApplicationProfile
		var registered *appdeploy.AppRegistrationResponse
		for i := range apps {
			app := &apps[i]
			if app.AppVersionID == request.AppVersionID && (app.AppID == profile.AppID || app.Name == profile.AppID) && app.Version == profile.AppVersion {
				registered = app
				break
			}
		}
		if registered == nil {
			return fmt.Errorf("Registered app name or app_id and version must match the Flow ApplicationProfile")
		}
		canonical := flow.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.DeploymentManifest
		infra := canonical.DesiredInfrastructure
		if infra.NodeCount != 1 || canonical.InferenceConfiguration.Replicas != 1 {
			return fmt.Errorf("This integration supports one initial node and one replica; multi-node deployment requires an AppDeployer contract")
		}
		runtime, accelerator := "cpu", "none"
		if infra.Accelerator.Count > 0 {
			if infra.Accelerator.Type != "GPU" {
				return fmt.Errorf("Unsupported accelerator type")
			}
			runtime, accelerator = "gpu", "nvidia"
		}
		var appSpec struct {
			Runtime struct {
				Type string `json:"type"`
			} `json:"runtime"`
		}
		if json.Unmarshal(registered.AppSpec, &appSpec) != nil || appSpec.Runtime.Type != runtime {
			return fmt.Errorf("Registered application runtime does not match approved resources")
		}
		resources := appdeploy.ResourceRequirements{CPU: strconv.Itoa(infra.CPUCoresPerNode), Memory: strconv.Itoa(infra.MemoryMiBPerNode) + "Mi", GPU: strconv.Itoa(infra.Accelerator.Count), Storage: strconv.Itoa(infra.StorageGiBPerNode) + "Gi"}
		manifest := appdeploy.DeploymentManifest{SchemaVersion: appdeploy.ManifestSchemaVersion, Kind: appdeploy.ManifestKind, Metadata: appdeploy.DeploymentMetadata{Name: canonical.ManifestID}, Spec: appdeploy.DeploymentSpec{AppVersionID: registered.AppVersionID, Accelerator: accelerator, Resources: resources, RequestedBy: "ai-ops-geon-planner", Requirements: &appdeploy.DeploymentRequirements{Runtime: runtime, Resources: resources, Accelerator: accelerator, CostPolicy: "min_cost"}}}
		manifest.Spec.Requirements.SLO = map[string]any{"latency_p95_ms_max": profile.Requirements.SLO.LatencyP95MSMax, "throughput_rps_min": profile.Requirements.SLO.ThroughputRPSMin}
		if profile.Requirements.Cost.CostPerHourMax != 0 {
			return fmt.Errorf("Hourly cost limit cannot yet be enforced by the AppDeployer contract")
		}
		if err := appdeploy.ValidateManifest(manifest, appdeploy.ManifestConstraints{AppVersionID: registered.AppVersionID, RuntimeType: runtime, RequestedBy: "ai-ops-geon-planner", Requirements: manifest.Spec.Requirements}); err != nil {
			return fmt.Errorf("Manifest conversion failed validation")
		}
		result = FlowDelivery{CorrelationID: id, DecisionID: flow.Decision.DecisionID, Revision: 1, Status: "PREPARED", ManifestSHA256: deliveryHash(canonical), DestinationSHA256: deliveryHash(s.config.AppDeployBaseURL), Request: appdeploy.DeploymentCreateRequest{Manifest: manifest}, UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano)}
		result.Limitations = []string{
			"The registered AppSpec supplies executable, model and inference settings; this projection does not apply geon inference_configuration.",
			"AppDeployer selects the target; geon resource_hints and GPU VRAM minimum are not enforced by this contract.",
			"SLO values are forwarded as requirements, not evidence of measured compliance. Revision 2 is not automatically applied.",
		}
		if !submit {
			return nil
		}
		result.Status = "SUBMISSION_UNKNOWN"
		if err := saveDelivery(s.deliveryPath(id), result, true); err != nil {
			return fmt.Errorf("Could not reserve delivery journal; no request sent")
		}
		response, err := client.CreateDeployment(ctx, manifest)
		if err != nil {
			return fmt.Errorf("AppDeployer response was not confirmed; delivery remains SUBMISSION_UNKNOWN and must be reconciled before any retry")
		}
		if response.AppVersionID != registered.AppVersionID {
			return fmt.Errorf("AppDeployer returned a mismatched app version; reconcile delivery before retry")
		}
		result.Status = "SUBMITTED"
		result.Deployment = &response
		if err := saveDelivery(s.deliveryPath(id), result, false); err != nil {
			return fmt.Errorf("Request may have succeeded but its receipt was not persisted; reconcile before retry")
		}
		return nil
	})
	return result, err
}

// RestGetFlowDelivery godoc
// @Summary Refresh the recorded AppDeployer deployment and attach its status to the same Flow
// @Tags AI Application Automation Agent
// @Produce json
// @Param correlation_id path string true "Flow ID"
// @Success 200 {object} FlowDelivery
// @Failure 409 {object} ErrorResponse
// @Router /api/v1/agent-control/flows/{correlation_id}/appdeploy [get]
func (h restHandler) RestGetFlowDelivery(c echo.Context) error {
	result, err := h.service.refreshDelivery(c.Request().Context(), c.Param("correlation_id"))
	if err != nil {
		return jsonError(c, 409, "AppDeployer status could not be attached. Check the delivery receipt and server connection.", err)
	}
	return c.JSON(200, result)
}

func (s Service) refreshDelivery(ctx context.Context, id string) (FlowDelivery, error) {
	s.flowDelivery.mu.Lock()
	defer s.flowDelivery.mu.Unlock()
	record, err := s.readDelivery(id)
	if err != nil {
		return record, fmt.Errorf("No readable delivery receipt exists for this Flow")
	}
	if record.DestinationSHA256 != deliveryHash(s.config.AppDeployBaseURL) {
		return record, fmt.Errorf("Configured AppDeployer differs from the original destination")
	}
	if record.Deployment == nil {
		return record, nil
	}
	client, err := appdeploy.NewClient(s.config.AppDeployBaseURL, nil)
	if err != nil {
		return record, fmt.Errorf("AppDeployer is not configured")
	}
	deployment, err := client.GetDeployment(ctx, record.Deployment.DeploymentID)
	if err != nil {
		return record, fmt.Errorf("AppDeployer status query failed")
	}
	if deployment.DeploymentID != record.Deployment.DeploymentID || deployment.AppVersionID != record.Request.Manifest.Spec.AppVersionID {
		return record, fmt.Errorf("AppDeployer response identity mismatch")
	}
	record.Deployment = &deployment
	record.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	if metrics, err := client.ListDeploymentMetrics(ctx, deployment.DeploymentID); err == nil {
		record.Metrics = &metrics
	}
	flow, ok := s.agentControl.GetFlow(id)
	if ok && flow.Decision != nil && flow.Decision.DecisionID == record.DecisionID && len(flow.ManifestRevisions) > 0 && deliveryHash(flow.ManifestRevisions[0].DeploymentRequest.Data.DeploymentRequest.DeploymentManifest) == record.ManifestSHA256 {
		status := deployment.Status
		switch status {
		case "REQUESTED", "VALIDATING", "VALIDATED":
			status = agentcontrol.DeploymentStateQueued
		case "RESOURCE_CHECKING", "RESOURCE_READY":
			status = agentcontrol.DeploymentStateProvisioning
		case "RUNTIME_FAILED", "VALIDATION_FAILED", "RESOURCE_FAILED":
			status = agentcontrol.DeploymentStateFailed
		}
		switch status {
		case "QUEUED", "PROVISIONING", "DEPLOYING", "RUNNING", "FAILED", "STOPPING", "STOPPED":
			message := agentcontrol.DeploymentStatusEnvelope{Envelope: agentcontrol.Envelope{ContractVersion: "1.0", MessageID: "msg-appdeploy-" + deliveryHash(deployment), MessageType: agentcontrol.MessageDeploymentStatusChanged, OccurredAt: record.UpdatedAt, CorrelationID: id, TraceID: flow.TraceID, CausationID: flow.ManifestRevisions[0].DeploymentRequest.MessageID, Source: agentcontrol.Endpoint{System: "appdeployer", Component: "deployment-orchestrator"}, Target: agentcontrol.Endpoint{System: "khu-agent-control", Component: "automation-agent"}}, Data: agentcontrol.DeploymentStatusData{DeploymentStatus: agentcontrol.DeploymentStatus{DeploymentID: deployment.DeploymentID, DecisionID: record.DecisionID, State: status, Message: "AppDeployer status: " + deployment.Status, UpdatedAt: record.UpdatedAt, ActualInfrastructure: agentcontrol.ActualInfrastructure{ResourceIDs: []string{deployment.TargetProfileID}}}}}
			updated, err := s.agentControl.ReceiveDeploymentStatus(ctx, message)
			if err != nil {
				return record, fmt.Errorf("AppDeployer status could not be attached to Flow")
			}
			record.Flow = &updated
		}
	}
	return record, nil
}

// RestGetFlowIntegration godoc
// @Summary Get AppDeployer integration availability
// @Tags AI Application Automation Agent
// @Produce json
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/agent-control/integration [get]
func (h restHandler) RestGetFlowIntegration(c echo.Context) error {
	return c.JSON(200, map[string]any{"configured": strings.TrimSpace(h.config.AppDeployBaseURL) != "", "submit_enabled": h.config.AppDeploySubmitEnabled, "external_input_path": pathAgentControl + "/external-flows"})
}
