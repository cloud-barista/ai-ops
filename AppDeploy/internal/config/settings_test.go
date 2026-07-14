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
