package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"

	"kyunghee-aiops/service-control-api/internal/agentcontrol"
	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

func deliveryFixture(t *testing.T, uncertain bool) (Service, *atomic.Int32) {
	t.Helper()
	calls := &atomic.Int32{}
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
			_, _ = w.Write([]byte(`{"deployment_id":"dep-real","app_version_id":"appver-real","target_profile_id":"registered-vm","status":"RUNNING"}`))
		case "GET /api/v1/deployments/dep-real":
			_, _ = w.Write([]byte(`{"deployment_id":"dep-real","app_version_id":"appver-real","target_profile_id":"registered-vm","status":"RUNNING"}`))
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
	return s, calls
}

func TestExternalFlowDeliveryAndStatus(t *testing.T) {
	s, calls := deliveryFixture(t, false)
	ctx := context.Background()
	req := FlowDeliveryRequest{AppVersionID: "appver-real", AcceptProjectionLimits: true}
	prepared, err := s.deliverFlow(ctx, "flow-api-001", req, false)
	if err != nil || prepared.Status != "PREPARED" || calls.Load() != 0 {
		t.Fatalf("prepare: %+v %v", prepared, err)
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
	refreshed, err := s.refreshDelivery(ctx, "flow-api-001")
	if err != nil || refreshed.Flow == nil || refreshed.Flow.DeploymentStatus.Data.DeploymentStatus.DeploymentID != "dep-real" || refreshed.Flow.CorrelationID != "flow-api-001" {
		t.Fatalf("refresh: %+v %v", refreshed, err)
	}
	if refreshed.Flow.OptimizationFeedback != nil {
		t.Fatal("raw metrics must not fabricate p95 Feedback")
	}
	restarted := NewService(s.config)
	if _, err := restarted.deliverFlow(ctx, "flow-api-001", req, true); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 1 {
		t.Fatal("restart resubmitted a completed delivery")
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
