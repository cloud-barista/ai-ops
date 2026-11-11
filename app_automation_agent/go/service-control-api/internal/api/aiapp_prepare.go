package api

import (
	"errors"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/labstack/echo/v4"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/model"
)

var (
	aiAppCatalogID = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,62}$`)
	aiAppName      = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,62}$`)
	aiAppNodeID    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
)

// RestPostAIAppPrepare godoc
// @ID PrepareAIAppDeployment
// @Summary Prepare AI-App requests for an approved initial revision
// @Description Return inference and application request bodies for a single approved CB-Tumblebug node. The selected resource ID must be nsId/infraId/nodeId and the profile's application version must be an AI-App catalog version number. Catalog and node verification, application environment binding, and submission are separate steps.
// @Tags AI-App Integration
// @Accept json
// @Produce json
// @Param correlation_id path string true "Flow ID"
// @Param request body model.AIAppPreparationRequest true "Selected AI-App model, artifact and optional vLLM baseline"
// @Success 200 {object} model.AIAppPreparation
// @Failure 400 {object} map[string]interface{} "Malformed request"
// @Failure 409 {object} map[string]interface{} "Flow approval or AI-App binding is incomplete"
// @Failure 422 {object} map[string]interface{} "Invalid model or artifact ID"
// @Router /api/v1/agent-control/flows/{correlation_id}/aiapp/prepare [post]
func (h restHandler) RestPostAIAppPrepare(c echo.Context) error {
	var input model.AIAppPreparationRequest
	if err := decodeFocusedRequest(c, &input); err != nil {
		return focusedError(c, http.StatusBadRequest, "INVALID_REQUEST", "Request body must be one valid AI-App preparation object.", err)
	}
	if !aiAppCatalogID.MatchString(input.ModelID) || !aiAppCatalogID.MatchString(input.ArtifactID) {
		return focusedError(c, http.StatusUnprocessableEntity, "INVALID_AIAPP_CATALOG_IDS", "Supply a valid AI-App model_id and artifact_id.", nil)
	}

	id := c.Param("correlation_id")
	var result model.AIAppPreparation
	err := h.service.agentControl.WithApprovedInitialFlow(id, func(flow agentcontrol.Flow) error {
		var err error
		result, err = prepareAIAppInitialFlow(flow, input)
		return err
	})
	if err != nil {
		return focusedError(c, http.StatusConflict, "AIAPP_PREPARATION_CONFLICT", "An approved single-node initial revision and a valid AI-App node, runtime, and application binding are required.", err)
	}
	return c.JSON(http.StatusOK, result)
}

func prepareAIAppInitialFlow(flow agentcontrol.Flow, input model.AIAppPreparationRequest) (model.AIAppPreparation, error) {
	revision := flow.ManifestRevisions[0]
	spec := revision.DesiredDeploymentSpec
	if spec.DesiredInfrastructure.NodeCount != 1 || spec.InferenceConfiguration.Replicas != 1 {
		return model.AIAppPreparation{}, errors.New("AI-App preparation requires one node and one replica")
	}
	node := strings.Split(flow.Decision.SelectedCandidateID, "/")
	if len(node) != 3 || !aiAppNodeID.MatchString(node[0]) || !aiAppNodeID.MatchString(node[1]) || !aiAppNodeID.MatchString(node[2]) {
		return model.AIAppPreparation{}, errors.New("approved resource_id must be nsId/infraId/nodeId")
	}
	if !validAIAppPlacementReview(input.PlacementReview, node[0]) {
		return model.AIAppPreparation{}, errors.New("the placement review does not match the approved namespace or is incomplete")
	}
	version, err := strconv.Atoi(spec.Application.AppVersion)
	if !aiAppName.MatchString(spec.Application.AppID) || err != nil || version < 1 || strconv.Itoa(version) != spec.Application.AppVersion {
		return model.AIAppPreparation{}, errors.New("approved app_id and app_version must be the AI-App catalog name and version number")
	}
	runtime := strings.ToLower(spec.InferenceConfiguration.RuntimeEngine)
	accelerator := spec.DesiredInfrastructure.Accelerator
	if !aiAppCatalogID.MatchString(runtime) || (accelerator.Count > 0 && accelerator.Type != "GPU") {
		return model.AIAppPreparation{}, errors.New("approved runtime or accelerator cannot be projected to AI-App")
	}
	var args []string
	if input.MaxNumSeqs != nil {
		if runtime != "vllm" || *input.MaxNumSeqs < 1 {
			return model.AIAppPreparation{}, errors.New("max_num_seqs requires vLLM and a positive integer")
		}
		args = []string{"--max-num-seqs", strconv.Itoa(*input.MaxNumSeqs)}
	}
	targets := []model.AIAppNodeTarget{{Kind: "node", NsID: node[0], InfraID: node[1], NodeID: node[2]}}
	return model.AIAppPreparation{
		CorrelationID: flow.CorrelationID,
		Revision:      revision.Revision,
		Deployments: []model.AIAppNodeDeployment{
			{Type: "inference", Targets: targets, PlacementReview: input.PlacementReview, Inference: &model.AIAppInference{
				ModelID: input.ModelID, ArtifactID: input.ArtifactID, RuntimeID: runtime, GPU: accelerator.Count > 0, Args: args,
			}},
			{Type: "app", Targets: targets, PlacementReview: input.PlacementReview, App: &model.AIAppApplication{Name: spec.Application.AppID, Version: version}},
		},
	}, nil
}

func parseAIAppMaxNumSeqs(inference *model.AIAppInference) (*int, error) {
	args := inference.Args
	if len(args) == 0 {
		return nil, nil
	}
	if inference.RuntimeID != "vllm" || len(args) != 2 || args[0] != "--max-num-seqs" {
		return nil, errors.New("approved runtime arguments support --max-num-seqs on vLLM")
	}
	value, err := strconv.Atoi(args[1])
	if err != nil || value < 1 {
		return nil, errors.New("--max-num-seqs requires a positive integer")
	}
	return &value, nil
}

func validAIAppPlacementReview(review *model.AIAppPlacementReview, nsID string) bool {
	return review == nil || (review.NsID == nsID && review.SpecID != "" && review.ImageID != "" &&
		(review.Status == "Ready" || review.Status == "Warning") && review.SpecStatus != "" && review.ImageStatus != "" &&
		!review.ReviewedAt.IsZero())
}
