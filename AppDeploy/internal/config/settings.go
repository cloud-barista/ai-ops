package config

import (
	"fmt"
	"strings"
	"time"

	"github.com/spf13/viper"
)

const (
	KeyConfigPath        = "AIAPP_CONFIG_PATH"
	KeyServerPort        = "AIAPP_SERVER_PORT"
	KeyStorePath         = "AIAPP_STORE_PATH"
	KeyCPUVMRunner       = "AIAPP_CPUVM_RUNNER"
	KeyGPUVMRunner       = "AIAPP_GPUVM_RUNNER"
	KeySSHDefaultTimeout = "AIAPP_SSH_DEFAULT_TIMEOUT"
	KeyCredentialRemote  = "AIAPP_CREDENTIAL_API_ALLOW_REMOTE"
	KeyAIOpsRoot         = "AIAPP_AIOPS_ROOT"
	KeyPackageOutputDir  = "AIAPP_PACKAGE_OUTPUT_DIR"
	KeyPackageTimeout    = "AIAPP_PACKAGE_BUILD_TIMEOUT"
	KeyPackageMaxUpload  = "AIAPP_PACKAGE_MAX_UPLOAD_BYTES"
)

type Settings struct {
	ServerPort        string
	StorePath         string
	CPUVMRunner       string
	GPUVMRunner       string
	SSHDefaultTimeout time.Duration
	CredentialRemote  bool
	AIOpsRoot         string
	PackageOutputDir  string
	PackageTimeout    time.Duration
	PackageMaxUpload  int64
}

func Load() (Settings, error) {
	values := newViper()
	if path := strings.TrimSpace(values.GetString(KeyConfigPath)); path != "" {
		values.SetConfigFile(path)
		if err := values.ReadInConfig(); err != nil {
			return Settings{}, fmt.Errorf("load config file: %w", err)
		}
	}

	serverPort := strings.TrimSpace(values.GetString(KeyServerPort))
	if serverPort == "" {
		serverPort = "8080"
	}
	timeout := values.GetDuration(KeySSHDefaultTimeout)
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	packageTimeout := values.GetDuration(KeyPackageTimeout)
	if packageTimeout == 0 {
		packageTimeout = 2 * time.Minute
	}
	packageMaxUpload := values.GetInt64(KeyPackageMaxUpload)
	if packageMaxUpload <= 0 {
		packageMaxUpload = 50 << 20
	}

	return Settings{
		ServerPort:        serverPort,
		StorePath:         strings.TrimSpace(values.GetString(KeyStorePath)),
		CPUVMRunner:       strings.TrimSpace(values.GetString(KeyCPUVMRunner)),
		GPUVMRunner:       strings.TrimSpace(values.GetString(KeyGPUVMRunner)),
		SSHDefaultTimeout: timeout,
		CredentialRemote:  values.GetBool(KeyCredentialRemote),
		AIOpsRoot:         strings.TrimSpace(values.GetString(KeyAIOpsRoot)),
		PackageOutputDir:  strings.TrimSpace(values.GetString(KeyPackageOutputDir)),
		PackageTimeout:    packageTimeout,
		PackageMaxUpload:  packageMaxUpload,
	}, nil
}

func newViper() *viper.Viper {
	values := viper.New()
	values.AutomaticEnv()
	values.SetDefault(KeyServerPort, "8080")
	values.SetDefault(KeySSHDefaultTimeout, 30*time.Second)
	values.SetDefault(KeyAIOpsRoot, "../ai-ops-geon")
	values.SetDefault(KeyPackageOutputDir, "tmp/appdeploy-web/packages")
	values.SetDefault(KeyPackageTimeout, 2*time.Minute)
	values.SetDefault(KeyPackageMaxUpload, 50<<20)
	return values
}
