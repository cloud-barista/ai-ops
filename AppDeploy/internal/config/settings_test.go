package config

import "testing"

func TestLoadCredentialRemoteSetting(t *testing.T) {
	t.Setenv(KeyConfigPath, "")
	t.Setenv(KeyCredentialRemote, "true")

	settings, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !settings.CredentialRemote {
		t.Fatal("credential remote setting was not loaded")
	}
}

func TestLoadLocalFaultInjectionSettings(t *testing.T) {
	t.Setenv(KeyConfigPath, "")
	t.Setenv(KeyLocalFaultRate, "0.25")
	t.Setenv(KeyLocalFaultSeed, "42")
	t.Setenv(KeyLocalFaultMax, "3")
	t.Setenv(KeyLocalFaultCodes, "GPU_OOM, CUDA_MISMATCH")
	t.Setenv(KeyLocalFaultTargetRates, "primary=0.8, alternative=0.1")
	t.Setenv(KeyLocalFaultDeterministic, "true")

	settings, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if settings.LocalFaultRate != 0.25 || settings.LocalFaultSeed != 42 || settings.LocalFaultMax != 3 {
		t.Fatalf("fault settings = rate=%v seed=%v max=%v", settings.LocalFaultRate, settings.LocalFaultSeed, settings.LocalFaultMax)
	}
	if len(settings.LocalFaultCodes) != 2 || settings.LocalFaultCodes[0] != "GPU_OOM" || settings.LocalFaultCodes[1] != "CUDA_MISMATCH" {
		t.Fatalf("fault codes = %#v", settings.LocalFaultCodes)
	}
	if settings.LocalFaultTargetRates["primary"] != 0.8 || settings.LocalFaultTargetRates["alternative"] != 0.1 || !settings.LocalFaultDeterministic {
		t.Fatalf("fault target settings = %#v deterministic=%v", settings.LocalFaultTargetRates, settings.LocalFaultDeterministic)
	}
}

func TestLoadRejectsInvalidLocalFaultTargetRate(t *testing.T) {
	t.Setenv(KeyConfigPath, "")
	t.Setenv(KeyLocalFaultTargetRates, "primary=1.1")
	if _, err := Load(); err == nil {
		t.Fatal("invalid target fault rate was accepted")
	}
}
