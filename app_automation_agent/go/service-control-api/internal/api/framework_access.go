package api

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/labstack/echo/v4"
)

var (
	frameworkActionPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}(\.[a-z][a-z0-9_]{0,31})?$`)
	principalEnvPattern    = regexp.MustCompile(`^AIOPS_PRINCIPAL_[A-Z0-9_]{1,96}_TOKEN$`)
)

type frameworkGrant struct {
	Agent     string   `json:"agent"`
	Framework string   `json:"framework"`
	Namespace string   `json:"namespace"`
	Actions   []string `json:"actions"`
}

type frameworkPrincipal struct {
	Name     string           `json:"name"`
	TokenEnv string           `json:"token_env"`
	Grants   []frameworkGrant `json:"grants"`
	bearer   string
}

func (p *frameworkPrincipal) allows(agent, framework, namespace, action string) bool {
	for _, grant := range p.Grants {
		if grant.Agent == agent && grant.Framework == framework && grant.Namespace == namespace &&
			contains(grant.Actions, action) {
			return true
		}
	}
	return false
}

type frameworkAccessPolicy struct {
	Version    int                         `json:"version"`
	Principals []frameworkPrincipal        `json:"principals"`
	MCPPeers   map[string]frameworkMCPPeer `json:"mcp_peers,omitempty"`
	digest     string
}

func loadFrameworkAccessPolicy(cfg ServerConfig) (*frameworkAccessPolicy, error) {
	path := cfg.FrameworkAccessPolicyFile
	if path == "" {
		return nil, nil
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(cfg.RepoRoot, path)
	}
	invalid := errors.New("framework access policy or principal credential configuration is invalid")
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) > 1<<20 {
		return nil, invalid
	}
	var policy frameworkAccessPolicy
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if dec.Decode(&policy) != nil || dec.Decode(new(any)) != io.EOF ||
		policy.Version != 1 || len(policy.Principals) == 0 || len(policy.Principals) > 128 {
		return nil, invalid
	}
	names := map[string]bool{}
	tokens := map[string]bool{}
	for _, token := range []string{
		cfg.AIAppToken, cfg.AIAppEngineRegistryToken, cfg.AIAppEngineApprovalToken, cfg.OpsLLMToken,
		strings.TrimSpace(cfg.AutonomyAdminToken),
	} {
		tokens[token] = true
	}
	for i := range policy.Principals {
		principal := &policy.Principals[i]
		if !agentNamePattern.MatchString(principal.Name) || names[principal.Name] ||
			principal.Name == "ai-app-gateway" || principal.Name == "ai-app-mcp" ||
			!principalEnvPattern.MatchString(principal.TokenEnv) || len(principal.Grants) == 0 {
			return nil, invalid
		}
		token := strings.TrimSpace(os.Getenv(principal.TokenEnv))
		if token == "" || strings.ContainsAny(token, " \t\r\n") || tokens[token] {
			return nil, invalid
		}
		for _, grant := range principal.Grants {
			if !validFrameworkGrant(grant) {
				return nil, invalid
			}
		}
		names[principal.Name], tokens[token] = true, true
		principal.bearer = "Bearer " + token
	}
	if !policy.validateMCPPeers(tokens) {
		return nil, invalid
	}
	hash := sha256.Sum256(raw)
	policy.digest = hex.EncodeToString(hash[:])
	if err := policy.auditActivation(cfg); err != nil {
		return nil, err
	}
	return &policy, nil
}

func validFrameworkGrant(grant frameworkGrant) bool {
	switch grant.Framework {
	case "AI-App", "AI-Infra", "AI-HA", "AI-Conn", "AI-Ops":
	default:
		return false
	}
	if !agentNamePattern.MatchString(grant.Agent) || !aiAppNodeID.MatchString(grant.Namespace) ||
		len(grant.Actions) == 0 {
		return false
	}
	for _, action := range grant.Actions {
		if !frameworkActionPattern.MatchString(action) {
			return false
		}
	}
	return true
}

type frameworkPolicyActivation struct {
	Digest      string                `json:"digest"`
	ActivatedAt time.Time             `json:"activated_at"`
	Policy      frameworkAccessPolicy `json:"policy"`
}

func (policy *frameworkAccessPolicy) auditActivation(cfg ServerConfig) error {
	currentPath := cfg.path("runs", "framework-access", "current.json")
	current, err := loadJSON[frameworkPolicyActivation](currentPath)
	if err == nil && current.Digest == policy.digest {
		return nil
	}
	failure := errors.New("framework access policy activation could not be recorded")
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return failure
	}
	at := time.Now().UTC()
	activation := frameworkPolicyActivation{Digest: policy.digest, ActivatedAt: at, Policy: *policy}
	event := at.Format("20060102T150405.000000000Z") + "-" + policy.digest + ".json"
	if err := saveJournalRecord(cfg.path("runs", "framework-access", "history", event), activation, true); err != nil {
		return failure
	}
	if err := saveJournalRecord(currentPath, activation, false); err != nil {
		return failure
	}
	return nil
}

func (h restHandler) frameworkPrincipal(c echo.Context) (*frameworkPrincipal, *aiAppIntegrationFailure) {
	policy := h.service.frameworkAccess
	if policy == nil {
		return nil, &aiAppIntegrationFailure{
			status: http.StatusServiceUnavailable, code: "FRAMEWORK_ACCESS_UNAVAILABLE",
			message: "A valid framework access policy is required.",
		}
	}
	got := []byte(c.Request().Header.Get(echo.HeaderAuthorization))
	for i := range policy.Principals {
		principal := &policy.Principals[i]
		if subtle.ConstantTimeCompare(got, []byte(principal.bearer)) == 1 {
			return principal, nil
		}
	}
	return nil, &aiAppIntegrationFailure{
		status: http.StatusUnauthorized, code: "UNAUTHORIZED", message: "Valid principal authorization is required.",
	}
}

func deniedFrameworkAction() *aiAppIntegrationFailure {
	return &aiAppIntegrationFailure{
		status: http.StatusForbidden, code: "FRAMEWORK_ACTION_FORBIDDEN",
		message: "The principal is not authorized for this agent, framework, namespace and action.",
	}
}
