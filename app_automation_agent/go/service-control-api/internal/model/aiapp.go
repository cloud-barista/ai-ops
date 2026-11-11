package model

import "time"

type AIAppPreparationRequest struct {
	ModelID         string                `json:"model_id" validate:"required" example:"ministral-3-8b"`
	ArtifactID      string                `json:"artifact_id" validate:"required" example:"hf-fp8"`
	MaxNumSeqs      *int                  `json:"max_num_seqs,omitempty" minimum:"1" example:"16"`
	PlacementReview *AIAppPlacementReview `json:"placement_review,omitempty"`
}

type AIAppPreparation struct {
	CorrelationID string                `json:"correlation_id"`
	Revision      int                   `json:"revision"`
	Deployments   []AIAppNodeDeployment `json:"deployments"`
}

// AIAppNodeDeployment is the subset of AI-App's POST /aiapp/deployments body
// that can be prepared before an inference endpoint and its credentials exist.
type AIAppNodeDeployment struct {
	Type            string                `json:"type" enums:"inference,app"`
	Targets         []AIAppNodeTarget     `json:"targets"`
	Inference       *AIAppInference       `json:"inference,omitempty"`
	App             *AIAppApplication     `json:"app,omitempty"`
	PlacementReview *AIAppPlacementReview `json:"placementReview,omitempty"`
}

type AIAppPlacementReview struct {
	NsID          string    `json:"nsId" example:"etri"`
	SpecID        string    `json:"specId" example:"aws+ap-northeast-2+g4dn.xlarge"`
	ImageID       string    `json:"imageId" example:"aws+ap-northeast-2+ami-example"`
	Status        string    `json:"status" enums:"Ready,Warning"`
	SpecStatus    string    `json:"specStatus" example:"Available"`
	ImageStatus   string    `json:"imageStatus" example:"Available"`
	EstimatedCost string    `json:"estimatedCost,omitempty" example:"$0.50/hour"`
	ReviewedAt    time.Time `json:"reviewedAt"`
}

type AIAppNodeTarget struct {
	Kind    string `json:"kind" example:"node"`
	NsID    string `json:"nsId" example:"etri"`
	InfraID string `json:"infraId" example:"travel"`
	NodeID  string `json:"nodeId" example:"vm1"`
}

type AIAppInference struct {
	ModelID    string   `json:"modelId" example:"ministral-3-8b"`
	ArtifactID string   `json:"artifactId" example:"hf-fp8"`
	RuntimeID  string   `json:"runtimeId" example:"vllm"`
	GPU        bool     `json:"gpu"`
	Args       []string `json:"args,omitempty"`
}

type AIAppApplication struct {
	Name             string                 `json:"name" example:"langflow"`
	Version          int                    `json:"version" example:"1"`
	InferenceBinding *AIAppInferenceBinding `json:"inferenceBinding,omitempty"`
	EnvFrom          map[string]string      `json:"envFrom,omitempty"`
}

type AIAppInferenceBinding struct {
	BaseURLEnv string `json:"baseUrlEnv" example:"MODEL_BASE_URL"`
	APIKeyEnv  string `json:"apiKeyEnv,omitempty" example:"MODEL_API_KEY"`
	Native     bool   `json:"native,omitempty"`
}

type AIAppStageRequest struct {
	Revision   int                 `json:"revision" example:"1"`
	Deployment AIAppNodeDeployment `json:"deployment"`
}

type AIAppStageReceipt struct {
	CorrelationID string    `json:"correlationId"`
	Revision      int       `json:"revision"`
	Namespace     string    `json:"nsId"`
	Target        string    `json:"target"`
	Status        string    `json:"status" enums:"blocked,failed,confirmation_pending"`
	EngineRunID   string    `json:"engineRunId,omitempty"`
	DeploymentID  string    `json:"deploymentId,omitempty"`
	UpdatedAt     time.Time `json:"updatedAt"`
}

type AIAppApplyRequest struct {
	Revision         int                 `json:"revision" example:"1"`
	PreviousRevision int                 `json:"previousRevision" example:"0"`
	Inference        AIAppNodeDeployment `json:"inference"`
	Application      AIAppNodeDeployment `json:"application"`
}

type AIAppApplyStep struct {
	Status       string `json:"status" enums:"waiting,succeeded,failed,confirmation_pending"`
	EngineRunID  string `json:"engineRunId,omitempty"`
	DeploymentID string `json:"deploymentId,omitempty"`
}

type AIAppApplyReceipt struct {
	CorrelationID    string         `json:"correlationId"`
	Revision         int            `json:"revision"`
	PreviousRevision int            `json:"previousRevision"`
	Namespace        string         `json:"nsId"`
	Target           string         `json:"target"`
	Status           string         `json:"status" enums:"queued,succeeded,failed,confirmation_pending"`
	Inference        AIAppApplyStep `json:"inference"`
	Application      AIAppApplyStep `json:"application"`
	UpdatedAt        time.Time      `json:"updatedAt"`
}
