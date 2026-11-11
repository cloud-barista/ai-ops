package api

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/model"
)

const (
	aiAppIntentPrefix  = "/aiapp/partner/nl"
	aiAppIntentSource  = "khu-aiops-application-agent"
	aiAppIntentVersion = "0.1.0"
)

var (
	aiAppDeploymentID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	aiAppIntentRunID  = regexp.MustCompile(`^nl-[0-9]{1,19}-[a-z2-7]{26,64}$`)
)

type AIAppIntentRequest struct {
	Intent  string `json:"intent" validate:"required,max=4000" example:"구축형 LLM으로 배포 가능한 응용을 보여줘" jsonschema:"Natural-language instruction for AI-App. Include the deployment ID for a status query."`
	LLMType string `json:"llmType,omitempty" enums:"subscription,open_weights,self_hosted" jsonschema:"Select subscription, open_weights, or self_hosted. Omit for the configured default."`
}

type AIAppIntentProposal struct {
	Action        string `json:"action"`
	DeploymentID  string `json:"deployment_id,omitempty"`
	ControlAction string `json:"control_action,omitempty"`
}

type AIAppOpsLLMRun struct {
	RunID       string               `json:"run_id"`
	Status      string               `json:"status"`
	LLMType     string               `json:"llm_type"`
	CandidateID string               `json:"candidate_id"`
	Provider    string               `json:"provider"`
	ActualModel string               `json:"actual_model"`
	LatencyMS   int64                `json:"latency_ms"`
	Proposal    *AIAppIntentProposal `json:"proposal,omitempty"`
}

type AIAppIntentStep struct {
	Tool         string            `json:"tool"`
	Grade        string            `json:"grade"`
	Agent        string            `json:"agent,omitempty"`
	Executed     bool              `json:"executed"`
	Input        map[string]string `json:"input"`
	Output       any               `json:"output,omitempty"`
	Error        string            `json:"error,omitempty"`
	EngineRunID  string            `json:"engineRunId,omitempty"`
	EngineStatus string            `json:"engineStatus,omitempty"`
}

type AIAppIntentRun struct {
	RunID     string            `json:"runId"`
	Source    string            `json:"source"`
	Caller    string            `json:"caller,omitempty"`
	Kind      string            `json:"kind"`
	Namespace string            `json:"nsId"`
	StartedAt time.Time         `json:"startedAt"`
	EndedAt   time.Time         `json:"endedAt"`
	Status    string            `json:"status"`
	Answer    string            `json:"answer"`
	Steps     []AIAppIntentStep `json:"steps"`
	DryRun    bool              `json:"dryRun"`
	OpsLLM    *AIAppOpsLLMRun   `json:"opsLlm,omitempty"`
}

type AIAppIntentRunList struct {
	Runs []AIAppIntentRun `json:"runs"`
}

type aiAppIntegrationFailure struct {
	status  int
	code    string
	message string
	cause   error
}

func (failure *aiAppIntegrationFailure) Error() string {
	return failure.code + ": " + failure.message
}

func (failure *aiAppIntegrationFailure) respond(c echo.Context) error {
	return focusedError(c, failure.status, failure.code, failure.message, failure.cause)
}

func registerAIAppIntentRoutes(server *echo.Echo, h restHandler) {
	server.GET(aiAppIntentPrefix+"/readyz", h.RestGetAIAppIntentReady)
	server.GET(aiAppIntentPrefix+"/card", h.requireAIAppIntentAccess(h.RestGetAIAppIntentCard))
	server.GET(aiAppIntentPrefix+"/runs", h.requireAIAppIntentAccess(h.RestGetAIAppIntentRuns))
	server.GET(aiAppIntentPrefix+"/runs/:run_id", h.requireAIAppIntentAccess(h.RestGetAIAppIntentRun))
	server.POST(aiAppIntentPrefix+"/ns/:ns_id/intents", h.requireAIAppIntentAccess(h.RestPostAIAppIntent))
	server.POST(aiAppIntentMCPPath, h.requireAIAppIntentAccess(h.newAIAppIntentMCPHandler()))
}

func (h restHandler) aiAppIntentConfigured() bool {
	return h.config.AIAppToken != "" && h.config.OpsLLMToken != "" &&
		aiAppNodeID.MatchString(h.config.AIAppNamespace) &&
		isHTTPOrigin(h.config.AIAppBaseURL) && isHTTPOrigin(h.config.LLMOpBaseURL)
}

func isHTTPOrigin(value string) bool {
	u, err := url.Parse(value)
	return err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != "" &&
		u.User == nil && !u.ForceQuery && u.RawQuery == "" && u.Fragment == "" && (u.Path == "" || u.Path == "/")
}

func (h restHandler) requireAIAppIntentAccess(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		if !h.aiAppIntentConfigured() {
			return focusedError(c, http.StatusServiceUnavailable, "AIAPP_INTENT_UNAVAILABLE", "AI-App intent integration is not configured.", nil)
		}
		bearer := "Bearer " + h.config.AIAppToken
		if subtle.ConstantTimeCompare([]byte(c.Request().Header.Get(echo.HeaderAuthorization)), []byte(bearer)) == 1 {
			return next(c)
		}
		if h.service.frameworkAccess == nil {
			return focusedError(c, http.StatusUnauthorized, "UNAUTHORIZED", "Valid AI-App authorization is required.", nil)
		}
		principal, failure := h.frameworkPrincipal(c)
		if failure != nil {
			return failure.respond(c)
		}
		if !principal.allows(aiAppEngineName, "AI-App", h.config.AIAppNamespace, "interpret") {
			return deniedFrameworkAction().respond(c)
		}
		r := c.Request()
		c.SetRequest(r.WithContext(context.WithValue(r.Context(), aiAppIntentPrincipalKey{}, principal)))
		return next(c)
	}
}

type aiAppIntentPrincipalKey struct{}

func aiAppIntentPrincipal(ctx context.Context) *frameworkPrincipal {
	principal, _ := ctx.Value(aiAppIntentPrincipalKey{}).(*frameworkPrincipal)
	return principal
}

// RestGetAIAppIntentReady godoc
// @ID GetAIAppIntentReady
// @Summary Check AI-App intent configuration and engine registration
// @Tags AI-App Integration
// @Produce json
// @Success 200 {object} map[string]string
// @Failure 503 {object} map[string]interface{}
// @Router /aiapp/partner/nl/readyz [get]
func (h restHandler) RestGetAIAppIntentReady(c echo.Context) error {
	if !h.aiAppIntentConfigured() {
		return focusedError(c, http.StatusServiceUnavailable, "AIAPP_INTENT_UNAVAILABLE", "AI-App intent integration is not configured.", nil)
	}
	if _, err := h.service.runtimeAgents.getAIAppEngine(); err != nil {
		return aiAppEngineUnavailable(err).respond(c)
	}
	return c.JSON(http.StatusOK, map[string]string{"message": "AI-App intent integration is configured and the engine is registered."})
}

// RestGetAIAppIntentCard godoc
// @ID GetAIAppIntentCard
// @Summary Describe the AI-Ops natural-language agent
// @Tags AI-App Integration
// @Produce json
// @Security AIAppBearer
// @Security FrameworkPrincipalBearer
// @Success 200 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /aiapp/partner/nl/card [get]
func (h restHandler) RestGetAIAppIntentCard(c echo.Context) error {
	return c.JSON(http.StatusOK, map[string]any{
		"name": aiAppIntentSource, "displayName": "경희대 AI 응용 관리 자동화 에이전트",
		"version": aiAppIntentVersion, "owner": "khu", "framework": "AI-Ops", "servesFramework": "AI-App",
		"intentEndpoint": aiAppIntentPrefix + "/ns/{nsId}/intents", "mcpEndpoint": aiAppIntentMCPPath, "mock": false,
		"tools": []map[string]string{
			{"name": "list_applications", "grade": "read"},
			{"name": "application_status", "grade": "read"},
		},
	})
}

// RestPostAIAppIntent godoc
// @ID PostAIAppIntent
// @Summary Interpret an AI-App intent and execute an authorized read tool
// @Description Catalog and status queries use the registered Innogrid engine and its allowed actions. Mutating intents are returned as proposals.
// @Tags AI-App Integration
// @Accept json
// @Produce json
// @Security AIAppBearer
// @Security FrameworkPrincipalBearer
// @Param ns_id path string true "Configured CB-Tumblebug namespace"
// @Param request body AIAppIntentRequest true "Natural-language instruction"
// @Success 200 {object} AIAppIntentRun
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /aiapp/partner/nl/ns/{ns_id}/intents [post]
func (h restHandler) RestPostAIAppIntent(c echo.Context) error {
	if c.Param("ns_id") != h.config.AIAppNamespace {
		return focusedError(c, http.StatusForbidden, "NAMESPACE_FORBIDDEN", "The namespace is outside this agent's scope.", nil)
	}
	var request AIAppIntentRequest
	if err := decodeFocusedRequest(c, &request); err != nil {
		return focusedError(c, http.StatusBadRequest, "INVALID_REQUEST", "Supply a valid intent request.", nil)
	}
	run, failure := h.runAIAppIntent(c.Request().Context(), request, "ai-app-gateway")
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, run)
}

func (h restHandler) runAIAppIntent(ctx context.Context, request AIAppIntentRequest, caller string) (AIAppIntentRun, *aiAppIntegrationFailure) {
	request.Intent = strings.TrimSpace(request.Intent)
	if request.Intent == "" || utf8.RuneCountInString(request.Intent) > 4000 {
		return AIAppIntentRun{}, &aiAppIntegrationFailure{
			status: http.StatusBadRequest, code: "INVALID_INTENT", message: "Supply an intent of up to 4000 characters.",
		}
	}
	switch request.LLMType {
	case "", "subscription", "open_weights", "self_hosted":
	default:
		return AIAppIntentRun{}, &aiAppIntegrationFailure{
			status: http.StatusBadRequest, code: "INVALID_LLM_TYPE", message: "Select a supported LLM type.",
		}
	}
	if _, err := h.service.runtimeAgents.getAIAppEngine(); err != nil {
		return AIAppIntentRun{}, aiAppEngineUnavailable(err)
	}

	started := time.Now().UTC()
	if principal := aiAppIntentPrincipal(ctx); principal != nil {
		caller = principal.Name
	}
	nonce := strings.ToLower(rand.Text())
	run := AIAppIntentRun{
		RunID:     "nl-" + strconv.FormatInt(started.UnixNano(), 10) + "-" + nonce,
		Source:    aiAppIntentSource,
		Caller:    caller,
		Kind:      "intent",
		Namespace: h.config.AIAppNamespace,
		StartedAt: started,
		Steps:     []AIAppIntentStep{},
	}
	ctx, cancel := context.WithTimeout(ctx, h.config.AgentExecutionTimeout)
	defer cancel()
	ops, status := h.interpretOpsLLM(ctx, request, "opsllm-"+nonce)
	if ops.RunID != "" {
		run.OpsLLM = &ops
	}
	switch {
	case status == http.StatusBadRequest:
		return AIAppIntentRun{}, &aiAppIntegrationFailure{
			status: http.StatusBadRequest, code: "INVALID_INTENT", message: "The Ops LLM rejected the intent or its LLM type.",
		}
	case status != http.StatusOK || ops.Status != "completed" || ops.RunID == "" || ops.Proposal == nil:
		run.Status, run.Answer = "failed", "OpsLLM에서 유효한 작업 제안을 받지 못했습니다."
	default:
		h.applyAIAppIntent(ctx, &run, *ops.Proposal)
	}

	run.EndedAt = time.Now().UTC()
	if err := saveJournalRecord(h.aiAppIntentPath(run.RunID), run, false); err != nil {
		return AIAppIntentRun{}, &aiAppIntegrationFailure{
			status: http.StatusInternalServerError, code: "INTENT_RECORD_FAILED", message: "The intent result could not be saved.", cause: err,
		}
	}
	return run, nil
}

func (h restHandler) interpretOpsLLM(ctx context.Context, request AIAppIntentRequest, runID string) (AIAppOpsLLMRun, int) {
	base := strings.TrimRight(h.config.LLMOpBaseURL, "/") + "/api/v1/ops-llm"
	client := partnerHTTPClient(h.config.AgentExecutionTimeout)
	client.Transport = opsLLMRunTransport(runID)
	body, _ := json.Marshal(map[string]string{"intent": request.Intent, "llm_type": request.LLMType})
	var result AIAppOpsLLMRun
	status, err := callPartnerJSON(ctx, client, http.MethodPost, base+"/interpret", h.config.OpsLLMToken, body, &result)
	if err == nil {
		return result, status
	}
	if status == 0 || status == http.StatusOK || status == http.StatusAccepted || status >= 500 {
		if ctx.Err() == nil {
			var stored AIAppOpsLLMRun
			readStatus, readErr := callPartnerJSON(ctx, client, http.MethodGet, base+"/runs/"+runID, h.config.OpsLLMToken, nil, &stored)
			if readErr == nil && readStatus == http.StatusOK && stored.RunID == runID {
				return stored, readStatus
			}
		}
		return AIAppOpsLLMRun{RunID: runID, Status: "completion_unconfirmed"}, status
	}
	return AIAppOpsLLMRun{}, status
}

type opsLLMRunTransport string

func (t opsLLMRunTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	req = req.Clone(req.Context())
	if req.Method == http.MethodPost {
		req.Header.Set("X-AIMCMP-OpsLLM-Run-ID", string(t))
	}
	return http.DefaultTransport.RoundTrip(req)
}

func (h restHandler) applyAIAppIntent(ctx context.Context, run *AIAppIntentRun, proposal AIAppIntentProposal) {
	step := AIAppIntentStep{Tool: proposal.Action, Grade: "write", Input: map[string]string{}}
	if proposal.DeploymentID != "" && !aiAppDeploymentID.MatchString(proposal.DeploymentID) {
		run.Status, run.Answer = "blocked", "배포 ID를 확인해 주세요."
		return
	}
	if proposal.DeploymentID != "" {
		step.Input["deploymentId"] = proposal.DeploymentID
	}
	switch proposal.Action {
	case "clarify":
		run.Status, run.Answer = "blocked", "대상과 수행할 작업을 확인해 주세요. 기존 배포에는 배포 ID가 필요합니다."
		return
	case "list_applications", "application_status":
		step.Grade = "read"
		h.readAIAppIntent(ctx, run, &step, proposal.DeploymentID)
	case "update_application":
		step.Tool = "deploy_application"
	case "control_application":
		switch proposal.ControlAction {
		case "suspend", "resume", "reboot", "terminate":
			step.Input["action"] = proposal.ControlAction
		default:
			run.Status, run.Answer = "blocked", "VM 제어 동작을 확인해 주세요."
			return
		}
	case "deploy_application", "heal_application", "remove_application", "optimize_inference":
	default:
		run.Status, run.Answer = "failed", "지원하는 작업으로 해석되지 않았습니다."
		return
	}
	if step.Grade == "write" {
		run.Status, run.Answer = "blocked", "작업 제안을 기록했습니다. 변경에는 대상과 실행 계획의 승인이 필요합니다."
	}
	run.Steps = append(run.Steps, step)
}

type aiAppIntentDeployment struct {
	ID     string `json:"id"`
	Type   string `json:"type"`
	Status string `json:"status"`
	Nodes  []struct {
		Target model.AIAppNodeTarget `json:"target"`
	} `json:"nodes"`
}

func (d aiAppIntentDeployment) inNamespace(id, namespace string) bool {
	if d.ID != id || d.Status == "" || len(d.Nodes) == 0 {
		return false
	}
	for _, node := range d.Nodes {
		if node.Target.NsID != namespace {
			return false
		}
	}
	return true
}

func (h restHandler) readAIAppIntent(ctx context.Context, run *AIAppIntentRun, step *AIAppIntentStep, deploymentID string) {
	step.Agent = aiAppEngineName
	if principal := aiAppIntentPrincipal(ctx); principal != nil &&
		!principal.allows(aiAppEngineName, "AI-App", run.Namespace, step.Tool) {
		step.Error = "This principal cannot use the read tool."
		run.Status, run.Answer = "blocked", "이 조회 작업에 필요한 권한이 없습니다."
		return
	}
	registration, err := h.service.runtimeAgents.getAIAppEngine()
	if err != nil || registration.Namespace != run.Namespace || !contains(registration.AllowedActions, step.Tool) {
		step.Error = "The registered engine does not authorize this read."
		run.Status, run.Answer = "blocked", "등록된 엔진에서 이 조회 작업이 허용되지 않았습니다."
		return
	}
	if step.Tool == "application_status" {
		var progress aiAppIntentDeployment
		endpoint := registration.Endpoint + "/aiapp/deployments/" + deploymentID + "/progress"
		if deploymentID == "" {
			run.Status, run.Answer = "blocked", "배포 ID를 알려주세요."
			return
		}
		status, err := callPartnerJSON(ctx, partnerHTTPClient(h.config.AgentExecutionTimeout), http.MethodGet,
			endpoint, h.config.AIAppToken, nil, &progress)
		if status == http.StatusNotFound || err == nil && status == http.StatusOK && !progress.inNamespace(deploymentID, run.Namespace) {
			run.Status, run.Answer = "blocked", "이 네임스페이스에서 확인할 수 있는 배포 ID가 필요합니다."
			return
		}
		if err != nil || status != http.StatusOK {
			step.Error = "AI-App deployment progress could not be read."
			run.Status, run.Answer = "failed", "AI-App에서 배포 정보를 읽지 못했습니다."
			return
		}
	}

	output, dryRun, err := h.callAIAppReadTool(ctx, registration, step.Tool, step.Input)
	run.DryRun = dryRun
	if err == nil && step.Tool == "list_applications" {
		var catalog struct {
			Models []struct {
				ID   string `json:"id"`
				Name string `json:"name"`
			} `json:"models"`
			Apps []struct {
				Name     string `json:"name"`
				Versions []int  `json:"versions"`
			} `json:"apps"`
		}
		if json.Unmarshal(output, &catalog) == nil && catalog.Models != nil && catalog.Apps != nil {
			step.Executed, step.Output = true, catalog
			run.Status = "succeeded"
			run.Answer = fmt.Sprintf("카탈로그에서 모델 %d개와 등록된 응용 %d개를 확인했습니다.", len(catalog.Models), len(catalog.Apps))
			return
		}
	}
	if err == nil && step.Tool == "application_status" {
		var engine struct {
			RunID     string `json:"runId"`
			Namespace string `json:"nsId"`
			Status    string `json:"status"`
			DryRun    bool   `json:"dryRun"`
			Steps     []struct {
				Tool   string          `json:"tool"`
				Output json.RawMessage `json:"output"`
			} `json:"steps"`
		}
		if json.Unmarshal(output, &engine) == nil && engine.RunID != "" && engine.Namespace == run.Namespace {
			step.EngineRunID, step.EngineStatus = engine.RunID, engine.Status
			run.DryRun = engine.DryRun
			for _, result := range engine.Steps {
				var deployment aiAppIntentDeployment
				if result.Tool == "get_deployment" && engine.Status == "succeeded" &&
					json.Unmarshal(result.Output, &deployment) == nil && deployment.inNamespace(deploymentID, run.Namespace) {
					step.Executed = true
					step.Output = map[string]string{"deploymentId": deploymentID, "status": deployment.Status}
					run.Status, run.Answer = "succeeded", fmt.Sprintf("배포 %s의 상태는 %s입니다.", deploymentID, deployment.Status)
					return
				}
			}
		}
	}
	step.Error = "AI-App engine read could not be confirmed."
	run.Status, run.Answer = "failed", "AI-App 엔진의 조회 결과를 확인하지 못했습니다."
}

func (h restHandler) aiAppIntentPath(runID string) string {
	return h.config.path("runs", "aiapp-intents", runID+".json")
}

// RestGetAIAppIntentRuns godoc
// @ID ListAIAppIntentRuns
// @Summary List the latest AI-App intent run records
// @Tags AI-App Integration
// @Produce json
// @Security AIAppBearer
// @Security FrameworkPrincipalBearer
// @Success 200 {object} AIAppIntentRunList
// @Failure 401 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /aiapp/partner/nl/runs [get]
func (h restHandler) RestGetAIAppIntentRuns(c echo.Context) error {
	runs := AIAppIntentRunList{Runs: []AIAppIntentRun{}}
	dir := h.config.path("runs", "aiapp-intents")
	entries, err := os.ReadDir(dir)
	if err != nil && !os.IsNotExist(err) {
		return focusedError(c, http.StatusInternalServerError, "INTENT_STORE_UNAVAILABLE", "The intent run store could not be read.", err)
	}
	for i := len(entries) - 1; i >= 0 && len(runs.Runs) < 50; i-- {
		name := entries[i].Name()
		if !entries[i].Type().IsRegular() || !strings.HasSuffix(name, ".json") ||
			!aiAppIntentRunID.MatchString(strings.TrimSuffix(name, ".json")) {
			continue
		}
		run, err := loadJSON[AIAppIntentRun](filepath.Join(dir, name))
		if err != nil {
			return focusedError(c, http.StatusInternalServerError, "INTENT_STORE_UNAVAILABLE", "An intent run record could not be read.", err)
		}
		principal := aiAppIntentPrincipal(c.Request().Context())
		if run.Namespace == h.config.AIAppNamespace && (principal == nil || run.Caller == principal.Name) {
			runs.Runs = append(runs.Runs, run)
		}
	}
	return c.JSON(http.StatusOK, runs)
}

// RestGetAIAppIntentRun godoc
// @ID GetAIAppIntentRun
// @Summary Read one AI-App intent run record
// @Tags AI-App Integration
// @Produce json
// @Security AIAppBearer
// @Security FrameworkPrincipalBearer
// @Param run_id path string true "Intent run ID"
// @Success 200 {object} AIAppIntentRun
// @Failure 401 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /aiapp/partner/nl/runs/{run_id} [get]
func (h restHandler) RestGetAIAppIntentRun(c echo.Context) error {
	run, failure := h.readAIAppIntentRun(c.Request().Context(), c.Param("run_id"))
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, run)
}

func (h restHandler) readAIAppIntentRun(ctx context.Context, id string) (AIAppIntentRun, *aiAppIntegrationFailure) {
	run, failure := h.getAIAppIntentRun(id)
	if failure != nil {
		return AIAppIntentRun{}, failure
	}
	if principal := aiAppIntentPrincipal(ctx); principal != nil && run.Caller != principal.Name {
		return AIAppIntentRun{}, &aiAppIntegrationFailure{status: http.StatusNotFound, code: "INTENT_NOT_FOUND", message: "The intent run was not found."}
	}
	return run, nil
}

func (h restHandler) getAIAppIntentRun(id string) (AIAppIntentRun, *aiAppIntegrationFailure) {
	notFound := aiAppIntegrationFailure{
		status: http.StatusNotFound, code: "INTENT_NOT_FOUND", message: "The intent run was not found.",
	}
	if !aiAppIntentRunID.MatchString(id) {
		return AIAppIntentRun{}, &notFound
	}
	run, err := loadJSON[AIAppIntentRun](h.aiAppIntentPath(id))
	if os.IsNotExist(err) || err == nil && (run.RunID != id || run.Namespace != h.config.AIAppNamespace) {
		return AIAppIntentRun{}, &notFound
	}
	if err != nil {
		return AIAppIntentRun{}, &aiAppIntegrationFailure{
			status: http.StatusInternalServerError, code: "INTENT_STORE_UNAVAILABLE", message: "The intent run record could not be read.", cause: err,
		}
	}
	return run, nil
}
