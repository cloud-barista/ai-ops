// Package api exposes the focused resource discovery and recommendation API.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/gin-gonic/gin"

	"ai-mcmp/internal/config"
	"ai-mcmp/internal/recommendation"
	"ai-mcmp/internal/resource"
	promsource "ai-mcmp/internal/resource/prometheus"
)

const maxRecommendationRequestBytes = 1 << 20

type recommender interface {
	Recommend(context.Context, resource.ResourceQuery) (resource.ResourceRecommendation, error)
	SourceName() string
}

type handler struct {
	recommender recommender
}

// NewRouter builds the production router with the temporary Prometheus source.
func NewRouter() (*gin.Engine, error) {
	source, err := promsource.New(promsource.DefaultConfig())
	if err != nil {
		return nil, err
	}
	return NewRouterWithSource(source)
}

// NewRouterWithSource injects a ResourceSource for tests or future delivery
// adapters without changing HTTP, normalization, filtering, or ranking code.
func NewRouterWithSource(source resource.Source) (*gin.Engine, error) {
	service, err := recommendation.New(source)
	if err != nil {
		return nil, err
	}
	handler := handler{recommender: service}
	router := gin.New()
	router.Use(gin.Recovery())
	if err := router.SetTrustedProxies(nil); err != nil {
		return nil, err
	}
	router.GET("/health", handler.health)
	router.POST("/api/v1/resource-recommendations", handler.recommendFromProfile)
	// ResourceQuery was the prototype HTTP input. Keep it explicitly isolated
	// for old clients while the focused path accepts llmop's ApplicationProfile.
	router.POST("/legacy/api/v1/resource-recommendations", handler.recommendFromQuery)
	return router, nil
}

func (handler handler) health(requestContext *gin.Context) {
	requestContext.JSON(http.StatusOK, gin.H{
		"status":          "ok",
		"version":         config.Version,
		"schema_version":  resource.SchemaVersion,
		"role":            "resource_discovery_normalization_filtering_ranking_recommendation",
		"resource_source": handler.recommender.SourceName(),
		"capabilities": []string{
			"application-profile-projection",
			"resource-discovery",
			"snapshot-normalization",
			"hard-constraint-filtering",
			"candidate-ranking",
			"resource-recommendation",
		},
	})
}

func (handler handler) recommendFromProfile(requestContext *gin.Context) {
	var request resource.ProfileRecommendationRequest
	if err := decodeJSONRequest(requestContext, &request); err != nil {
		writeError(requestContext, http.StatusBadRequest, "MALFORMED_JSON", "Request body must be one valid JSON object with only supported fields.", nil)
		return
	}
	query, err := resource.QueryFromApplicationProfile(request)
	if err != nil {
		writeError(requestContext, http.StatusUnprocessableEntity, "INVALID_APPLICATION_PROFILE", err.Error(), nil)
		return
	}
	handler.writeRecommendation(requestContext, query)
}

func (handler handler) recommendFromQuery(requestContext *gin.Context) {
	var query resource.ResourceQuery
	if err := decodeJSONRequest(requestContext, &query); err != nil {
		writeError(requestContext, http.StatusBadRequest, "MALFORMED_JSON", "Request body must be one valid JSON object with only supported fields.", nil)
		return
	}
	handler.writeRecommendation(requestContext, query)
}

func decodeJSONRequest(requestContext *gin.Context, destination any) error {
	requestContext.Request.Body = http.MaxBytesReader(requestContext.Writer, requestContext.Request.Body, maxRecommendationRequestBytes)
	decoder := json.NewDecoder(requestContext.Request.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return err
	}
	return nil
}

func (handler handler) writeRecommendation(requestContext *gin.Context, query resource.ResourceQuery) {
	result, err := handler.recommender.Recommend(requestContext.Request.Context(), query)
	if err == nil {
		requestContext.JSON(http.StatusOK, result)
		return
	}
	if errors.Is(err, recommendation.ErrInvalidQuery) {
		writeError(requestContext, http.StatusUnprocessableEntity, "INVALID_RESOURCE_QUERY", err.Error(), nil)
		return
	}
	var sourceError *recommendation.SourceUnavailableError
	if errors.As(err, &sourceError) {
		writeError(requestContext, http.StatusBadGateway, "RESOURCE_SOURCE_UNAVAILABLE", "The configured resource source could not provide a snapshot.", nil)
		return
	}
	writeError(requestContext, http.StatusInternalServerError, "INTERNAL_ERROR", "Resource recommendation failed.", nil)
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return errors.New("request body must contain exactly one JSON value")
		}
		return err
	}
	return nil
}

type errorDetail struct {
	Code    string   `json:"code"`
	Message string   `json:"message"`
	Details []string `json:"details,omitempty"`
}

func writeError(requestContext *gin.Context, status int, code, message string, details []string) {
	requestContext.JSON(status, gin.H{
		"schema_version": resource.SchemaVersion,
		"error": errorDetail{
			Code:    code,
			Message: message,
			Details: details,
		},
	})
}
