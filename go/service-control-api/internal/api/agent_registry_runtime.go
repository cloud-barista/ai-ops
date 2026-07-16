package api

import (
	"context"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	agentSourceConfiguration    = "configuration"
	agentSourceRuntime          = "runtime"
	invocationStatusNotExecuted = "not_executed"
)

var agentNamePattern = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_-]{2,63}$`)

type runtimeAgentStore struct {
	mu     sync.RWMutex
	agents map[string]AgentProfile
}

func newRuntimeAgentStore() *runtimeAgentStore {
	return &runtimeAgentStore{agents: make(map[string]AgentProfile)}
}

func (store *runtimeAgentStore) get(name string) (AgentProfile, bool) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	agent, ok := store.agents[name]
	return agent, ok
}

func (store *runtimeAgentStore) add(agent AgentProfile) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if _, exists := store.agents[agent.Name]; exists {
		return fmt.Errorf("agent is already registered: %s", agent.Name)
	}
	store.agents[agent.Name] = agent
	return nil
}

func (store *runtimeAgentStore) list() []AgentProfile {
	store.mu.RLock()
	defer store.mu.RUnlock()
	agents := make([]AgentProfile, 0, len(store.agents))
	for _, agent := range store.agents {
		agents = append(agents, agent)
	}
	sort.SliceStable(agents, func(i, j int) bool {
		return agents[i].Name < agents[j].Name
	})
	return agents
}

func (store *runtimeAgentStore) count() int {
	store.mu.RLock()
	defer store.mu.RUnlock()
	return len(store.agents)
}

func (service Service) RegisterExternalAgent(ctx context.Context, request ExternalAgentRegistrationRequest) (AgentProfile, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentProfile{}, err
	}
	if err := validateExternalAgentRegistration(request); err != nil {
		return AgentProfile{}, err
	}
	registry, err := loadAgentRegistry(service.config.path("config", "agent_registry.json"))
	if err != nil {
		return AgentProfile{}, err
	}
	if _, err := findAgent(registry.Agents, request.Name); err == nil {
		return AgentProfile{}, fmt.Errorf("agent name conflicts with configured registry: %s", request.Name)
	}

	enabled := true
	if request.Enabled != nil {
		enabled = *request.Enabled
	}
	agent := AgentProfile{
		Name:             request.Name,
		KoreanName:       strings.TrimSpace(request.KoreanName),
		Version:          strings.TrimSpace(request.Version),
		Role:             strings.TrimSpace(request.Role),
		Responsibilities: normalizeStringList(request.Responsibilities),
		Capabilities:     normalizeStringList(request.Capabilities),
		BoundedActions:   normalizeStringList(request.BoundedActions),
		RewardSignals:    normalizeStringList(request.RewardSignals),
		Enabled:          enabled,
		Endpoint:         strings.TrimRight(strings.TrimSpace(request.Endpoint), "/"),
		InvocationPath:   request.InvocationPath,
		Source:           agentSourceRuntime,
		RegisteredAt:     time.Now().UTC().Format(time.RFC3339),
	}
	if err := service.runtimeAgents.add(agent); err != nil {
		return AgentProfile{}, err
	}
	return agent, nil
}

func (service Service) BuildAgentInvocationPlan(ctx context.Context, agentName string, request AgentInvocationPlanRequest) (AgentInvocationPlan, error) {
	if err := ensureContext(ctx); err != nil {
		return AgentInvocationPlan{}, err
	}
	agent, err := service.ShowAgent(ctx, agentName)
	if err != nil {
		return AgentInvocationPlan{}, err
	}
	if !agent.Enabled {
		return AgentInvocationPlan{}, fmt.Errorf("agent is disabled: %s", agentName)
	}
	if agent.Source != agentSourceRuntime || agent.Endpoint == "" || agent.InvocationPath == "" {
		return AgentInvocationPlan{}, fmt.Errorf("agent does not define a runtime invocation endpoint: %s", agentName)
	}
	if !contains(agent.Capabilities, request.Capability) {
		return AgentInvocationPlan{}, fmt.Errorf("capability is not registered for agent %s: %s", agentName, request.Capability)
	}
	if !contains(agent.BoundedActions, request.Action) {
		return AgentInvocationPlan{}, fmt.Errorf("action is outside the agent boundary for %s: %s", agentName, request.Action)
	}

	return AgentInvocationPlan{
		Valid:           true,
		Agent:           agent.Name,
		Capability:      request.Capability,
		Action:          request.Action,
		TargetURL:       agent.Endpoint + agent.InvocationPath,
		Parameters:      request.Parameters,
		ExecutionStatus: invocationStatusNotExecuted,
		Reason:          "registered capability and bounded action were validated; external execution was not performed",
	}, nil
}

func validateExternalAgentRegistration(request ExternalAgentRegistrationRequest) error {
	if !agentNamePattern.MatchString(request.Name) {
		return fmt.Errorf("agent name must match %s", agentNamePattern.String())
	}
	if strings.TrimSpace(request.Version) == "" || strings.TrimSpace(request.Role) == "" {
		return fmt.Errorf("agent version and role are required")
	}
	if len(normalizeStringList(request.Capabilities)) == 0 {
		return fmt.Errorf("at least one capability is required")
	}
	if len(normalizeStringList(request.BoundedActions)) == 0 {
		return fmt.Errorf("at least one bounded action is required")
	}
	endpoint, err := url.Parse(strings.TrimSpace(request.Endpoint))
	if err != nil || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.Host == "" {
		return fmt.Errorf("endpoint must be an absolute HTTP or HTTPS URL")
	}
	if endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return fmt.Errorf("endpoint must not contain credentials, query parameters, or fragments")
	}
	if !strings.HasPrefix(request.InvocationPath, "/") || strings.Contains(request.InvocationPath, "?") || strings.Contains(request.InvocationPath, "#") {
		return fmt.Errorf("invocation_path must be an absolute URL path without query parameters or fragments")
	}
	if strings.Contains(request.InvocationPath, "..") {
		return fmt.Errorf("invocation_path must not contain parent path segments")
	}
	return nil
}

func normalizeStringList(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
