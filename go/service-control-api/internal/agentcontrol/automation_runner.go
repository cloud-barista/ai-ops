package agentcontrol

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

const (
	AutomationRunStatusCompleted = "COMPLETED"
	AutomationRunStatusFailed    = "FAILED"
)

var ErrAnalysisRequestIdempotencyConflict = errors.New(
	"application analysis request message_id already exists with a different payload",
)

type AutomationRun struct {
	RunID                  string                     `json:"run_id"`
	CorrelationID          string                     `json:"correlation_id"`
	TraceID                string                     `json:"trace_id"`
	Status                 string                     `json:"status"`
	Input                  AutomationRunInput         `json:"input"`
	RequirementAnalysis    *RequirementAnalysisResult `json:"requirement_analysis,omitempty"`
	ResourceRecommendation *RecommendationResult      `json:"resource_recommendation,omitempty"`
	Flow                   *Flow                      `json:"flow,omitempty"`
	DesiredDeploymentSpec  *DesiredDeploymentSpec     `json:"desired_deployment_spec,omitempty"`
	DeploymentSubmission   *DeploymentSubmission      `json:"deployment_submission,omitempty"`
	ErrorCode              string                     `json:"error_code,omitempty"`
	ErrorMessage           string                     `json:"error_message,omitempty"`
	CreatedAt              string                     `json:"created_at"`
	UpdatedAt              string                     `json:"updated_at"`
}

type AutomationRunner struct {
	mu                sync.RWMutex
	protocolMu        sync.Mutex
	runs              map[string]AutomationRun
	protocolRequests  map[string]analysisRequestRecord
	analyzer          RequirementAnalyzer
	recommender       ResourceRecommender
	agentControl      *Service
	deploymentAdapter DeploymentAdapter
	Now               func() time.Time
	IDGenerator       func(string) string
}

type analysisRequestRecord struct {
	Fingerprint string
	RunID       string
	Error       string
}

type automationRunIdentity struct {
	CorrelationID string
	TraceID       string
	CausationID   string
}

func NewAutomationRunner(
	analyzer RequirementAnalyzer,
	recommender ResourceRecommender,
	agentControl *Service,
) *AutomationRunner {
	return NewAutomationRunnerWithAdapter(
		analyzer,
		recommender,
		agentControl,
		MockDeploymentAdapter{},
	)
}

func NewAutomationRunnerWithAdapter(
	analyzer RequirementAnalyzer,
	recommender ResourceRecommender,
	agentControl *Service,
	deploymentAdapter DeploymentAdapter,
) *AutomationRunner {
	return &AutomationRunner{
		runs:              map[string]AutomationRun{},
		protocolRequests:  map[string]analysisRequestRecord{},
		analyzer:          analyzer,
		recommender:       recommender,
		agentControl:      agentControl,
		deploymentAdapter: deploymentAdapter,
		Now:               func() time.Time { return time.Now().UTC() },
		IDGenerator:       randomAutomationID,
	}
}

func (runner *AutomationRunner) Run(
	ctx context.Context,
	input AutomationRunInput,
) (AutomationRun, error) {
	return runner.run(ctx, input, automationRunIdentity{})
}

func (runner *AutomationRunner) RunAnalysisRequest(
	ctx context.Context,
	request ApplicationAnalysisRequestEnvelope,
) (AutomationRun, bool, error) {
	if err := validateApplicationAnalysisRequest(request); err != nil {
		return AutomationRun{}, false, err
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return AutomationRun{}, false, fmt.Errorf("encode application analysis request: %w", err)
	}
	fingerprint := string(encoded)

	runner.protocolMu.Lock()
	defer runner.protocolMu.Unlock()

	if record, ok := runner.protocolRequests[request.MessageID]; ok {
		if record.Fingerprint != fingerprint {
			return AutomationRun{}, false, ErrAnalysisRequestIdempotencyConflict
		}
		run, found := runner.Get(record.RunID)
		if !found {
			return AutomationRun{}, false, fmt.Errorf(
				"idempotency record references missing run_id %q",
				record.RunID,
			)
		}
		if record.Error != "" {
			return run, true, errors.New(record.Error)
		}
		return run, true, nil
	}

	run, runErr := runner.run(
		ctx,
		automationInputFromAnalysisRequest(request),
		automationRunIdentity{
			CorrelationID: request.CorrelationID,
			TraceID:       request.TraceID,
			CausationID:   request.MessageID,
		},
	)
	record := analysisRequestRecord{Fingerprint: fingerprint, RunID: run.RunID}
	if runErr != nil {
		record.Error = runErr.Error()
	}
	runner.protocolRequests[request.MessageID] = record
	return run, false, runErr
}

func (runner *AutomationRunner) run(
	ctx context.Context,
	input AutomationRunInput,
	identity automationRunIdentity,
) (AutomationRun, error) {
	if err := ctx.Err(); err != nil {
		return AutomationRun{}, err
	}
	if runner.analyzer == nil ||
		runner.recommender == nil ||
		runner.agentControl == nil ||
		runner.deploymentAdapter == nil {
		return AutomationRun{}, fmt.Errorf("automation runner dependencies are not configured")
	}
	now := runner.Now
	if now == nil {
		now = func() time.Time { return time.Now().UTC() }
	}
	idGenerator := runner.IDGenerator
	if idGenerator == nil {
		idGenerator = randomAutomationID
	}
	createdAt := now().UTC()
	correlationID := strings.TrimSpace(identity.CorrelationID)
	if correlationID == "" {
		correlationID = idGenerator("flow")
	}
	traceID := strings.TrimSpace(identity.TraceID)
	if traceID == "" {
		traceID = idGenerator("trace")
	}
	run := AutomationRun{
		RunID:         idGenerator("run"),
		CorrelationID: correlationID,
		TraceID:       traceID,
		Status:        AutomationRunStatusFailed,
		Input:         input,
		CreatedAt:     createdAt.Format(time.RFC3339Nano),
		UpdatedAt:     createdAt.Format(time.RFC3339Nano),
	}

	analysis, err := runner.analyzer.Analyze(ctx, input)
	if err != nil {
		return runner.failAndStore(run, "REQUIREMENT_ANALYSIS_FAILED", "Requirement analysis failed"), err
	}
	run.RequirementAnalysis = &analysis

	recommendation, err := runner.recommender.Recommend(ctx, analysis.ApplicationProfile)
	if err != nil {
		return runner.failAndStore(run, "RESOURCE_RECOMMENDATION_FAILED", "Resource recommendation failed"), err
	}
	run.ResourceRecommendation = &recommendation

	occurredAt := now().UTC().Format(time.RFC3339Nano)
	applicationMessageID := idGenerator("msg-context")
	applicationContext := ApplicationContextEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       applicationMessageID,
			MessageType:     MessageApplicationContextCreated,
			OccurredAt:      occurredAt,
			CorrelationID:   run.CorrelationID,
			TraceID:         run.TraceID,
			CausationID:     identity.CausationID,
			Source: Endpoint{
				System:    "khu-requirements",
				Component: "requirement-analyzer",
			},
			Target: Endpoint{
				System:    "khu-agent-control",
				Component: "automation-agent",
			},
		},
		Data: ApplicationContextData{
			ApplicationProfile:  analysis.ApplicationProfile,
			ModelRecommendation: analysis.ModelRecommendation,
		},
	}
	if _, err := runner.agentControl.ReceiveApplicationContext(ctx, applicationContext); err != nil {
		return runner.failAndStore(run, "APPLICATION_CONTEXT_REJECTED", "Application Context was rejected"), err
	}

	resourceRecommendation := ResourceRecommendationEnvelope{
		Envelope: Envelope{
			ContractVersion: ContractVersionV1,
			MessageID:       idGenerator("msg-resource"),
			MessageType:     MessageResourceRecommendationCreated,
			OccurredAt:      occurredAt,
			CorrelationID:   run.CorrelationID,
			TraceID:         run.TraceID,
			CausationID:     applicationMessageID,
			Source: Endpoint{
				System:    "khu-resource-service",
				Component: "resource-recommender",
			},
			Target: Endpoint{
				System:    "khu-agent-control",
				Component: "automation-agent",
			},
		},
		Data: ResourceRecommendationData{
			ResourceRecommendation: recommendation.ResourceRecommendation,
		},
	}
	flow, err := runner.agentControl.ReceiveResourceRecommendation(ctx, resourceRecommendation)
	if err != nil {
		return runner.failAndStore(run, "RESOURCE_RECOMMENDATION_REJECTED", "Resource Recommendation was rejected"), err
	}
	run.Flow = &flow
	run.DesiredDeploymentSpec = flow.DesiredDeploymentSpec
	if flow.DeploymentRequest != nil && run.DesiredDeploymentSpec != nil {
		submission, submitErr := runner.deploymentAdapter.Submit(
			ctx,
			*flow.DeploymentRequest,
		)
		if submitErr != nil {
			if strings.TrimSpace(submission.Adapter) == "" {
				submission.Adapter = runner.deploymentAdapter.Name()
			}
			submission.Status = DeploymentSubmissionFailed
			submission.RequestID = flow.DeploymentRequest.Data.DeploymentRequest.RequestID
			submission.SubmittedAt = now().UTC().Format(time.RFC3339Nano)
			submission.ErrorCode = "DEPLOYMENT_ADAPTER_FAILED"
			submission.ErrorMessage = "Deployment adapter could not accept the approved request."
			run.DeploymentSubmission = &submission
			return runner.failAndStore(
				run,
				"DEPLOYMENT_ADAPTER_FAILED",
				"Deployment adapter could not accept the approved request.",
			), submitErr
		}
		run.DeploymentSubmission = &submission
	}
	run.Status = AutomationRunStatusCompleted
	run.UpdatedAt = now().UTC().Format(time.RFC3339Nano)
	runner.store(run)
	return run, nil
}

func automationInputFromAnalysisRequest(
	request ApplicationAnalysisRequestEnvelope,
) AutomationRunInput {
	application := request.Data.Application
	artifact := application.Artifact
	artifact.Entrypoint = append([]string(nil), application.Artifact.Entrypoint...)
	labels := make(map[string]string, len(application.Labels))
	for key, value := range application.Labels {
		labels[key] = value
	}
	requestedBy := strings.TrimSpace(request.Source.System)
	if component := strings.TrimSpace(request.Source.Component); component != "" {
		requestedBy += "/" + component
	}
	return AutomationRunInput{
		InputType:   InputTypeNaturalLanguage,
		Request:     application.UserRequest,
		RequestedBy: requestedBy,
		AppSpec: &StructuredAppSpec{
			AppID:          application.AppID,
			AppVersion:     application.AppVersion,
			Artifact:       &artifact,
			ExpectedRPS:    application.DeclaredSpec.ExpectedRPS,
			MaxInputTokens: application.DeclaredSpec.MaxInputTokens,
			Labels:         labels,
		},
	}
}

func validateApplicationAnalysisRequest(request ApplicationAnalysisRequestEnvelope) error {
	if err := validateEnvelope(request.Envelope, MessageApplicationAnalysisRequest); err != nil {
		return err
	}
	application := request.Data.Application
	switch {
	case strings.TrimSpace(application.AppID) == "":
		return fmt.Errorf("application.app_id is required")
	case strings.TrimSpace(application.AppVersion) == "":
		return fmt.Errorf("application.app_version is required")
	case strings.TrimSpace(application.Artifact.Type) == "":
		return fmt.Errorf("application.artifact.type is required")
	case strings.TrimSpace(application.Artifact.URI) == "":
		return fmt.Errorf("application.artifact.uri is required")
	case strings.TrimSpace(application.UserRequest) == "":
		return fmt.Errorf("application.user_request is required")
	case application.DeclaredSpec.ExpectedRPS < 0:
		return fmt.Errorf("application.declared_spec.expected_rps must not be negative")
	case application.DeclaredSpec.MaxInputTokens < 0:
		return fmt.Errorf("application.declared_spec.max_input_tokens must not be negative")
	}
	return nil
}

func (runner *AutomationRunner) Get(runID string) (AutomationRun, bool) {
	runner.mu.RLock()
	defer runner.mu.RUnlock()
	run, ok := runner.runs[runID]
	return run, ok
}

func (runner *AutomationRunner) List() []AutomationRun {
	runner.mu.RLock()
	defer runner.mu.RUnlock()
	runs := make([]AutomationRun, 0, len(runner.runs))
	for _, run := range runner.runs {
		runs = append(runs, run)
	}
	return runs
}

func (runner *AutomationRunner) failAndStore(
	run AutomationRun,
	code string,
	message string,
) AutomationRun {
	run.Status = AutomationRunStatusFailed
	run.ErrorCode = code
	run.ErrorMessage = message
	if runner.Now != nil {
		run.UpdatedAt = runner.Now().UTC().Format(time.RFC3339Nano)
	}
	runner.store(run)
	return run
}

func (runner *AutomationRunner) store(run AutomationRun) {
	runner.mu.Lock()
	defer runner.mu.Unlock()
	runner.runs[run.RunID] = run
}

func randomAutomationID(prefix string) string {
	buffer := make([]byte, 8)
	if _, err := rand.Read(buffer); err != nil {
		return fmt.Sprintf("%s-%d", prefix, time.Now().UTC().UnixNano())
	}
	return prefix + "-" + hex.EncodeToString(buffer)
}
