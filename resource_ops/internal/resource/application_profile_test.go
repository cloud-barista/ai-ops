package resource

import "testing"

func TestQueryFromApplicationProfileProjectsLookupConditions(t *testing.T) {
	request := ProfileRecommendationRequest{
		SchemaVersion: SchemaVersion,
		RequestID:     "flow-001",
		ApplicationProfile: ApplicationProfile{
			ProfileID:  "profile-001",
			AppID:      "chat-service",
			AppVersion: "1.0.0",
			Requirements: ApplicationRequirements{
				Compute: ComputeRequirements{CPUCoresMin: 8, MemoryMiBMin: 32768, StorageGiBMin: 50},
				Accelerator: AcceleratorRequirements{
					Required: true, Type: "GPU", CountMin: 1, MemoryMiBMinPerDevice: 12000,
				},
				Deployment: DeploymentRequirements{ReplicasMin: 4, ReplicasMax: 6},
			},
		},
	}
	query, err := QueryFromApplicationProfile(request)
	if err != nil {
		t.Fatal(err)
	}
	if query.RequestID != "flow-001" || query.ProfileID != "profile-001" {
		t.Fatalf("identity projection = %#v", query)
	}
	if query.Constraints.CPUCoresMin != 8 || query.Constraints.MemoryMiBMin != 32768 ||
		query.Constraints.Accelerator.MemoryMiBMinPerDevice != 12000 {
		t.Fatalf("constraint projection = %#v", query.Constraints)
	}
	if query.Preferences.MaxResults != 4 {
		t.Fatalf("max_results = %d, want replicas_min 4", query.Preferences.MaxResults)
	}
}

func TestQueryFromApplicationProfileRejectsIdentityAndReplicaErrors(t *testing.T) {
	request := ProfileRecommendationRequest{ApplicationProfile: ApplicationProfile{
		ProfileID: "profile-001", AppID: "app", AppVersion: "1",
		Requirements: ApplicationRequirements{
			Deployment: DeploymentRequirements{ReplicasMin: 2, ReplicasMax: 1},
		},
	}}
	if _, err := QueryFromApplicationProfile(request); err == nil {
		t.Fatal("invalid replica range was accepted")
	}
	request.ApplicationProfile.ProfileID = ""
	if _, err := QueryFromApplicationProfile(request); err == nil {
		t.Fatal("missing profile_id was accepted")
	}
}
