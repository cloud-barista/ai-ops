package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func deliveryFixture(t *testing.T, uncertain bool) (Service, *atomic.Int32) {
	return deliveryFixtureWithSourceTime(t, uncertain, nil)
}

func deliveryFixtureWithSourceTime(t *testing.T, uncertain bool, sourceTime *string) (Service, *atomic.Int32) {
	service, calls, _ := deliveryFixtureWithSourceControl(t, uncertain, sourceTime)
	return service, calls
}

type deliverySourceControl struct {
	status    atomic.Value
	updatedAt atomic.Value
}

func deliveryFixtureWithSourceControl(t *testing.T, uncertain bool, sourceTime *string) (Service, *atomic.Int32, *deliverySourceControl) {
	t.Helper()
	calls := &atomic.Int32{}
	source := &deliverySourceControl{}
	source.status.Store("RUNNING")
	if sourceTime == nil {
		source.updatedAt.Store("")
	} else {
		source.updatedAt.Store(*sourceTime)
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.Method + " " + r.URL.Path {
		case "GET /api/v1/apps":
			_, _ = w.Write([]byte(`{"apps":[{"app_id":"registered-id","app_version_id":"appver-real","name":"chat-service","version":"1.0.0","app_spec":{"runtime":{"type":"gpu"}}}]}`))
		case "POST /api/v1/deployments":
			calls.Add(1)
			var body appdeploy.DeploymentCreateRequest
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body.Manifest.Spec.AppVersionID != "appver-real" || body.Manifest.Spec.Resources.Memory != "8192Mi" || body.Manifest.Spec.TargetProfileID != "" {
				t.Errorf("wrong projection: %+v", body)
			}
			if uncertain {
				w.WriteHeader(503)
				_, _ = w.Write([]byte(`{"error":{"message":"upstream internal detail"}}`))
				return
			}
			if sourceTime == nil {
				source.updatedAt.Store(time.Now().UTC().Format(time.RFC3339Nano))
			}
			_, _ = w.Write([]byte(`{"deployment_id":"dep-real","app_version_id":"appver-real","target_profile_id":"registered-vm","status":"RUNNING"}`))
		case "GET /api/v1/deployments/dep-real":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"deployment_id":     "dep-real",
				"app_version_id":    "appver-real",
				"target_profile_id": "registered-vm",
				"status":            source.status.Load().(string),
				"updated_at":        source.updatedAt.Load().(string),
			})
		case "GET /api/v1/deployments/dep-real/metrics":
			_, _ = w.Write([]byte(`{"items":[]}`))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(upstream.Close)
	config := NewServerConfig()
	config.AppDeployBaseURL = upstream.URL + "/api/v1"
	config.AppDeploySubmitEnabled = true
	config.AppDeployDeliveryDir = t.TempDir()
	s := NewService(config)
	flow, err := s.agentControl.ReceiveExternalInputs(context.Background(), apiApplicationContextEnvelope(), apiResourceRecommendationEnvelope(), "")
	if err != nil || flow.State != agentcontrol.StateDecisionApproved {
		t.Fatalf("external flow: %+v %v", flow, err)
	}
	return s, calls, source
}

func TestExternalFlowDeliveryAndStatus(t *testing.T) {
	s, calls := deliveryFixture(t, false)
	ctx := context.Background()
	req := FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}
	prepared, err := s.deliverFlow(ctx, "flow-api-001", req, false)
	if err != nil || prepared.Status != "PREPARED" || calls.Load() != 0 {
		t.Fatalf("prepare: %+v %v", prepared, err)
	}
	approved, ok := s.agentControl.GetFlow("flow-api-001")
	if !ok || len(approved.ManifestRevisions) != 1 {
		t.Fatal("approved initial revision is missing")
	}
	approvedRequest := approved.ManifestRevisions[0].DeploymentRequest
	if prepared.RequestID != approvedRequest.Data.DeploymentRequest.RequestID ||
		prepared.RequestMessageID != approvedRequest.MessageID ||
		prepared.RequestOccurredAt != approvedRequest.OccurredAt ||
		prepared.ReservedAt != "" || prepared.ReceiptReceivedAt != "" {
		t.Fatalf("prepared request identity/times: %+v", prepared)
	}
	var group sync.WaitGroup
	for i := 0; i < 5; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			if _, err := s.deliverFlow(ctx, "flow-api-001", req, true); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if calls.Load() != 1 {
		t.Fatalf("duplicate POST count: %d", calls.Load())
	}
	receipt, err := s.readDelivery("flow-api-001")
	if err != nil || receipt.Status != "SUBMITTED" || receipt.RequestID != prepared.RequestID ||
		receipt.RequestMessageID != prepared.RequestMessageID || receipt.RequestOccurredAt != prepared.RequestOccurredAt ||
		receipt.ReservedAt == "" || receipt.ReceiptReceivedAt == "" {
		t.Fatalf("durable receipt lost the approved action identity: %+v %v", receipt, err)
	}
	reservedAt, err := time.Parse(time.RFC3339, receipt.ReservedAt)
	if err != nil {
		t.Fatal(err)
	}
	receivedAt, err := time.Parse(time.RFC3339, receipt.ReceiptReceivedAt)
	if err != nil || receivedAt.Before(reservedAt) {
		t.Fatalf("receipt time predates reservation: %+v %v", receipt, err)
	}
	refreshed, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || refreshed.Flow == nil || refreshed.Flow.DeploymentStatus.Data.DeploymentStatus.DeploymentID != "dep-real" || refreshed.Flow.CorrelationID != "flow-api-001" {
		t.Fatalf("refresh: %+v %v", refreshed, err)
	}
	if refreshed.Flow.OptimizationFeedback != nil {
		t.Fatal("raw metrics must not fabricate p95 Feedback")
	}
	persisted, err := s.readDelivery("flow-api-001")
	if err != nil || persisted.RequestID != prepared.RequestID || len(persisted.StatusObservations) != 1 ||
		persisted.RequestMessageID != prepared.RequestMessageID || persisted.RequestOccurredAt != prepared.RequestOccurredAt ||
		persisted.ReservedAt != receipt.ReservedAt || persisted.ReceiptReceivedAt != receipt.ReceiptReceivedAt ||
		persisted.Flow != nil || persisted.Metrics != nil {
		t.Fatalf("refresh did not persist a compact status observation: %+v %v", persisted, err)
	}
	restarted := NewService(s.config)
	if _, err := restarted.deliverFlow(ctx, "flow-api-001", req, true); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 1 {
		t.Fatal("restart resubmitted a completed delivery")
	}
	refreshedAfterRestart, err := restarted.refreshDelivery(ctx, "flow-api-001")
	if err != nil || len(refreshedAfterRestart.StatusObservations) != 1 || refreshedAfterRestart.Flow != nil {
		t.Fatalf("restart lost status history or fabricated a Flow: %+v %v", refreshedAfterRestart, err)
	}
}

func TestDelayedDeliveryRefreshPreservesSourceStatusTime(t *testing.T) {
	s, _ := deliveryFixture(t, false)
	ctx := context.Background()
	if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err != nil {
		t.Fatal(err)
	}
	refreshed, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || refreshed.Flow == nil || refreshed.Flow.DeploymentStatus == nil || refreshed.Deployment == nil {
		t.Fatalf("refresh: %+v %v", refreshed, err)
	}
	sourceUpdatedAt := refreshed.Deployment.UpdatedAt
	if sourceUpdatedAt == "" {
		t.Fatal("AppDeployer fixture did not provide a source status time")
	}
	status := refreshed.Flow.DeploymentStatus
	if status.Data.DeploymentStatus.UpdatedAt != sourceUpdatedAt || status.OccurredAt != sourceUpdatedAt || refreshed.Deployment.UpdatedAt != sourceUpdatedAt {
		t.Fatalf("source status time was replaced by refresh time: %+v", status)
	}
	fetchedAt, err := time.Parse(time.RFC3339, refreshed.UpdatedAt)
	if err != nil {
		t.Fatalf("invalid local fetch time: %v", err)
	}
	sourceTime, err := time.Parse(time.RFC3339, sourceUpdatedAt)
	if err != nil {
		t.Fatal(err)
	}
	if !fetchedAt.After(sourceTime) {
		t.Fatalf("local fetch time %s must follow source status time %s", refreshed.UpdatedAt, sourceUpdatedAt)
	}
	requestAt, err := time.Parse(time.RFC3339, refreshed.Flow.ManifestRevisions[0].DeploymentRequest.OccurredAt)
	if err != nil || sourceTime.Before(requestAt) {
		t.Fatalf("source status time %s predates request %s: %v", sourceUpdatedAt, refreshed.Flow.ManifestRevisions[0].DeploymentRequest.OccurredAt, err)
	}
}

func TestDeliveryRefreshRejectsStatusOlderThanInitialRequest(t *testing.T) {
	staleTime := "2024-01-01T00:00:00Z"
	s, _ := deliveryFixtureWithSourceTime(t, false, &staleTime)
	ctx := context.Background()
	if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err != nil {
		t.Fatal(err)
	}
	if _, err := s.refreshDelivery(ctx, "flow-api-001"); err == nil {
		t.Fatal("accepted an AppDeployer status older than the approved initial request")
	}
	flow, ok := s.agentControl.GetFlow("flow-api-001")
	if !ok || flow.DeploymentStatus != nil {
		t.Fatalf("stale upstream status changed the Flow: %+v", flow.DeploymentStatus)
	}
}

func TestDeliveryRefreshRejectsSourceTimeBeforeSubmissionReservation(t *testing.T) {
	s, _, source := deliveryFixtureWithSourceControl(t, false, nil)
	ctx := context.Background()
	receipt, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true)
	if err != nil {
		t.Fatal(err)
	}
	requestAt, err := time.Parse(time.RFC3339, receipt.RequestOccurredAt)
	if err != nil {
		t.Fatal(err)
	}
	reservedAt, err := time.Parse(time.RFC3339, receipt.ReservedAt)
	if err != nil || !reservedAt.After(requestAt) {
		t.Fatalf("expected reservation after approval: %+v %v", receipt, err)
	}
	// This source time is newer than approval but older than the actual POST reservation.
	source.updatedAt.Store(requestAt.Add(reservedAt.Sub(requestAt) / 2).Format(time.RFC3339Nano))
	restarted := NewService(s.config)
	if _, err := restarted.refreshDelivery(ctx, "flow-api-001"); err == nil || !strings.Contains(err.Error(), "reservation") {
		t.Fatalf("accepted pre-submission source status after restart: %v", err)
	}
	stored, err := restarted.readDelivery("flow-api-001")
	if err != nil || len(stored.StatusObservations) != 0 {
		t.Fatalf("pre-submission source status altered the journal: %+v %v", stored.StatusObservations, err)
	}
}

func TestDeliveryRefreshMarksLocalObservationWhenSourceTimeMissing(t *testing.T) {
	missingTime := ""
	s, _ := deliveryFixtureWithSourceTime(t, false, &missingTime)
	ctx := context.Background()
	if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err != nil {
		t.Fatal(err)
	}
	refreshed, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || refreshed.Flow == nil || refreshed.Flow.DeploymentStatus == nil {
		t.Fatalf("refresh: %+v %v", refreshed, err)
	}
	status := refreshed.Flow.DeploymentStatus
	if status.OccurredAt != refreshed.UpdatedAt || status.Data.DeploymentStatus.UpdatedAt != refreshed.UpdatedAt ||
		!strings.Contains(status.Data.DeploymentStatus.Message, "using local observation time") {
		t.Fatalf("missing source time was not marked as a local observation: %+v", status)
	}
	if len(refreshed.StatusObservations) != 1 ||
		refreshed.StatusObservations[0].SourceUpdatedAt != "" ||
		refreshed.StatusObservations[0].FetchedAt != refreshed.UpdatedAt ||
		refreshed.StatusObservations[0].EffectiveUpdatedAt != status.OccurredAt ||
		refreshed.StatusObservations[0].TimeBasis != deliveryTimeLocal ||
		refreshed.StatusObservations[0].FallbackReason != "source_updated_at_missing" {
		t.Fatalf("missing-time provenance was not preserved: %+v", refreshed.StatusObservations)
	}
}

func TestDeliveryRefreshPersistsOrderedStatusHistoryAndRejectsSourceRegression(t *testing.T) {
	s, calls, source := deliveryFixtureWithSourceControl(t, false, nil)
	ctx := context.Background()
	request := FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}
	if _, err := s.deliverFlow(ctx, "flow-api-001", request, true); err != nil {
		t.Fatal(err)
	}
	first, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || len(first.StatusObservations) != 1 {
		t.Fatalf("first status: %+v %v", first.StatusObservations, err)
	}
	duplicate, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || len(duplicate.StatusObservations) != 1 ||
		duplicate.StatusObservations[0].ObservationID != first.StatusObservations[0].ObservationID ||
		duplicate.StatusObservations[0].FetchedAt != first.StatusObservations[0].FetchedAt {
		t.Fatalf("duplicate refresh appended a status event: %+v %v", duplicate.StatusObservations, err)
	}
	source.status.Store("STOPPING")
	source.updatedAt.Store(time.Now().UTC().Format(time.RFC3339Nano))
	second, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || len(second.StatusObservations) != 2 ||
		second.StatusObservations[1].Status != "STOPPING" ||
		second.StatusObservations[1].TimeBasis != deliveryTimeSource {
		t.Fatalf("second status was not persisted in order: %+v %v", second.StatusObservations, err)
	}
	restarted := NewService(s.config)
	stored, err := restarted.readDelivery("flow-api-001")
	if err != nil || len(stored.StatusObservations) != 2 || stored.Flow != nil || stored.Metrics != nil {
		t.Fatalf("restart lost compact status history: %+v %v", stored, err)
	}
	source.updatedAt.Store(first.StatusObservations[0].SourceUpdatedAt)
	if _, err := restarted.refreshDelivery(ctx, "flow-api-001"); err == nil {
		t.Fatal("accepted an older source status after restart")
	}
	stillStored, err := restarted.readDelivery("flow-api-001")
	if err != nil || len(stillStored.StatusObservations) != 2 || calls.Load() != 1 {
		t.Fatalf("regression changed journal or retried POST: %+v %v calls=%d", stillStored, err, calls.Load())
	}
}

func TestDeliveryRefreshPersistsRawObservationBeforeFlowAttachment(t *testing.T) {
	s, _, source := deliveryFixtureWithSourceControl(t, false, nil)
	ctx := context.Background()
	if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err != nil {
		t.Fatal(err)
	}
	first, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || first.Flow == nil || first.Flow.DeploymentStatus == nil || len(first.StatusObservations) != 1 {
		t.Fatalf("first refresh: %+v %v", first, err)
	}
	firstTime, err := time.Parse(time.RFC3339, first.StatusObservations[0].SourceUpdatedAt)
	if err != nil {
		t.Fatal(err)
	}
	source.updatedAt.Store(firstTime.Add(time.Nanosecond).Format(time.RFC3339Nano))
	manual := *first.Flow.DeploymentStatus
	manual.MessageID = "msg-manual-newer-status"
	manual.Data.DeploymentStatus.State = agentcontrol.DeploymentStateStopping
	manual.OccurredAt = time.Now().UTC().Format(time.RFC3339Nano)
	manual.Data.DeploymentStatus.UpdatedAt = manual.OccurredAt
	if _, err := s.agentControl.ReceiveDeploymentStatus(ctx, manual); err != nil {
		t.Fatal(err)
	}
	if _, err := s.refreshDelivery(ctx, "flow-api-001"); err == nil {
		t.Fatal("accepted a source status older than the in-memory Flow status")
	}
	stored, err := s.readDelivery("flow-api-001")
	if err != nil || len(stored.StatusObservations) != 2 || stored.StatusObservations[1].Status != "RUNNING" || stored.Flow != nil {
		t.Fatalf("raw source observation was not durable or was marked as attached: %+v %v", stored, err)
	}
	current, ok := s.agentControl.GetFlow("flow-api-001")
	if !ok || current.DeploymentStatus.MessageID != manual.MessageID {
		t.Fatalf("rejected status replaced the Flow: %+v", current.DeploymentStatus)
	}
	if _, err := s.refreshDelivery(ctx, "flow-api-001"); err == nil {
		t.Fatal("duplicate raw observation masked a failed Flow attachment")
	}
}

func TestDeliveryRefreshDoesNotChangeFlowWhenJournalWriteFails(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("read-only directory mode is not enforced for this test environment")
	}
	s, _ := deliveryFixture(t, false)
	ctx := context.Background()
	if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(s.config.AppDeployDeliveryDir, 0500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(s.config.AppDeployDeliveryDir, 0700) })
	if _, err := s.refreshDelivery(ctx, "flow-api-001"); err == nil {
		t.Fatal("refresh succeeded despite an unwritable journal")
	}
	flow, ok := s.agentControl.GetFlow("flow-api-001")
	if !ok || flow.DeploymentStatus != nil {
		t.Fatalf("Flow changed before the status observation was durable: %+v", flow.DeploymentStatus)
	}
	stored, err := s.readDelivery("flow-api-001")
	if err != nil || len(stored.StatusObservations) != 0 {
		t.Fatalf("failed journal write recorded an observation: %+v %v", stored.StatusObservations, err)
	}
}

func TestDeliveryRefreshKeepsInvalidAndFutureSourceTimestampProvenance(t *testing.T) {
	for _, test := range []struct {
		name   string
		source string
		reason string
	}{
		{name: "invalid", source: "not-a-time", reason: "source_updated_at_invalid"},
		{name: "future", source: time.Now().UTC().Add(time.Hour).Format(time.RFC3339Nano), reason: "source_updated_at_future"},
	} {
		t.Run(test.name, func(t *testing.T) {
			s, _ := deliveryFixtureWithSourceTime(t, false, &test.source)
			ctx := context.Background()
			if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err != nil {
				t.Fatal(err)
			}
			refreshed, err := s.refreshDelivery(ctx, "flow-api-001")
			if err != nil || len(refreshed.StatusObservations) != 1 || refreshed.Flow == nil || refreshed.Flow.DeploymentStatus == nil {
				t.Fatalf("refresh: %+v %v", refreshed, err)
			}
			observation := refreshed.StatusObservations[0]
			if observation.SourceUpdatedAt != test.source || observation.FallbackReason != test.reason ||
				observation.TimeBasis != deliveryTimeLocal || observation.EffectiveUpdatedAt != observation.FetchedAt ||
				refreshed.Flow.DeploymentStatus.OccurredAt != observation.EffectiveUpdatedAt {
				t.Fatalf("fallback provenance was lost: %+v", observation)
			}
			persisted, err := s.readDelivery("flow-api-001")
			if err != nil || len(persisted.StatusObservations) != 1 || persisted.StatusObservations[0] != observation {
				t.Fatalf("fallback provenance was not durable: %+v %v", persisted.StatusObservations, err)
			}
		})
	}
}

func TestOptimizedRevisionRemainsUnissuedInInitialDeliveryJournal(t *testing.T) {
	s, calls := deliveryFixture(t, false)
	ctx := context.Background()
	request := FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}
	if _, err := s.deliverFlow(ctx, "flow-api-001", request, true); err != nil {
		t.Fatal(err)
	}
	refreshed, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || refreshed.Flow == nil || refreshed.Flow.DeploymentStatus == nil {
		t.Fatalf("initial status: %+v %v", refreshed, err)
	}
	status := refreshed.Flow.DeploymentStatus
	feedback := apiOptimizationFeedbackEnvelope()
	feedback.CausationID = status.MessageID
	feedback.Data.OptimizationFeedback.DecisionID = refreshed.DecisionID
	feedback.Data.OptimizationFeedback.DeploymentID = status.Data.DeploymentStatus.DeploymentID
	feedback.Data.OptimizationFeedback.SLOViolations = []string{"latency_p95_ms"}
	feedback.Data.OptimizationFeedback.ObservationWindow.StartedAt = status.Data.DeploymentStatus.UpdatedAt
	endedAt := time.Now().UTC().Format(time.RFC3339Nano)
	feedback.Data.OptimizationFeedback.ObservationWindow.EndedAt = endedAt
	feedback.Data.OptimizationFeedback.CreatedAt = endedAt
	feedback.OccurredAt = endedAt
	optimized, err := s.agentControl.ReceiveOptimizationFeedback(ctx, feedback)
	if err != nil || len(optimized.ManifestRevisions) != 2 || optimized.ScalingDecision == nil || optimized.ScalingDecision.Action != agentcontrol.ScalingActionScaleOut {
		t.Fatalf("expected a guarded scale plan: %+v %v", optimized.ScalingDecision, err)
	}
	if optimized.ManifestRevisions[1].DeploymentRequest.Data.DeploymentRequest.RequestID == refreshed.RequestID {
		t.Fatal("optimized revision reused the initial request ID")
	}
	initial, err := s.deliverFlow(ctx, "flow-api-001", request, true)
	if err != nil || initial.Revision != 1 || initial.RequestID != refreshed.RequestID || calls.Load() != 1 {
		t.Fatalf("optimized revision was presented as delivered: %+v %v posts=%d", initial, err, calls.Load())
	}
	stored, err := s.readDelivery("flow-api-001")
	if err != nil || stored.Revision != 1 || stored.RequestID != refreshed.RequestID || calls.Load() != 1 {
		t.Fatalf("journal contains an unissued scale action: %+v %v posts=%d", stored, err, calls.Load())
	}
}

func TestDeliveryUnknownNeverRetries(t *testing.T) {
	s, calls := deliveryFixture(t, true)
	req := FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}
	if _, err := s.deliverFlow(context.Background(), "flow-api-001", req, true); err == nil {
		t.Fatal("expected uncertain response")
	}
	restarted := NewService(s.config)
	record, err := restarted.deliverFlow(context.Background(), "flow-api-001", req, true)
	if err != nil || record.Status != "SUBMISSION_UNKNOWN" || calls.Load() != 1 {
		t.Fatalf("unsafe retry: %+v %v calls=%d", record, err, calls.Load())
	}
	if record.RequestID == "" || record.RequestMessageID == "" || record.ReservedAt == "" ||
		record.ReceiptReceivedAt != "" || record.Deployment != nil {
		t.Fatalf("unknown attempt was recorded as an acknowledgement: %+v", record)
	}
}

func TestDeliveryRejectsBindingAndDisabledExecution(t *testing.T) {
	s, calls := deliveryFixture(t, false)
	if _, err := s.deliverFlow(context.Background(), "flow-api-001", FlowDeliveryRequest{AppVersionID: "invented", AcceptProjectionLimits: true}, true); err == nil {
		t.Fatal("accepted invented app")
	}
	s.config.AppDeploySubmitEnabled = false
	if _, err := s.deliverFlow(context.Background(), "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err == nil {
		t.Fatal("executed while disabled")
	}
	if calls.Load() != 0 {
		t.Fatal("unexpected upstream POST")
	}
}

func TestExternalPairAtomicAndConflict(t *testing.T) {
	s := NewService(NewServerConfig())
	app, rec := apiApplicationContextEnvelope(), apiResourceRecommendationEnvelope()
	rec.TraceID = "different"
	if _, err := s.agentControl.ReceiveExternalInputs(context.Background(), app, rec, ""); err == nil {
		t.Fatal("accepted mismatched trace")
	}
	if len(s.agentControl.ListFlows()) != 0 {
		t.Fatal("partially stored invalid pair")
	}
	rec.TraceID = app.TraceID
	if _, err := s.agentControl.ReceiveExternalInputs(context.Background(), app, rec, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := s.agentControl.ReceiveExternalInputs(context.Background(), app, rec, ""); err != nil {
		t.Fatalf("identical replay was rejected: %v", err)
	}
	app.Data.ApplicationProfile.Requirements.Compute.CPUCoresMin = 100
	if _, err := s.agentControl.ReceiveExternalInputs(context.Background(), app, rec, ""); err == nil {
		t.Fatal("replaced existing identity")
	}
}

func TestExternalPairRecordsImmutableInputOrigin(t *testing.T) {
	s := NewService(NewServerConfig())
	app, rec := apiApplicationContextEnvelope(), apiResourceRecommendationEnvelope()
	flow, err := s.agentControl.ReceiveExternalInputsFrom(context.Background(), app, rec, "", "jang-automation-runner")
	if err != nil {
		t.Fatal(err)
	}
	if flow.InputOrigin != "jang-automation-runner" {
		t.Fatalf("input origin was not recorded: %+v", flow)
	}
	if _, err := s.agentControl.ReceiveExternalInputsFrom(context.Background(), app, rec, "", "another-system"); err == nil {
		t.Fatal("replay changed the immutable input origin")
	}
}

func TestDeliveryRequiresAcceptanceAndApprovedAgent(t *testing.T) {
	s, calls := deliveryFixture(t, false)
	ctx := context.Background()
	if _, err := s.deliverFlow(ctx, "flow-api-001", FlowDeliveryRequest{AppVersionID: "appver-real"}, true); err == nil {
		t.Fatal("submitted without explicit projection acceptance")
	}
	s.agentControl.DeleteFlow("flow-api-001")
	flow, err := s.agentControl.ReceiveExternalInputs(ctx, apiApplicationContextEnvelope(), apiResourceRecommendationEnvelope(), "unregistered-agent")
	if err != nil || flow.State == agentcontrol.StateDecisionApproved {
		t.Fatalf("expected unapproved agent flow: %s %v", flow.State, err)
	}
	if _, err := s.deliverFlow(ctx, flow.CorrelationID, FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}, true); err == nil {
		t.Fatal("submitted unapproved agent output")
	}
	if calls.Load() != 0 {
		t.Fatal("unexpected deployment request")
	}
}
