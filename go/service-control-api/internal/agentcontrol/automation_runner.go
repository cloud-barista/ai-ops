package agentcontrol

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"sync"
	"time"
)

const (
	AutomationRunStatusCompleted = "COMPLETED"
	AutomationRunStatusFailed    = "FAILED"
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
	ErrorCode              string                     `json:"error_code,omitempty"`
	ErrorMessage           string                     `json:"error_message,omitempty"`
	CreatedAt              string                     `json:"created_at"`
	UpdatedAt              string                     `json:"updated_at"`
}

type AutomationRunner struct {
	mu           sync.RWMutex
	runs         map[string]AutomationRun
	analyzer     RequirementAnalyzer
	recommender  ResourceRecommender
	agentControl *Service
	Now          func() time.Time
	IDGenerator  func(string) string
}

func NewAutomationRunner(
	analyzer RequirementAnalyzer,
	recommender ResourceRecommender,
	agentControl *Service,
) *AutomationRunner {
	return &AutomationRunner{
		runs:         map[string]AutomationRun{},
		analyzer:     analyzer,
		recommender:  recommender,
		agentControl: agentControl,
		Now:          func() time.Time { return time.Now().UTC() },
		IDGenerator:  randomAutomationID,
	}
}

func (runner *AutomationRunner) Run(
	ctx context.Context,
	input AutomationRunInput,
) (AutomationRun, error) {
	if err := ctx.Err(); err != nil {
		return AutomationRun{}, err
	}
	if runner.analyzer == nil || runner.recommender == nil || runner.agentControl == nil {
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
	run := AutomationRun{
		RunID:         idGenerator("run"),
		CorrelationID: idGenerator("flow"),
		TraceID:       idGenerator("trace"),
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
	run.Status = AutomationRunStatusCompleted
	run.Flow = &flow
	run.DesiredDeploymentSpec = flow.DesiredDeploymentSpec
	run.UpdatedAt = now().UTC().Format(time.RFC3339Nano)
	runner.store(run)
	return run, nil
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
