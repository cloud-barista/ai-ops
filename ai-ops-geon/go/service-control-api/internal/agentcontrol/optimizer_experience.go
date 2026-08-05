package agentcontrol

import (
	"sort"
	"strings"
)

// QueryExperiences reads the latest deployment result retained in each flow.
// ponytail: flow snapshots keep the PoC small; use an append-only history when retry history must be retained.
func (service *Service) QueryExperiences(query ExperienceQuery) []DeploymentExperience {
	profileID := strings.TrimSpace(query.ProfileID)
	candidateID := strings.TrimSpace(query.CandidateID)
	errorCode := strings.TrimSpace(query.ErrorCode)

	service.mu.RLock()
	defer service.mu.RUnlock()

	experiences := make([]DeploymentExperience, 0, len(service.flows))
	for _, flow := range service.flows {
		experience, ok := deploymentExperience(flow)
		if !ok ||
			(profileID != "" && experience.ProfileID != profileID) ||
			(candidateID != "" && experience.CandidateID != candidateID) ||
			(errorCode != "" && experience.ErrorCode != errorCode) {
			continue
		}
		experiences = append(experiences, experience)
	}
	sort.Slice(experiences, func(left, right int) bool {
		if experiences[left].UpdatedAt != experiences[right].UpdatedAt {
			return experiences[left].UpdatedAt > experiences[right].UpdatedAt
		}
		return experiences[left].CorrelationID < experiences[right].CorrelationID
	})
	if query.Limit > 0 && len(experiences) > query.Limit {
		return experiences[:query.Limit]
	}
	return experiences
}

func deploymentExperience(flow Flow) (DeploymentExperience, bool) {
	if flow.DeploymentStatus == nil && flow.OptimizationFeedback == nil {
		return DeploymentExperience{}, false
	}

	experience := DeploymentExperience{
		CorrelationID: flow.CorrelationID,
		ProfileID:     flow.ProfileID,
		UpdatedAt:     flow.UpdatedAt,
	}
	if flow.Decision != nil {
		experience.DecisionID = flow.Decision.DecisionID
		experience.CandidateID = flow.Decision.SelectedCandidateID
	}
	if flow.DeploymentStatus != nil {
		status := flow.DeploymentStatus.Data.DeploymentStatus
		experience.DeploymentID = status.DeploymentID
		experience.DeploymentState = status.State
		experience.ErrorCode = status.ErrorCode
		experience.Success = status.State == DeploymentStateRunning
		experience.UpdatedAt = status.UpdatedAt
	}
	if flow.OptimizationFeedback != nil {
		feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
		experience.Outcome = feedback.Outcome
		if feedback.ErrorCode != "" {
			experience.ErrorCode = feedback.ErrorCode
		}
		experience.Success = feedback.Outcome == FeedbackOutcomeSucceeded
		experience.SLOViolations = append([]string(nil), feedback.SLOViolations...)
		experience.UpdatedAt = feedback.CreatedAt
	}
	return experience, true
}
