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
)

type Settings struct {
	ServerPort        string
	StorePath         string
	CPUVMRunner       string
	GPUVMRunner       string
	SSHDefaultTimeout time.Duration
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

	return Settings{
		ServerPort:        serverPort,
		StorePath:         strings.TrimSpace(values.GetString(KeyStorePath)),
		CPUVMRunner:       strings.TrimSpace(values.GetString(KeyCPUVMRunner)),
		GPUVMRunner:       strings.TrimSpace(values.GetString(KeyGPUVMRunner)),
		SSHDefaultTimeout: timeout,
	}, nil
}

func newViper() *viper.Viper {
	values := viper.New()
	values.AutomaticEnv()
	values.SetDefault(KeyServerPort, "8080")
	values.SetDefault(KeySSHDefaultTimeout, 30*time.Second)
	return values
}
