package profile

import (
	"context"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/credentialref"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/store"
)

func TestServiceDeleteProfileResponses(t *testing.T) {
	ctx := context.Background()
	repo := store.NewMemory()
	service := NewService(repo)

	targetProfile := validTargetProfile()
	targetProfile.TargetProfileID = "target-delete-service"
	targetProfile.Name = "target service delete"
	if _, err := service.CreateTarget(ctx, targetProfile); err != nil {
		t.Fatal(err)
	}
	if err := repo.SaveInventory(ctx, model.ResourceInventory{TargetProfileID: targetProfile.TargetProfileID}); err != nil {
		t.Fatal(err)
	}
	targetDeleted, err := service.DeleteTarget(ctx, targetProfile.TargetProfileID)
	if err != nil {
		t.Fatal(err)
	}
	if targetDeleted.ProfileType != model.ProfileTypeTarget || targetDeleted.ProfileID != targetProfile.TargetProfileID || targetDeleted.Name != targetProfile.Name {
		t.Fatalf("unexpected target delete response: %+v", targetDeleted)
	}
	if !targetDeleted.Deleted || !targetDeleted.InventoryDeleted || targetDeleted.DeletedAt.IsZero() || targetDeleted.RequestID != "" {
		t.Fatalf("unexpected target delete result fields: %+v", targetDeleted)
	}

	withoutInventory := validTargetProfile()
	withoutInventory.TargetProfileID = "target-delete-without-inventory"
	withoutInventory.Name = ""
	if _, err := service.CreateTarget(ctx, withoutInventory); err != nil {
		t.Fatal(err)
	}
	withoutInventoryDeleted, err := service.DeleteTarget(ctx, withoutInventory.TargetProfileID)
	if err != nil {
		t.Fatal(err)
	}
	if withoutInventoryDeleted.Name != "" || withoutInventoryDeleted.InventoryDeleted {
		t.Fatalf("unexpected target delete response without inventory: %+v", withoutInventoryDeleted)
	}
}

func TestValidateTargetProfileCredentialRef(t *testing.T) {
	t.Parallel()

	valid := []string{
		"",
		"cred://runtime/cpu-vm-001",
		"cred://local/cpu-vm-001",
		"cred://nhn-cloud/gpu-vm-001",
		"cred://etri/aws-gpu-vm-001",
	}
	for _, credentialRef := range valid {
		credentialRef := credentialRef
		t.Run("valid_"+strings.ReplaceAll(credentialRef, "/", "_"), func(t *testing.T) {
			profile := validTargetProfile()
			profile.VM.CredentialRef = credentialRef
			if err := ValidateTargetProfile(profile); err != nil {
				t.Fatalf("ValidateTargetProfile(%q): %v", credentialRef, err)
			}
		})
	}

	invalid := []string{
		"plain-password",
		"-----BEGIN PRIVATE KEY-----",
		" cred://runtime/cpu-vm-001",
		"cred://runtime/Bad-ID",
		"cred://runtime/../secret",
		"cred://runtime/cpu-vm-001?password=value",
		"cred://runtime/" + strings.Repeat("a", credentialref.MaxLength),
	}
	for _, credentialRef := range invalid {
		credentialRef := credentialRef
		t.Run("invalid_"+strings.ReplaceAll(credentialRef[:min(len(credentialRef), 20)], "/", "_"), func(t *testing.T) {
			profile := validTargetProfile()
			profile.VM.CredentialRef = credentialRef
			if err := ValidateTargetProfile(profile); err == nil {
				t.Fatalf("ValidateTargetProfile(%q) succeeded", credentialRef)
			}
		})
	}
}

func validTargetProfile() model.TargetProfile {
	return model.TargetProfile{
		TargetProfileID: "target-cpu-001",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "cpu-vm.example.internal",
			SSHPort:       22,
			CredentialRef: "cred://local/cpu-vm-001",
		},
		Runtime: model.TargetRuntime{
			RuntimeType:   "cpu",
			Accelerator:   "none",
			OperatingMode: "vm_process",
		},
	}
}
