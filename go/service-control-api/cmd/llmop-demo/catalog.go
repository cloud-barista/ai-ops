package main

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/llmop"
)

const demoCatalogVersion = "ai-ops.llm-op-demo-catalog/v1"

var (
	demoCountPattern    = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)
	demoQuantityPattern = regexp.MustCompile(`^([1-9][0-9]*)(Mi|Gi|Ti)$`)
)

type demoCatalog struct {
	SchemaVersion         string                    `json:"schema_version"`
	ExecutionPolicy       demoExecutionPolicy       `json:"execution_policy"`
	PlannerModelBindings []demoPlannerModelBinding `json:"planner_model_bindings"`
	AIServices            []demoAIService            `json:"ai_services"`
}

type demoExecutionPolicy struct {
	Mode                   string `json:"mode"`
	NetworkAllowed         bool   `json:"network_allowed"`
	WeightsLoaded          bool   `json:"weights_loaded"`
	ModelSelectionEnabled  bool   `json:"model_selection_enabled"`
	AppDeploySubmitEnabled bool   `json:"appdeploy_submit_enabled"`
}

type demoPlannerModelBinding struct {
	CandidateID             string  `json:"candidate_id"`
	BindingMode             string  `json:"binding_mode"`
	Provider                string  `json:"provider"`
	ActualModel             string  `json:"actual_model"`
	IntendedModel           string  `json:"intended_model"`
	InitialPreferenceWeight float64 `json:"initial_preference_weight"`
	WeightSemantics         string  `json:"weight_semantics"`
	JSONMode                bool    `json:"json_mode"`
	NetworkAllowed          bool    `json:"network_allowed"`
	WeightsLoaded           bool    `json:"weights_loaded"`
	BenchmarkStatus         string  `json:"benchmark_status"`
}

type demoAIService struct {
	ServiceID            string           `json:"service_id"`
	DisplayName          string           `json:"display_name"`
	Task                 string           `json:"task"`
	Description          string           `json:"description"`
	AppVersionID         string           `json:"app_version_id"`
	WorkloadModelRef     string           `json:"workload_model_ref"`
	LifecycleStatus      string           `json:"lifecycle_status"`
	InputMediaType       string           `json:"input_media_type"`
	OutputMediaType      string           `json:"output_media_type"`
	NetworkEndpointReady bool             `json:"network_endpoint_ready"`
	WeightsLoaded        bool             `json:"weights_loaded"`
	DefaultResources     demoResourcePlan `json:"default_resources"`
}

type demoResourcePlan struct {
	CPU         string `json:"cpu"`
	Memory      string `json:"memory"`
	GPU         string `json:"gpu"`
	Storage     string `json:"storage"`
	Accelerator string `json:"accelerator"`
}

func loadDemoBinding(
	path string,
	serviceID string,
	request llmop.Request,
) (llmclient.Candidate, demoAIService, error) {
	var catalog demoCatalog
	if err := decodeJSONFile(path, &catalog); err != nil {
		return llmclient.Candidate{}, demoAIService{}, err
	}
	return resolveDemoBinding(catalog, serviceID, request)
}

func resolveDemoBinding(
	catalog demoCatalog,
	serviceID string,
	request llmop.Request,
) (llmclient.Candidate, demoAIService, error) {
	if catalog.SchemaVersion != demoCatalogVersion {
		return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
			"demo catalog schema_version must be %s",
			demoCatalogVersion,
		)
	}
	policy := catalog.ExecutionPolicy
	if policy.Mode != "offline_fixture_only" || policy.NetworkAllowed ||
		policy.WeightsLoaded || policy.ModelSelectionEnabled ||
		policy.AppDeploySubmitEnabled {
		return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
			"demo catalog execution_policy must prohibit network, weights, selection, and submission",
		)
	}

	bindings := make(map[string]demoPlannerModelBinding)
	for _, binding := range catalog.PlannerModelBindings {
		candidateID := strings.TrimSpace(binding.CandidateID)
		if candidateID == "" {
			return llmclient.Candidate{}, demoAIService{}, fmt.Errorf("demo planner candidate_id is required")
		}
		if _, exists := bindings[candidateID]; exists {
			return llmclient.Candidate{}, demoAIService{}, fmt.Errorf("duplicate demo planner candidate_id %q", candidateID)
		}
		if binding.BindingMode != "caller_pinned" ||
			binding.Provider != llmop.OfflineFixtureProvider ||
			binding.ActualModel != llmop.OfflineFixtureActualModel ||
			strings.TrimSpace(binding.IntendedModel) == "" ||
			!binding.JSONMode || binding.NetworkAllowed || binding.WeightsLoaded ||
			binding.BenchmarkStatus != "not_executed" ||
			binding.InitialPreferenceWeight <= 0 ||
			binding.InitialPreferenceWeight > 1 ||
			binding.WeightSemantics != "metadata_only_no_selection" {
			return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
				"demo planner binding %q violates the caller-pinned offline contract",
				candidateID,
			)
		}
		bindings[candidateID] = binding
	}

	services := make(map[string]demoAIService)
	for _, service := range catalog.AIServices {
		id := strings.TrimSpace(service.ServiceID)
		if id == "" {
			return llmclient.Candidate{}, demoAIService{}, fmt.Errorf("demo AI service_id is required")
		}
		if _, exists := services[id]; exists {
			return llmclient.Candidate{}, demoAIService{}, fmt.Errorf("duplicate demo AI service_id %q", id)
		}
		if strings.TrimSpace(service.DisplayName) == "" ||
			strings.TrimSpace(service.Task) == "" ||
			strings.TrimSpace(service.Description) == "" ||
			strings.TrimSpace(service.AppVersionID) == "" ||
			strings.TrimSpace(service.WorkloadModelRef) == "" ||
			service.LifecycleStatus != "demo_catalog_only" ||
			strings.TrimSpace(service.InputMediaType) == "" ||
			strings.TrimSpace(service.OutputMediaType) == "" ||
			service.NetworkEndpointReady || service.WeightsLoaded ||
			!demoResourcesValid(service.DefaultResources) {
			return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
				"demo AI service %q violates the offline service contract",
				id,
			)
		}
		services[id] = service
	}

	binding, ok := bindings[request.CandidateID]
	if !ok {
		return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
			"request candidate_id %q has no exact caller-pinned demo binding",
			request.CandidateID,
		)
	}
	service, ok := services[strings.TrimSpace(serviceID)]
	if !ok {
		return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
			"demo AI service %q is not registered",
			serviceID,
		)
	}
	if service.AppVersionID != request.Application.AppVersionID {
		return llmclient.Candidate{}, demoAIService{}, fmt.Errorf(
			"demo service app_version_id %q does not match request application %q",
			service.AppVersionID,
			request.Application.AppVersionID,
		)
	}

	return llmclient.Candidate{
		CandidateID: request.CandidateID,
		RoleLabel:   "caller-pinned-llm-operation-planner",
		Provider:    binding.Provider,
		ActualModel: binding.ActualModel,
		Enabled:     true,
		JSONMode:    true,
	}, service, nil
}

func demoResourcesValid(resources demoResourcePlan) bool {
	if !demoCountPattern.MatchString(resources.CPU) ||
		!demoCountPattern.MatchString(resources.GPU) {
		return false
	}
	cpu, cpuErr := strconv.ParseUint(resources.CPU, 10, 64)
	gpu, gpuErr := strconv.ParseUint(resources.GPU, 10, 64)
	memory, memoryErr := demoQuantityMi(resources.Memory)
	storage, storageErr := demoQuantityMi(resources.Storage)
	if cpuErr != nil || gpuErr != nil || memoryErr != nil || storageErr != nil ||
		cpu == 0 || cpu > 256 || gpu > 16 ||
		memory > 2*1024*1024 || storage > 64*1024*1024 {
		return false
	}
	if gpu == 0 {
		return resources.Accelerator == "none"
	}
	return resources.Accelerator == "nvidia"
}

func demoQuantityMi(value string) (uint64, error) {
	matches := demoQuantityPattern.FindStringSubmatch(value)
	if len(matches) != 3 {
		return 0, fmt.Errorf("invalid demo resource quantity %q", value)
	}
	amount, err := strconv.ParseUint(matches[1], 10, 64)
	if err != nil {
		return 0, err
	}
	multiplier := uint64(1)
	switch matches[2] {
	case "Gi":
		multiplier = 1024
	case "Ti":
		multiplier = 1024 * 1024
	}
	if amount > ^uint64(0)/multiplier {
		return 0, fmt.Errorf("demo resource quantity %q overflows", value)
	}
	return amount * multiplier, nil
}
