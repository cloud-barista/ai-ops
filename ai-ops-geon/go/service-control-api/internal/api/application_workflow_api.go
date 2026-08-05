package api

import (
	"errors"
	"fmt"
	"mime/multipart"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
	"kyunghee-aiops/service-control-api/internal/controlrun"
)

const applicationUploadMemoryBytes int64 = 8 << 20

var (
	applicationPositiveInteger    = regexp.MustCompile(`^[1-9][0-9]*$`)
	applicationNonNegativeInteger = regexp.MustCompile(`^[0-9]+$`)
	applicationMemoryQuantity     = regexp.MustCompile(`^[1-9][0-9]*(Mi|Gi|Ti)$`)
)

// PackageControlRunErrorResponse documents the flat fields returned by either
// an ErrorResponse before Run creation or a persisted Run after creation.
type PackageControlRunErrorResponse struct {
	ErrorResponse
	controlrun.Run
}

// RestPostControlRunFromPackage godoc
// @ID CreateControlRunFromPackage
// @Summary Package and register an application before generating a guarded DeploymentManifest
// @Description Accept application source as multipart form data, invoke AppDeploy packaging and registration, then run the existing guarded ControlRun planning workflow.
// @Description Successful Package and App registration results remain in application and partial_result after a later Planner or Guard failure and are not automatically rolled back. AppDeploy submission remains a separate explicit request.
// @Tags AI Application Automation
// @Accept multipart/form-data
// @Produce json
// @Param source formData file true "Application source file or source archive"
// @Param package_type formData string true "Package type"
// @Param app_name formData string true "Application name"
// @Param app_version formData string true "Application version"
// @Param entrypoint formData string true "Application entrypoint"
// @Param runtime_type formData string true "Runtime type: cpu or gpu"
// @Param service_port formData integer false "Application service port"
// @Param healthcheck_path formData string false "HTTP healthcheck path"
// @Param natural_language_request formData string true "Deployment planning request"
// @Param candidate_id formData string true "Configured Planner candidate"
// @Param requested_by formData string false "Trusted requester; defaults to ai-agent" default(ai-agent)
// @Param agent_name formData string false "Manifest Planner Agent"
// @Param target_profile_id formData string false "AppDeploy Target hint; AppDeploy performs final Target selection"
// @Param cpu formData string true "CPU requirement"
// @Param memory formData string true "Memory requirement"
// @Param gpu formData string true "GPU requirement"
// @Param storage formData string true "Storage requirement"
// @Param cost_policy formData string false "Cost policy; empty or min_cost" Enums(min_cost)
// @Success 201 {object} controlrun.Run "Package and App registered; DeploymentManifest approved"
// @Failure 400 {object} PackageControlRunErrorResponse "Malformed multipart or field validation returns api.ErrorResponse; Request Guard rejection returns controlrun.Run"
// @Failure 403 {object} controlrun.Run "Agent Registry or bounded Action rejection"
// @Failure 413 {object} ErrorResponse "Upload exceeds AIOPS_APP_UPLOAD_MAX_BYTES"
// @Failure 422 {object} controlrun.Run "Planner or Manifest Guard rejection with partial results preserved"
// @Failure 500 {object} PackageControlRunErrorResponse "Pre-ControlRun setup failures return api.ErrorResponse; unexpected ControlRun states return controlrun.Run"
// @Failure 502 {object} controlrun.Run "AppDeploy Package or App registration failure with available partial results preserved"
// @Router /api/v1/control-runs/from-package [post]
func (handler restHandler) RestPostControlRunFromPackage(context echo.Context) error {
	request := context.Request()
	request.Body = http.MaxBytesReader(
		context.Response(),
		request.Body,
		handler.config.AppUploadMaxBytes,
	)
	parseErr := request.ParseMultipartForm(applicationUploadMemoryBytes)
	if request.MultipartForm != nil {
		defer request.MultipartForm.RemoveAll()
	}
	if parseErr != nil {
		if isApplicationUploadTooLarge(parseErr) {
			return jsonError(
				context,
				http.StatusRequestEntityTooLarge,
				"Application upload exceeds the configured limit",
				parseErr,
			)
		}
		return jsonError(context, http.StatusBadRequest, "Malformed multipart request", parseErr)
	}

	source, header, err := request.FormFile("source")
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "Application source is required", err)
	}
	defer source.Close()
	if header.Size <= 0 {
		return jsonError(
			context,
			http.StatusBadRequest,
			"Application source must not be empty",
			fmt.Errorf("source file is empty"),
		)
	}

	servicePort, err := parseOptionalServicePort(request)
	if err != nil {
		return jsonError(context, http.StatusBadRequest, "service_port must be a valid TCP port", err)
	}
	runtimeType := strings.TrimSpace(request.FormValue("runtime_type"))
	input := CreatePackageControlRunInput{
		Package: appdeploy.PackageUpload{
			Source:          source,
			Filename:        header.Filename,
			PackageType:     strings.TrimSpace(request.FormValue("package_type")),
			AppName:         strings.TrimSpace(request.FormValue("app_name")),
			AppVersion:      strings.TrimSpace(request.FormValue("app_version")),
			Entrypoint:      strings.TrimSpace(request.FormValue("entrypoint")),
			RuntimeType:     runtimeType,
			ServicePort:     servicePort,
			HealthcheckPath: strings.TrimSpace(request.FormValue("healthcheck_path")),
		},
		Planner: CreateControlRunRequest{
			NaturalLanguageRequest: strings.TrimSpace(request.FormValue("natural_language_request")),
			CandidateID:            strings.TrimSpace(request.FormValue("candidate_id")),
			TargetProfileID:        strings.TrimSpace(request.FormValue("target_profile_id")),
			RequestedBy:            strings.TrimSpace(request.FormValue("requested_by")),
			AgentName:              strings.TrimSpace(request.FormValue("agent_name")),
		},
		Requirements: appdeploy.DeploymentRequirements{
			Runtime:     runtimeType,
			Accelerator: applicationAccelerator(runtimeType),
			Resources: appdeploy.ResourceRequirements{
				CPU:     strings.TrimSpace(request.FormValue("cpu")),
				Memory:  strings.TrimSpace(request.FormValue("memory")),
				GPU:     strings.TrimSpace(request.FormValue("gpu")),
				Storage: strings.TrimSpace(request.FormValue("storage")),
			},
			CostPolicy: strings.TrimSpace(request.FormValue("cost_policy")),
		},
	}
	if err := validatePackageControlRunInput(input); err != nil {
		return jsonError(
			context,
			http.StatusBadRequest,
			"Application workflow fields are invalid",
			err,
		)
	}

	run, workflowErr := handler.service.CreateControlRunFromPackage(
		request.Context(),
		input,
	)
	if workflowErr != nil && run.RunID == "" {
		return jsonError(
			context,
			http.StatusInternalServerError,
			"Package ControlRun could not be created",
			workflowErr,
		)
	}
	return context.JSON(controlRunFromPackageHTTPStatus(run.Status), run)
}

func validatePackageControlRunInput(input CreatePackageControlRunInput) error {
	requiredFields := []struct {
		name  string
		value string
	}{
		{name: "package_type", value: input.Package.PackageType},
		{name: "app_name", value: input.Package.AppName},
		{name: "app_version", value: input.Package.AppVersion},
		{name: "entrypoint", value: input.Package.Entrypoint},
		{name: "runtime_type", value: input.Package.RuntimeType},
		{name: "natural_language_request", value: input.Planner.NaturalLanguageRequest},
		{name: "candidate_id", value: input.Planner.CandidateID},
		{name: "cpu", value: input.Requirements.Resources.CPU},
		{name: "memory", value: input.Requirements.Resources.Memory},
		{name: "gpu", value: input.Requirements.Resources.GPU},
		{name: "storage", value: input.Requirements.Resources.Storage},
	}
	for _, field := range requiredFields {
		if field.value == "" {
			return fmt.Errorf("%s is required", field.name)
		}
	}

	if input.Requirements.Runtime != "cpu" && input.Requirements.Runtime != "gpu" {
		return fmt.Errorf("runtime_type must be cpu or gpu")
	}
	if !applicationPositiveInteger.MatchString(input.Requirements.Resources.CPU) {
		return fmt.Errorf("cpu must be a positive integer")
	}
	if !applicationMemoryQuantity.MatchString(input.Requirements.Resources.Memory) {
		return fmt.Errorf("memory must use Mi, Gi, or Ti")
	}
	if !applicationNonNegativeInteger.MatchString(input.Requirements.Resources.GPU) {
		return fmt.Errorf("gpu must be a non-negative integer")
	}
	if !applicationMemoryQuantity.MatchString(input.Requirements.Resources.Storage) {
		return fmt.Errorf("storage must use Mi, Gi, or Ti")
	}
	gpu, err := strconv.Atoi(input.Requirements.Resources.GPU)
	if err != nil {
		return fmt.Errorf("gpu must be a non-negative integer: %w", err)
	}
	if input.Requirements.Runtime == "cpu" && gpu != 0 {
		return fmt.Errorf("cpu runtime requires zero GPUs")
	}
	if input.Requirements.Runtime == "gpu" && gpu < 1 {
		return fmt.Errorf("gpu runtime requires at least one GPU")
	}
	return nil
}

func parseOptionalServicePort(request *http.Request) (int, error) {
	value := strings.TrimSpace(request.FormValue("service_port"))
	if value == "" {
		return 0, nil
	}
	port, err := strconv.Atoi(value)
	if err != nil {
		return 0, err
	}
	if port < 1 || port > 65535 {
		return 0, fmt.Errorf("service_port must be between 1 and 65535")
	}
	return port, nil
}

func applicationAccelerator(runtimeType string) string {
	if runtimeType == "gpu" {
		return "nvidia"
	}
	return "none"
}

func isApplicationUploadTooLarge(err error) bool {
	var maxBytesError *http.MaxBytesError
	return errors.As(err, &maxBytesError) || errors.Is(err, multipart.ErrMessageTooLarge)
}

func controlRunFromPackageHTTPStatus(status controlrun.Status) int {
	switch status {
	case controlrun.StatusManifestApproved:
		return http.StatusCreated
	case controlrun.StatusPackageFailed, controlrun.StatusAppRegistrationFailed:
		return http.StatusBadGateway
	default:
		return controlRunHTTPStatus(status)
	}
}
