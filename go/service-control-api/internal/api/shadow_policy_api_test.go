package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"
)

func TestCommonResourceLabelsKeepsOnlySharedConditions(t *testing.T) {
	labels := commonResourceLabels([]ResourceOpsRankedResource{
		{Resource: ResourceOpsProfile{Labels: map[string]string{"accelerator": "nvidia", "zone": "a"}}},
		{Resource: ResourceOpsProfile{Labels: map[string]string{"accelerator": "nvidia", "zone": "b"}}},
	})
	want := map[string]string{"accelerator": "nvidia"}
	if !reflect.DeepEqual(labels, want) {
		t.Fatalf("commonResourceLabels() = %#v, want %#v", labels, want)
	}
}

func TestInvokeShadowPolicyHonorsTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		time.Sleep(50 * time.Millisecond)
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte("{\"proposal\":{\"action\":\"KEEP\"}}"))
	}))
	defer server.Close()
	t.Setenv(shadowPolicyTimeoutEnv, "0.001")

	if _, err := invokeShadowPolicy(context.Background(), server.URL, map[string]any{}); err == nil {
		t.Fatal("invokeShadowPolicy() error = nil, want timeout")
	}
}
