package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"

	"kyunghee-aiops/service-control-api/internal/model"
)

const (
	aiAppNativeMCPPath = "/aiapp/mcp"
	aiAppNativeServer  = "ai-mcmp-ai-app"
	aiAppRemoveTool    = "delete_deployment"
)

func aiAppEngineDigest(engine *registeredAIAppEngine) string {
	return frameworkDigest([]string{
		engine.Endpoint, engine.Namespace, engine.Card.Name, engine.Card.Version, engine.Card.Owner,
		engine.Card.ServesFramework, engine.Card.MCPEndpoint,
	})
}

func (engine registeredAIAppEngine) advertisesWrite(workflow, tool string) bool {
	if !contains(engine.Card.Workflows, workflow) {
		return false
	}
	for _, item := range engine.Card.Tools.Exposed {
		if item.Name == tool && item.Grade == "write" && item.GuardedByDryRun {
			return true
		}
	}
	return false
}

func (h restHandler) bindAIAppIntentChange(ctx context.Context, engine *registeredAIAppEngine, plan *AIAppIntentChangePlan) *aiAppIntegrationFailure {
	req := plan.Request
	if engine.Namespace != plan.Namespace {
		return deniedFrameworkAction()
	}
	switch plan.Action {
	case "deploy_application", "update_application":
		return h.bindAIAppIntentRevision(ctx, engine, plan)
	case "heal_application":
		if req.Apply != nil {
			return h.bindAIAppIntentRevision(ctx, engine, plan)
		}
		if !engine.advertisesWrite("heal", plan.Action) {
			return deniedFrameworkAction()
		}
		plan.Tool = plan.Action
	case "control_application":
		if !engine.advertisesWrite("control", plan.Action) ||
			!contains([]string{"suspend", "resume", "reboot", "terminate"}, plan.ControlAction) {
			return deniedFrameworkAction()
		}
		plan.Tool = plan.Action
	case "remove_application":
		if req.DeploymentType != "app" && req.DeploymentType != "inference" {
			return intentChangeInvalid("Confirm whether this removes an app or an inference deployment.")
		}
		plan.Tool = aiAppRemoveTool
	default:
		return intentChangeConflict("This intent is not an approved deployment, update, recovery, control or removal.")
	}
	if req.Apply != nil || req.CorrelationID != "" || (req.DeploymentType != "" && plan.Action != "remove_application") ||
		!aiAppDeploymentID.MatchString(plan.DeploymentID) {
		return intentChangeInvalid("Use the deployment ID from the intent and the allowed arguments for this action.")
	}
	d, failure := h.readIntentDeployment(ctx, engine, plan.DeploymentID)
	if failure != nil {
		return failure
	}
	if plan.Action == "remove_application" && d.Type != req.DeploymentType {
		return intentChangeConflict("The reviewed removal type differs from the actual deployment type.")
	}
	switch plan.Action {
	case "control_application":
		if d.Status != "running" && d.Status != "dry-run" {
			return intentChangeConflict("VM control requires a settled running or dry-run deployment.")
		}
		if d.Status == "running" {
			if failure := h.requireActiveIntentDeployments(ctx, engine, d.Nodes[0].Target, d.ID); failure != nil {
				return failure
			}
		}
	case "heal_application":
		if !contains([]string{"failed", "partial", "interrupted"}, d.Status) {
			return intentChangeConflict("Recovery requires a failed or interrupted deployment.")
		}
	case "remove_application":
		if contains([]string{"pending", "deploying", "removing", "removed"}, d.Status) {
			return intentChangeConflict("The deployment has not settled or has already been removed.")
		}
	}
	plan.Target, plan.DeploymentStatus = d.Nodes[0].Target, d.Status
	return nil
}

func (h restHandler) bindAIAppIntentRevision(ctx context.Context, engine *registeredAIAppEngine, plan *AIAppIntentChangePlan) *aiAppIntegrationFailure {
	req, id := plan.Request.Apply, plan.Request.CorrelationID
	if req == nil || !aiAppNodeID.MatchString(id) || plan.Request.DeploymentType != "" ||
		!validApplyRequest(*req, engine.Namespace) {
		return intentChangeInvalid("Supply the exact single-node inference and application revision.")
	}
	if !h.config.aiAppApprovalCredentialsConfigured() {
		return &aiAppIntegrationFailure{status: http.StatusServiceUnavailable, code: "AIAPP_APPLY_UNAVAILABLE", message: "The AI-App approval credential is not configured."}
	}
	if !engine.advertisesWrite("deploy", "deploy_application") {
		return deniedFrameworkAction()
	}
	plan.Tool, plan.Target = aiAppApplyTool, req.Inference.Targets[0]
	if plan.Action == "deploy_application" {
		if req.Revision != 1 || plan.DeploymentID != "" || h.checkInitialApply(id, *req) != nil {
			return intentChangeConflict("New deployments require the exact guarded and staged Revision 1.")
		}
		return nil
	}
	if req.PreviousRevision < 1 || !aiAppDeploymentID.MatchString(plan.DeploymentID) {
		return intentChangeInvalid("The intent must identify the deployment in the previous approved revision.")
	}
	before, err := loadJSON[aiAppApplyApproval](h.applyApprovalPath(id, req.PreviousRevision))
	if err != nil || before.Request.Revision != req.PreviousRevision || !validApplyRequest(before.Request, engine.Namespace) {
		return intentChangeConflict("The previous approved revision could not be found.")
	}
	expected := before.Request
	expected.Revision, expected.PreviousRevision = req.Revision, req.PreviousRevision
	if plan.Action == "update_application" {
		version := *expected.Application.App
		version.Version = req.Application.App.Version
		expected.Application.App = &version
		if version.Version == before.Request.Application.App.Version {
			return intentChangeConflict("An application update requires a different catalog version.")
		}
	}
	if !reflect.DeepEqual(expected, *req) {
		return intentChangeConflict("The request changes settings outside the intended update or retry.")
	}
	receipt, err := h.readAppliedRevision(ctx, engine, id, before.Request)
	if err != nil {
		return intentChangePartnerFailure(err)
	}
	if plan.Action == "heal_application" {
		if receipt.Status != "failed" || (receipt.Inference.DeploymentID != plan.DeploymentID &&
			receipt.Application.DeploymentID != plan.DeploymentID) {
			return intentChangeConflict("The intent must name a deployment from the failed revision.")
		}
		return nil
	}
	if receipt.Status != "succeeded" || receipt.Application.DeploymentID != plan.DeploymentID {
		return intentChangeConflict("The intent must name the application in the previous successful revision.")
	}
	return h.requireActiveIntentDeployments(ctx, engine, plan.Target, receipt.Inference.DeploymentID, receipt.Application.DeploymentID)
}

func (h restHandler) readIntentDeployment(ctx context.Context, engine *registeredAIAppEngine, id string) (aiAppIntentDeployment, *aiAppIntegrationFailure) {
	var d aiAppIntentDeployment
	status, err := callPartnerJSON(ctx, partnerHTTPClient(h.config.AgentExecutionTimeout), http.MethodGet,
		engine.Endpoint+"/aiapp/deployments/"+id+"/progress", h.config.AIAppToken, nil, &d)
	if status == http.StatusNotFound {
		return d, intentChangeConflict("The reviewed deployment could not be found.")
	}
	if err != nil || status != http.StatusOK {
		return d, intentChangePartnerFailure(err)
	}
	if !d.inNamespace(id, engine.Namespace) || len(d.Nodes) != 1 ||
		(d.Type != "app" && d.Type != "inference") || d.Nodes[0].Target.Kind != "node" ||
		!aiAppNodeID.MatchString(d.Nodes[0].Target.InfraID) || !aiAppNodeID.MatchString(d.Nodes[0].Target.NodeID) {
		return d, deniedFrameworkAction()
	}
	return d, nil
}

func (h restHandler) requireActiveIntentDeployments(ctx context.Context, engine *registeredAIAppEngine, t model.AIAppNodeTarget, ids ...string) *aiAppIntegrationFailure {
	query := url.Values{"nsId": {t.NsID}, "infraId": {t.InfraID}, "nodeId": {t.NodeID}, "limit": {"1000"}}
	var response struct {
		Endpoints []struct {
			NsID         string `json:"nsId"`
			InfraID      string `json:"infraId"`
			NodeID       string `json:"nodeId"`
			DeploymentID string `json:"deploymentId"`
		} `json:"endpoints"`
	}
	status, err := callPartnerJSON(ctx, partnerHTTPClient(h.config.AgentExecutionTimeout), http.MethodGet,
		engine.Endpoint+"/aiapp/endpoints?"+query.Encode(), h.config.AIAppToken, nil, &response)
	if err != nil || status != http.StatusOK {
		return intentChangePartnerFailure(err)
	}
	current := map[string]bool{}
	for _, endpoint := range response.Endpoints {
		if endpoint.NsID == t.NsID && endpoint.InfraID == t.InfraID && endpoint.NodeID == t.NodeID {
			current[endpoint.DeploymentID] = true
		}
	}
	for _, id := range ids {
		if !current[id] {
			return intentChangeConflict("The reviewed deployment was replaced or is no longer active on the node.")
		}
	}
	return nil
}

func (h restHandler) dispatchAIAppIntentChange(ctx context.Context, engine *registeredAIAppEngine, plan AIAppIntentChangePlan, approval *AIAppIntentChangeApproval) *aiAppIntegrationFailure {
	if plan.Request.Apply != nil {
		receipt, failure := h.queueAIAppApply(ctx, engine, plan.Request.CorrelationID, *plan.Request.Apply)
		if failure != nil {
			return failure
		}
		approval.Apply, approval.Status = receipt, receipt.Status
		return nil
	}
	path, name, grade := engine.Card.MCPEndpoint, engine.Card.Name, "write"
	input := map[string]string{"deploymentId": plan.DeploymentID}
	if plan.Tool == aiAppRemoveTool {
		path, name, grade = aiAppNativeMCPPath, aiAppNativeServer, "destructive"
		input = map[string]string{"id": plan.DeploymentID}
	} else if plan.ControlAction != "" {
		input["action"] = plan.ControlAction
	}
	raw, dryRun, err := h.callIntentWriteTool(ctx, engine, path, name, grade, plan.Tool, input)
	if err != nil {
		return intentChangePartnerFailure(err)
	}
	if plan.Tool == aiAppRemoveTool {
		var d aiAppIntentDeployment
		if json.Unmarshal(raw, &d) == nil && d.inNamespace(plan.DeploymentID, plan.Namespace) &&
			d.Type == plan.Request.DeploymentType && len(d.Nodes) == 1 && d.Nodes[0].Target == plan.Target {
			status := aiAppRemovalStatus(d.Status)
			if dryRun {
				status = "blocked"
			}
			if status != "" {
				approval.Status, approval.DeploymentStatus = status, d.Status
				return nil
			}
		}
	} else if readIntentEngineRun(raw, plan, approval) {
		return nil
	}
	return intentChangePartnerFailure(errors.New("unconfirmed AI-App change result"))
}

func (h restHandler) callIntentWriteTool(ctx context.Context, engine *registeredAIAppEngine, path, serverName, grade, tool string, args map[string]string) (json.RawMessage, bool, error) {
	ctx = aiAppEngineContext{ctx}
	session, err := h.connectAIAppMCP(ctx, engine, path, h.config.AIAppToken)
	if err != nil {
		return nil, false, err
	}
	defer session.Close()
	initialized := session.InitializeResult()
	if initialized.ServerInfo == nil || initialized.ServerInfo.Name != serverName {
		return nil, false, errors.New("AI-App MCP identity does not match")
	}
	dryRun, ok := initialized.Meta["ai-mcmp/dryRun"].(bool)
	if !ok {
		return nil, false, errors.New("AI-App did not advertise its dry-run state")
	}
	for def, err := range session.Tools(ctx, nil) {
		if err != nil {
			return nil, dryRun, err
		}
		if def.Name == tool && def.Meta["ai-mcmp/grade"] == grade && def.Meta["ai-mcmp/guardedByDryRun"] == true &&
			def.Annotations != nil && !def.Annotations.ReadOnlyHint &&
			(grade != "destructive" || (def.Annotations.DestructiveHint != nil && *def.Annotations.DestructiveHint)) {
			raw, err := aiAppMCPTextResult(ctx, session, tool, args)
			return raw, dryRun, err
		}
	}
	return nil, dryRun, errors.New("AI-App did not advertise the required write tool")
}

func readIntentEngineRun(raw json.RawMessage, plan AIAppIntentChangePlan, approval *AIAppIntentChangeApproval) bool {
	var run struct {
		RunID     string            `json:"runId"`
		Namespace string            `json:"nsId"`
		Workflow  string            `json:"workflow"`
		Status    string            `json:"status"`
		Input     map[string]string `json:"input"`
		Steps     []struct {
			Tool   string          `json:"tool"`
			Output json.RawMessage `json:"output"`
		} `json:"steps"`
	}
	if json.Unmarshal(raw, &run) != nil || run.RunID == "" || run.Namespace != plan.Namespace ||
		run.Input["deploymentId"] != plan.DeploymentID || run.Input["action"] != plan.ControlAction ||
		!contains([]string{"succeeded", "blocked", "failed"}, run.Status) {
		return false
	}
	deploymentID, deploymentStatus := plan.DeploymentID, ""
	switch plan.Tool {
	case "control_application":
		if run.Workflow != "control" {
			return false
		}
		var outcome struct {
			ID     string `json:"id"`
			Action string `json:"action"`
			Nodes  []struct {
				Target model.AIAppNodeTarget `json:"target"`
				Status string                `json:"status"`
			} `json:"nodes"`
		}
		if len(run.Steps) != 1 || run.Steps[0].Tool != "control_node" ||
			json.Unmarshal(run.Steps[0].Output, &outcome) != nil || outcome.ID != plan.DeploymentID ||
			outcome.Action != plan.ControlAction || len(outcome.Nodes) != 1 || outcome.Nodes[0].Target != plan.Target {
			return false
		}
		expected := map[string]string{"succeeded": "done", "blocked": "blocked", "failed": "failed"}
		if outcome.Nodes[0].Status != expected[run.Status] {
			return false
		}
	case "heal_application":
		if run.Workflow != "heal" {
			return false
		}
		for _, step := range run.Steps {
			var d aiAppIntentDeployment
			if (step.Tool == "create_deployment" || step.Tool == "get_deployment") &&
				json.Unmarshal(step.Output, &d) == nil && d.ID != plan.DeploymentID && aiAppDeploymentID.MatchString(d.ID) &&
				d.inNamespace(d.ID, plan.Namespace) && len(d.Nodes) == 1 && d.Nodes[0].Target == plan.Target {
				deploymentID, deploymentStatus = d.ID, d.Status
			}
		}
		if run.Status == "succeeded" && (deploymentID == plan.DeploymentID || deploymentStatus != "running") {
			return false
		}
		if deploymentStatus == "pending" || deploymentStatus == "deploying" {
			run.Status = "queued"
		}
	default:
		return false
	}
	approval.EngineRunID, approval.Status = run.RunID, run.Status
	approval.DeploymentID, approval.DeploymentStatus = deploymentID, deploymentStatus
	return true
}

func (h restHandler) refreshAIAppIntentApproval(ctx context.Context, change *AIAppIntentChange) {
	plan, approval := change.Plan, change.Approval
	engine, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil || plan.EngineDigest != aiAppEngineDigest(engine) {
		return
	}
	if req := plan.Request.Apply; req != nil {
		if receipt, err := h.readSavedAppliedRevision(ctx, engine, plan.Request.CorrelationID, *req); err == nil {
			approval.Apply, approval.Status = receipt, receipt.Status
		}
	} else if plan.Tool == aiAppRemoveTool {
		if d, failure := h.readIntentDeployment(ctx, engine, plan.DeploymentID); failure == nil &&
			d.Type == plan.Request.DeploymentType && d.Nodes[0].Target == plan.Target {
			approval.DeploymentStatus = d.Status
			if approval.Status == "queued" && aiAppRemovalStatus(d.Status) != "" {
				approval.Status = aiAppRemovalStatus(d.Status)
			}
		}
	} else if plan.Tool == "heal_application" && approval.Status == "queued" && approval.DeploymentID != plan.DeploymentID {
		if d, failure := h.readIntentDeployment(ctx, engine, approval.DeploymentID); failure == nil && d.Nodes[0].Target == plan.Target {
			approval.DeploymentStatus = d.Status
			switch d.Status {
			case "running":
				approval.Status = "succeeded"
			case "failed", "partial", "interrupted":
				approval.Status = "failed"
			}
		}
	}
}

func aiAppRemovalStatus(status string) string {
	switch status {
	case "removed":
		return "succeeded"
	case "remove-failed":
		return "failed"
	case "removing":
		return "queued"
	default:
		return ""
	}
}

func intentChangePartnerFailure(err error) *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusBadGateway, code: "AIAPP_CHANGE_UNCONFIRMED",
		message: "The registered AI-App did not confirm the requested information or change.", cause: err}
}
