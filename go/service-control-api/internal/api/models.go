package api

import "kyunghee-aiops/service-control-api/internal/model"

type AgentRegistry = model.AgentRegistry
type AgentProfile = model.AgentProfile
type ExternalAgentRegistrationRequest = model.ExternalAgentRegistrationRequest
type AgentInvocationPlanRequest = model.AgentInvocationPlanRequest
type AgentInvocationPlan = model.AgentInvocationPlan
type LLMAutomationActionRequest = model.LLMAutomationActionRequest
type LLMActionProposal = model.LLMActionProposal
type LLMDecisionResult = model.LLMDecisionResult
type GuardDecision = model.GuardDecision
type LLMAutomationActionResponse = model.LLMAutomationActionResponse
type AutomationFeedbackRequest = model.AutomationFeedbackRequest
type AutomationFeedbackRecord = model.AutomationFeedbackRecord
type OpsLLMBenchmark = model.OpsLLMBenchmark
type OpsLLMPolicy = model.OpsLLMPolicy
type OpsLLMCandidate = model.OpsLLMCandidate
type OpsLLMSelectRequest = model.OpsLLMSelectRequest
type ErrorResponse = model.ErrorResponse
type OpsLLMSelectionResponse = model.OpsLLMSelectionResponse
type OpsLLMRankedItem = model.OpsLLMRankedItem
type VMCompatibilityRequest = model.VMCompatibilityRequest
type VMRequirementsConfig = model.VMRequirementsConfig
type VMWorkloadRequirement = model.VMWorkloadRequirement
type VMResourceSnapshot = model.VMResourceSnapshot
type VMPerformanceEvidence = model.VMPerformanceEvidence
type VMCompatibilityCheck = model.VMCompatibilityCheck
type VMCompatibilityResponse = model.VMCompatibilityResponse
type ServiceOperationsRequest = model.ServiceOperationsRequest
type DeploymentPlanResponse = model.DeploymentPlanResponse
type DeploymentPlan = model.DeploymentPlan
type DeploymentValidation = model.DeploymentValidation
type AgentReviews = model.AgentReviews
type AgentReview = model.AgentReview
type OperationReadiness = model.OperationReadiness
type GuardValidation = model.GuardValidation
type ServiceOperationsResponse = model.ServiceOperationsResponse
type AgentControlReasoningComparisonRequest = model.AgentControlReasoningComparisonRequest
type ExternalFlowRequest = model.ExternalFlowRequest
type FlowDeliveryRequest = model.FlowDeliveryRequest
type FlowDelivery = model.FlowDelivery
type TrustedAutomationRunRequest = model.TrustedAutomationRunRequest
type TrustedAutomationRunErrorResponse = model.TrustedAutomationRunErrorResponse
type PackageControlRunErrorResponse = model.PackageControlRunErrorResponse
type ReadinessResponse = model.ReadinessResponse
