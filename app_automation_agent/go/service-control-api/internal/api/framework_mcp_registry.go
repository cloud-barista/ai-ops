package api

import (
	"errors"
	"net"
	"net/http"
	"net/url"
	"os"
	"path"
	"regexp"
	"strings"
	"time"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"
)

const frameworkMCPPath = "/api/v1/framework-agents"

var (
	frameworkMCPName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)
	frameworkMCPEnv  = regexp.MustCompile(`^AIOPS_MCP_[A-Z0-9_]{1,96}_TOKEN$`)
)

type frameworkMCPPeer struct {
	Framework  string                      `json:"framework"`
	Namespace  string                      `json:"namespace"`
	Role       string                      `json:"role"`
	Endpoint   string                      `json:"endpoint"`
	ServerName string                      `json:"server_name"`
	TokenEnv   string                      `json:"token_env"`
	ReadTools  map[string]frameworkMCPTool `json:"read_tools"`
	token      string
}

type frameworkMCPTool struct {
	Name              string   `json:"name"`
	NamespaceArgument string   `json:"namespace_argument"`
	Arguments         []string `json:"arguments,omitempty"`
}

func (p *frameworkAccessPolicy) validateMCPPeers(tokens map[string]bool) bool {
	if len(p.MCPPeers) > 32 {
		return false
	}
	for name, peer := range p.MCPPeers {
		if !validFrameworkGrant(frameworkGrant{Agent: name, Framework: peer.Framework, Namespace: peer.Namespace, Actions: []string{"registry.read"}}) ||
			(peer.Framework != "AI-Infra" && peer.Framework != "AI-HA" && peer.Framework != "AI-Conn") ||
			name == aiAppEngineName || strings.TrimSpace(peer.Role) == "" || len(peer.Role) > 128 ||
			!validFrameworkMCPEndpoint(peer.Endpoint) || !frameworkMCPName.MatchString(peer.ServerName) ||
			!frameworkMCPEnv.MatchString(peer.TokenEnv) || len(peer.ReadTools) == 0 || len(peer.ReadTools) > 32 {
			return false
		}
		toolNames := map[string]bool{}
		for action, tool := range peer.ReadTools {
			if !strings.HasPrefix(action, "read.") || !frameworkActionPattern.MatchString(action) ||
				!frameworkMCPName.MatchString(tool.Name) || toolNames[tool.Name] || !tool.validArguments() {
				return false
			}
			toolNames[tool.Name] = true
		}
		token := strings.TrimSpace(os.Getenv(peer.TokenEnv))
		if token == "" || strings.ContainsAny(token, " \t\r\n") || tokens[token] {
			return false
		}
		tokens[token] = true
		peer.token = token
		p.MCPPeers[name] = peer
	}
	return true
}

func (tool frameworkMCPTool) validArguments() bool {
	if !frameworkMCPName.MatchString(tool.NamespaceArgument) || len(tool.Arguments) > 16 {
		return false
	}
	seen := map[string]bool{tool.NamespaceArgument: true}
	for _, argument := range tool.Arguments {
		if !frameworkMCPName.MatchString(argument) || seen[argument] {
			return false
		}
		seen[argument] = true
	}
	return true
}

func validFrameworkMCPEndpoint(endpoint string) bool {
	u, err := url.Parse(endpoint)
	if err != nil || len(endpoint) > 2048 || u.Hostname() == "" || u.User != nil ||
		u.ForceQuery || u.RawQuery != "" || strings.Contains(endpoint, "#") || u.EscapedPath() != u.Path ||
		u.Path == "" || path.Clean(u.Path) != strings.TrimRight(u.Path, "/") {
		return false
	}
	return u.Scheme == "https" || (u.Scheme == "http" && net.ParseIP(u.Hostname()).IsLoopback())
}

type frameworkMCPRecord struct {
	SchemaVersion  int       `json:"schema_version"`
	PeerDigest     string    `json:"peer_digest"`
	ContractDigest string    `json:"contract_digest"`
	ServerVersion  string    `json:"server_version"`
	RegisteredBy   string    `json:"registered_by"`
	RegisteredAt   time.Time `json:"registered_at"`
}

type frameworkMCPRegistration struct {
	frameworkMCPRecord
	Name       string                      `json:"name"`
	Framework  string                      `json:"framework"`
	Namespace  string                      `json:"namespace"`
	Role       string                      `json:"role"`
	Endpoint   string                      `json:"endpoint"`
	ServerName string                      `json:"server_name"`
	ReadTools  map[string]frameworkMCPTool `json:"read_tools"`
}

type frameworkMCPAccess struct {
	name      string
	principal string
	peer      frameworkMCPPeer
	path      string
}

func registerFrameworkMCPRoutes(server *echo.Echo, h restHandler) {
	server.PUT(frameworkMCPPath+"/:agent", h.RestPutFrameworkMCPRegistration)
	server.GET(frameworkMCPPath+"/:agent", h.RestGetFrameworkMCPRegistration)
	server.DELETE(frameworkMCPPath+"/:agent", h.RestDeleteFrameworkMCPRegistration)
	server.POST(frameworkMCPPath+"/:agent/actions/:action/invoke", h.RestPostFrameworkMCPInvocation)
}

func (h restHandler) frameworkMCPAccess(c echo.Context, action string) (frameworkMCPAccess, *aiAppIntegrationFailure) {
	principal, failure := h.frameworkPrincipal(c)
	if failure != nil {
		return frameworkMCPAccess{}, failure
	}
	name := c.Param("agent")
	peer, ok := h.service.frameworkAccess.MCPPeers[name]
	if !ok || !principal.allows(name, peer.Framework, peer.Namespace, action) {
		return frameworkMCPAccess{}, deniedFrameworkAction()
	}
	return frameworkMCPAccess{name, principal.Name, peer, h.config.path("runs", "framework-mcp", name+".json")}, nil
}

func (a frameworkMCPAccess) registration(record frameworkMCPRecord) frameworkMCPRegistration {
	return frameworkMCPRegistration{
		frameworkMCPRecord: record, Name: a.name, Framework: a.peer.Framework, Namespace: a.peer.Namespace,
		Role: a.peer.Role, Endpoint: a.peer.Endpoint, ServerName: a.peer.ServerName, ReadTools: a.peer.ReadTools,
	}
}

func (a frameworkMCPAccess) load() (frameworkMCPRecord, *aiAppIntegrationFailure) {
	record, err := loadJSON[frameworkMCPRecord](a.path)
	switch {
	case errors.Is(err, os.ErrNotExist):
		return record, &aiAppIntegrationFailure{status: http.StatusNotFound, code: "FRAMEWORK_MCP_NOT_REGISTERED", message: "Register the framework MCP agent first."}
	case err != nil || record.SchemaVersion != 1 || record.RegisteredAt.IsZero() ||
		!agentNamePattern.MatchString(record.RegisteredBy) || record.ServerVersion == "" ||
		!aiAppEvidenceDigest.MatchString(record.ContractDigest) || !aiAppEvidenceDigest.MatchString(record.PeerDigest):
		return record, frameworkMCPStorageFailure()
	case record.PeerDigest != frameworkDigest(a.peer):
		return record, changedFrameworkMCPContract()
	default:
		return record, nil
	}
}

// RestPutFrameworkMCPRegistration verifies and registers a configured MCP peer.
// @ID PutFrameworkMCPRegistration
// @Summary Register or refresh a configured framework MCP agent
// @Tags Agent Registry
// @Produce json
// @Security FrameworkPrincipalBearer
// @Param agent path string true "Configured agent name"
// @Success 200 {object} frameworkMCPRegistration
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 502 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/framework-agents/{agent} [put]
func (h restHandler) RestPutFrameworkMCPRegistration(c echo.Context) error {
	access, failure := h.frameworkMCPAccess(c, "registry.manage")
	if failure != nil {
		return failure.respond(c)
	}
	ctx, cancel := h.frameworkMCPContext(c)
	defer cancel()
	session, contract, failure := h.openFrameworkMCP(ctx, access.peer)
	if failure != nil {
		return failure.respond(c)
	}
	defer session.Close()
	digest := frameworkDigest(contract)
	record, failure := access.load()
	if failure == nil && record.ContractDigest == digest {
		return c.JSON(http.StatusOK, access.registration(record))
	}
	record = frameworkMCPRecord{
		SchemaVersion: 1, PeerDigest: frameworkDigest(access.peer), ContractDigest: digest,
		ServerVersion: contract.Version, RegisteredBy: access.principal, RegisteredAt: time.Now().UTC(),
	}
	if err := saveJournalRecord(access.path, record, false); err != nil {
		return frameworkMCPStorageFailure().respond(c)
	}
	access.audit("registry.manage", "registered")
	return c.JSON(http.StatusOK, access.registration(record))
}

// RestGetFrameworkMCPRegistration returns the stored registration without contacting the peer.
// @ID GetFrameworkMCPRegistration
// @Summary Read a framework MCP registration
// @Tags Agent Registry
// @Produce json
// @Security FrameworkPrincipalBearer
// @Param agent path string true "Configured agent name"
// @Success 200 {object} frameworkMCPRegistration
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 404 {object} map[string]interface{}
// @Failure 409 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/framework-agents/{agent} [get]
func (h restHandler) RestGetFrameworkMCPRegistration(c echo.Context) error {
	access, failure := h.frameworkMCPAccess(c, "registry.read")
	if failure != nil {
		return failure.respond(c)
	}
	record, failure := access.load()
	if failure != nil {
		return failure.respond(c)
	}
	return c.JSON(http.StatusOK, access.registration(record))
}

// RestDeleteFrameworkMCPRegistration removes the stored registration.
// @ID DeleteFrameworkMCPRegistration
// @Summary Unregister a framework MCP agent
// @Tags Agent Registry
// @Security FrameworkPrincipalBearer
// @Param agent path string true "Configured agent name"
// @Success 204
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 500 {object} map[string]interface{}
// @Failure 503 {object} map[string]interface{}
// @Router /api/v1/framework-agents/{agent} [delete]
func (h restHandler) RestDeleteFrameworkMCPRegistration(c echo.Context) error {
	access, failure := h.frameworkMCPAccess(c, "registry.manage")
	if failure != nil {
		return failure.respond(c)
	}
	if err := os.Remove(access.path); err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			return frameworkMCPStorageFailure().respond(c)
		}
	} else {
		access.audit("registry.manage", "unregistered")
	}
	return c.NoContent(http.StatusNoContent)
}

func (a frameworkMCPAccess) audit(action, outcome string) {
	log.Info().Str("principal", a.principal).Str("agent", a.name).Str("framework", a.peer.Framework).
		Str("namespace", a.peer.Namespace).Str("action", action).Str("outcome", outcome).Msg("framework MCP operation")
}

func frameworkMCPStorageFailure() *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusInternalServerError, code: "FRAMEWORK_MCP_REGISTRY_ERROR", message: "The framework MCP registration could not be accessed."}
}

func changedFrameworkMCPContract() *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{status: http.StatusConflict, code: "FRAMEWORK_MCP_REGISTRATION_CHANGED", message: "The MCP peer binding or tool contract changed. Verify the contract and refresh its registration."}
}
