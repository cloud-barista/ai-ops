package config

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/viper"
)

const (
	KeyConfigPath              = "AIAPP_CONFIG_PATH"
	KeyServerPort              = "AIAPP_SERVER_PORT"
	KeyStorePath               = "AIAPP_STORE_PATH"
	KeyCPUVMRunner             = "AIAPP_CPUVM_RUNNER"
	KeyGPUVMRunner             = "AIAPP_GPUVM_RUNNER"
	KeySSHDefaultTimeout       = "AIAPP_SSH_DEFAULT_TIMEOUT"
	KeyCredentialRemote        = "AIAPP_CREDENTIAL_API_ALLOW_REMOTE"
	KeyAIOpsRoot               = "AIAPP_AIOPS_ROOT"
	KeyPackageOutputDir        = "AIAPP_PACKAGE_OUTPUT_DIR"
	KeyPackageTimeout          = "AIAPP_PACKAGE_BUILD_TIMEOUT"
	KeyPackageMaxUpload        = "AIAPP_PACKAGE_MAX_UPLOAD_BYTES"
	KeyResourceProvider        = "RESOURCE_PROVIDER"
	KeyPlacementProvider       = "PLACEMENT_PROVIDER"
	KeyETRIResourceEndpoint    = "ETRI_RESOURCE_ENDPOINT"
	KeyETRIPlacementEndpoint   = "ETRI_PLACEMENT_ENDPOINT"
	KeyETRICredentialRef       = "ETRI_CREDENTIAL_REF"
	KeyETRITimeout             = "ETRI_TIMEOUT"
	KeyLocalRuntimeWorkDir     = "AIAPP_LOCAL_RUNTIME_WORK_DIR"
	KeyLocalFaultRate          = "AIAPP_LOCAL_FAULT_RATE"
	KeyLocalFaultSeed          = "AIAPP_LOCAL_FAULT_SEED"
	KeyLocalFaultMax           = "AIAPP_LOCAL_FAULT_MAX"
	KeyLocalFaultCodes         = "AIAPP_LOCAL_FAULT_CODES"
	KeyLocalFaultTargetRates   = "AIAPP_LOCAL_FAULT_TARGET_RATES"
	KeyLocalFaultDeterministic = "AIAPP_LOCAL_FAULT_DETERMINISTIC"
)

type Settings struct {
	ServerPort              string
	StorePath               string
	CPUVMRunner             string
	GPUVMRunner             string
	SSHDefaultTimeout       time.Duration
	CredentialRemote        bool
	AIOpsRoot               string
	PackageOutputDir        string
	PackageTimeout          time.Duration
	PackageMaxUpload        int64
	ResourceProvider        string
	PlacementProvider       string
	ETRIResourceEndpoint    string
	ETRIPlacementEndpoint   string
	ETRICredentialRef       string
	ETRITimeout             time.Duration
	LocalRuntimeWorkDir     string
	LocalFaultRate          float64
	LocalFaultSeed          int64
	LocalFaultMax           int
	LocalFaultCodes         []string
	LocalFaultTargetRates   map[string]float64
	LocalFaultDeterministic bool
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
	etriTimeout := values.GetDuration(KeyETRITimeout)
	if etriTimeout == 0 {
		etriTimeout = 30 * time.Second
	}
	faultRate := values.GetFloat64(KeyLocalFaultRate)
	if faultRate < 0 || faultRate > 1 {
		return Settings{}, fmt.Errorf("%s must be between 0 and 1", KeyLocalFaultRate)
	}
	faultMax := values.GetInt(KeyLocalFaultMax)
	if faultMax < 0 {
		return Settings{}, fmt.Errorf("%s must not be negative", KeyLocalFaultMax)
	}
	faultTargetRates, err := parseTargetRates(values.GetString(KeyLocalFaultTargetRates))
	if err != nil {
		return Settings{}, err
	}

	return Settings{
		ServerPort:              serverPort,
		StorePath:               strings.TrimSpace(values.GetString(KeyStorePath)),
		CPUVMRunner:             strings.TrimSpace(values.GetString(KeyCPUVMRunner)),
		GPUVMRunner:             strings.TrimSpace(values.GetString(KeyGPUVMRunner)),
		SSHDefaultTimeout:       timeout,
		CredentialRemote:        values.GetBool(KeyCredentialRemote),
		AIOpsRoot:               strings.TrimSpace(values.GetString(KeyAIOpsRoot)),
		PackageOutputDir:        strings.TrimSpace(values.GetString(KeyPackageOutputDir)),
		PackageTimeout:          packageTimeout,
		PackageMaxUpload:        packageMaxUpload,
		ResourceProvider:        strings.ToLower(strings.TrimSpace(values.GetString(KeyResourceProvider))),
		PlacementProvider:       strings.ToLower(strings.TrimSpace(values.GetString(KeyPlacementProvider))),
		ETRIResourceEndpoint:    strings.TrimSpace(values.GetString(KeyETRIResourceEndpoint)),
		ETRIPlacementEndpoint:   strings.TrimSpace(values.GetString(KeyETRIPlacementEndpoint)),
		ETRICredentialRef:       strings.TrimSpace(values.GetString(KeyETRICredentialRef)),
		ETRITimeout:             etriTimeout,
		LocalRuntimeWorkDir:     strings.TrimSpace(values.GetString(KeyLocalRuntimeWorkDir)),
		LocalFaultRate:          faultRate,
		LocalFaultSeed:          values.GetInt64(KeyLocalFaultSeed),
		LocalFaultMax:           faultMax,
		LocalFaultCodes:         splitCSV(values.GetString(KeyLocalFaultCodes)),
		LocalFaultTargetRates:   faultTargetRates,
		LocalFaultDeterministic: values.GetBool(KeyLocalFaultDeterministic),
	}, nil
}

func splitCSV(value string) []string {
	var result []string
	for _, item := range strings.Split(value, ",") {
		item = strings.TrimSpace(item)
		if item != "" {
			result = append(result, item)
		}
	}
	return result
}

func parseTargetRates(value string) (map[string]float64, error) {
	rates := map[string]float64{}
	for _, item := range splitCSV(value) {
		parts := strings.SplitN(item, "=", 2)
		if len(parts) != 2 || strings.TrimSpace(parts[0]) == "" {
			return nil, fmt.Errorf("%s must use target_id=rate entries", KeyLocalFaultTargetRates)
		}
		rate, err := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
		if err != nil || rate < 0 || rate > 1 {
			return nil, fmt.Errorf("%s rate for %q must be between 0 and 1", KeyLocalFaultTargetRates, strings.TrimSpace(parts[0]))
		}
		rates[strings.TrimSpace(parts[0])] = rate
	}
	return rates, nil
}

func newViper() *viper.Viper {
	values := viper.New()
	values.AutomaticEnv()
	values.SetDefault(KeyServerPort, "8080")
	values.SetDefault(KeySSHDefaultTimeout, 30*time.Second)
	values.SetDefault(KeyAIOpsRoot, "../ai-ops-geon")
	values.SetDefault(KeyPackageOutputDir, "tmp/appdeploy-packages")
	values.SetDefault(KeyPackageTimeout, 2*time.Minute)
	values.SetDefault(KeyPackageMaxUpload, 50<<20)
	values.SetDefault(KeyResourceProvider, "local")
	values.SetDefault(KeyPlacementProvider, "local")
	values.SetDefault(KeyETRITimeout, 30*time.Second)
	values.SetDefault(KeyLocalRuntimeWorkDir, "tmp/local-runtime")
	return values
}
