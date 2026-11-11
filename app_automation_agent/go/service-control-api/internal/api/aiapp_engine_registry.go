package api

import (
	"crypto/subtle"
	"errors"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/labstack/echo/v4"
)

const (
	aiAppEngineRegistryPath = "/api/v1/integrations/ai-app/agents"
	aiAppEngineName         = "ai-mcmp-app-automation-engine"
	aiAppEngineWorkflowPath = "/aiapp/engine/workflows/{workflow}?ns={nsId}"
	aiAppEngineSource       = "ai_app_engine"
	aiAppRegistryVersion    = 1
)

var (
	aiAppEngineVersionPattern   = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9.+_-]{0,63}$`)
	errAIAppEngineNotRegistered = errors.New("AI-App engine card is not registered")
	errAIAppRegistryInvalid     = errors.New("AI-App engine registry record is invalid or unreadable")
)

type AIAppEngineCard struct {
	Name             string               `json:"name" example:"ai-mcmp-app-automation-engine"`
	DisplayName      string               `json:"displayName"`
	Version          string               `json:"version" example:"0.1.0"`
	Owner            string               `json:"owner" example:"innogrid"`
	Framework        string               `json:"framework" example:"AI-Ops"`
	ServesFramework  string               `json:"servesFramework" example:"AI-App"`
	Description      string               `json:"description"`
	UsesLLM          bool                 `json:"usesLLM"`
	Workflows        []string             `json:"workflows" example:"deploy,heal,control,status"`
	WorkflowEndpoint string               `json:"workflowEndpoint"`
	MCPEndpoint      string               `json:"mcpEndpoint" example:"/aiapp/engine/mcp"`
	MCP              AIAppEngineCardMCP   `json:"mcp"`
	Tools            AIAppEngineCardTools `json:"tools"`
	DryRun           bool                 `json:"dryRun"`
}

type AIAppEngineCardMCP struct {
	AppServer struct {
		URL       string `json:"url"`
		Connected bool   `json:"connected"`
		ToolCount int    `json:"toolCount"`
	} `json:"appServer"`
}

type AIAppEngineCardTools struct {
	Exposed []AIAppEngineCardTool `json:"exposed"`
	Uses    []AIAppEngineCardTool `json:"uses"`
}

type AIAppEngineCardTool struct {
	Name            string `json:"name"`
	Grade           string `json:"grade"`
	GuardedByDryRun bool   `json:"guardedByDryRun"`
}

type registeredAIAppEngine struct {
	SchemaVersion  int             `json:"schema_version"`
	Endpoint       string          `json:"endpoint"`
	Namespace      string          `json:"namespace"`
	AllowedActions []string        `json:"allowed_actions"`
	RegisteredAt   time.Time       `json:"registered_at"`
	Card           AIAppEngineCard `json:"card"`
}

func (config ServerConfig) aiAppEngineRegistryConfigured() bool {
	token := config.AIAppEngineRegistryToken
	return token != "" && config.AIAppToken != "" && token != config.AIAppToken &&
		token != config.OpsLLMToken && token != strings.TrimSpace(config.AutonomyAdminToken) &&
		isHTTPOrigin(config.AIAppBaseURL) && aiAppNodeID.MatchString(config.AIAppNamespace)
}

func (config ServerConfig) aiAppEngineRegistryFile() string {
	return config.path("runs", "aiapp-engine-registry.json")
}

func (card AIAppEngineCard) valid() bool {
	appServer, err := url.Parse(card.MCP.AppServer.URL)
	if err != nil || (appServer.Scheme != "http" && appServer.Scheme != "https") || appServer.Hostname() == "" ||
		appServer.User != nil || appServer.ForceQuery || appServer.RawQuery != "" || appServer.Fragment != "" ||
		card.MCP.AppServer.ToolCount < 0 {
		return false
	}
	for _, workflow := range card.Workflows {
		if !agentNamePattern.MatchString(workflow) {
			return false
		}
	}
	return card.Name == aiAppEngineName && card.Owner == "innogrid" &&
		card.Framework == "AI-Ops" && card.ServesFramework == "AI-App" &&
		aiAppEngineVersionPattern.MatchString(card.Version) && !card.UsesLLM &&
		strings.TrimSpace(card.DisplayName) != "" && len(card.DisplayName) <= 256 && len(card.Description) <= 2048 &&
		card.MCPEndpoint == aiAppEngineMCPPath && card.WorkflowEndpoint == aiAppEngineWorkflowPath &&
		validAIAppEngineTools(card.Tools.Exposed, false) && validAIAppEngineTools(card.Tools.Uses, true)
}

func validAIAppEngineTools(tools []AIAppEngineCardTool, allowUngraded bool) bool {
	seen := make(map[string]bool, len(tools))
	for _, tool := range tools {
		if !agentNamePattern.MatchString(tool.Name) || seen[tool.Name] {
			return false
		}
		seen[tool.Name] = true
		switch tool.Grade {
		case "read", "review", "write", "destructive":
		case "":
			if !allowUngraded {
				return false
			}
		default:
			return false
		}
	}
	return true
}

func (card AIAppEngineCard) readActions() []string {
	actions := []string{}
	for _, tool := range card.Tools.Exposed {
		if tool.Grade != "read" || tool.GuardedByDryRun {
			continue
		}
		if tool.Name == "list_applications" || tool.Name == "application_status" && contains(card.Workflows, "status") {
			actions = append(actions, tool.Name)
		}
	}
	slices.Sort(actions)
	return actions
}

func (engine registeredAIAppEngine) profile() AgentProfile {
	return AgentProfile{
		Name: engine.Card.Name, KoreanName: engine.Card.DisplayName, Version: engine.Card.Version,
		Owner: engine.Card.Owner, Framework: engine.Card.Framework, ServesFramework: engine.Card.ServesFramework,
		Namespace: engine.Namespace, Role: "AI-App automation engine", Capabilities: []string{"ai_application_deployment_control"},
		BoundedActions: slices.Clone(engine.AllowedActions), Enabled: true, Endpoint: engine.Endpoint,
		InvocationPath: engine.Card.MCPEndpoint, AuthTokenEnv: "AIOPS_AIAPP_TOKEN", Source: aiAppEngineSource,
		RegisteredAt: engine.RegisteredAt.Format(time.RFC3339Nano),
	}
}

func (store *runtimeAgentStore) restoreAIAppEngine(config ServerConfig) {
	if !config.aiAppEngineRegistryConfigured() {
		return
	}
	engine, err := loadJSON[registeredAIAppEngine](config.aiAppEngineRegistryFile())
	if errors.Is(err, os.ErrNotExist) {
		return
	}
	if err != nil || engine.SchemaVersion != aiAppRegistryVersion || !engine.Card.valid() ||
		!isHTTPOrigin(engine.Endpoint) || !aiAppNodeID.MatchString(engine.Namespace) || engine.RegisteredAt.IsZero() ||
		!slices.Equal(engine.AllowedActions, engine.Card.readActions()) {
		store.aiAppLoadErr = errAIAppRegistryInvalid
		return
	}
	if engine.Endpoint == strings.TrimRight(config.AIAppBaseURL, "/") && engine.Namespace == config.AIAppNamespace {
		store.aiAppEngine = &engine
	}
}

func (store *runtimeAgentStore) getAIAppEngine() (*registeredAIAppEngine, error) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	if store.aiAppLoadErr != nil {
		return nil, store.aiAppLoadErr
	}
	if store.aiAppEngine == nil {
		return nil, errAIAppEngineNotRegistered
	}
	return store.aiAppEngine, nil
}

func (store *runtimeAgentStore) saveAIAppEngine(config ServerConfig, card AIAppEngineCard) (AgentProfile, bool, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.aiAppLoadErr != nil {
		return AgentProfile{}, false, store.aiAppLoadErr
	}
	if current := store.aiAppEngine; current != nil && reflect.DeepEqual(current.Card, card) {
		return current.profile(), false, nil
	}
	created := store.aiAppEngine == nil
	engine := registeredAIAppEngine{
		SchemaVersion: aiAppRegistryVersion, Endpoint: strings.TrimRight(config.AIAppBaseURL, "/"),
		Namespace: config.AIAppNamespace, AllowedActions: card.readActions(), RegisteredAt: time.Now().UTC(), Card: card,
	}
	if err := saveJournalRecord(config.aiAppEngineRegistryFile(), engine, false); err != nil {
		return AgentProfile{}, false, err
	}
	store.aiAppEngine = &engine
	return engine.profile(), created, nil
}

// RestPostAIAppEngineCard godoc
// @ID PostAIAppEngineCard
// @Summary Register the Innogrid AI-App automation engine
// @Description Save the engine card and map the operator-configured AI-App origin and namespace to the advertised read tools.
// @Tags Agent Registry
// @Accept json
// @Produce json
// @Security AIAppEngineRegistryBearer
// @Param card body AIAppEngineCard true "Innogrid engine card"
// @Success 200 {object} AgentProfile
// @Success 201 {object} AgentProfile
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/integrations/ai-app/agents [post]
func (h restHandler) RestPostAIAppEngineCard(c echo.Context) error {
	if !h.config.aiAppEngineRegistryConfigured() {
		return focusedError(c, http.StatusServiceUnavailable, "AIAPP_ENGINE_REGISTRY_UNAVAILABLE", "Configure the AI-App engine registry and its dedicated registration token.", nil)
	}
	bearer := "Bearer " + h.config.AIAppEngineRegistryToken
	if subtle.ConstantTimeCompare([]byte(c.Request().Header.Get(echo.HeaderAuthorization)), []byte(bearer)) != 1 {
		return focusedError(c, http.StatusUnauthorized, "UNAUTHORIZED", "Valid AI-App engine registration authorization is required.", nil)
	}
	var card AIAppEngineCard
	if err := decodeFocusedRequest(c, &card); err != nil || !card.valid() {
		return focusedError(c, http.StatusBadRequest, "INVALID_ENGINE_CARD", "Supply a valid Innogrid AI-App engine card.", nil)
	}
	profile, created, err := h.service.runtimeAgents.saveAIAppEngine(h.config, card)
	if err == errAIAppRegistryInvalid {
		return aiAppEngineUnavailable(err).respond(c)
	}
	if err != nil {
		return focusedError(c, http.StatusInternalServerError, "ENGINE_REGISTRATION_SAVE_FAILED", "The AI-App engine card could not be saved.", err)
	}
	status := http.StatusOK
	if created {
		status = http.StatusCreated
	}
	return c.JSON(status, profile)
}

func aiAppEngineUnavailable(err error) *aiAppIntegrationFailure {
	if err == errAIAppRegistryInvalid {
		return &aiAppIntegrationFailure{
			status: http.StatusServiceUnavailable, code: "AIAPP_ENGINE_REGISTRY_INVALID", message: "The stored AI-App engine registration could not be loaded.",
		}
	}
	return &aiAppIntegrationFailure{
		status: http.StatusServiceUnavailable, code: "AIAPP_ENGINE_NOT_REGISTERED", message: "Register the AI-App engine card for this origin and namespace.",
	}
}
