package credentialref

import (
	"strings"
	"testing"
)

func TestValid(t *testing.T) {
	t.Parallel()
	for _, value := range []string{"", "cred://runtime/cpu-vm-001", "cred://nhn-cloud/gpu-vm-001", "cred://etri/aws-gpu-vm-001"} {
		if !Valid(value) {
			t.Fatalf("Valid(%q) = false", value)
		}
	}
	for _, value := range []string{"password", "-----BEGIN PRIVATE KEY-----", "cred://runtime/Bad-ID", "cred://runtime/../secret", "cred://runtime/" + strings.Repeat("a", MaxLength)} {
		if Valid(value) {
			t.Fatalf("Valid(%q) = true", value)
		}
	}
}
