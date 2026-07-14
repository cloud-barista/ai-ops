package artifact

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/requestid"
	"github.com/rs/zerolog/log"
)

const (
	PresetAIOpsGeon = "aiops-geon-service-control"
	PackageTypeGo   = "go"
	PackageTypePy   = "python"
	PackageTypeNode = "node"
	PackageTypeBin  = "binary"
	PackageTypeSh   = "script"

	defaultPort           = 18089
	aiopsBinaryName       = "aiops-service-control-api"
	genericBinaryName     = "appdeploy-app"
	defaultMaxUploadBytes = int64(50 << 20)
	maximumUploadBytes    = int64(1 << 30)
)

type Config struct {
	AIOpsRoot      string
	OutputDir      string
	BuildTimeout   time.Duration
	MaxUploadBytes int64
}

type Service struct {
	aiopsRoot      string
	outputDir      string
	buildTimeout   time.Duration
	maxUploadBytes int64
	now            func() time.Time
}

func New(config Config) *Service {
	timeout := config.BuildTimeout
	if timeout <= 0 {
		timeout = 2 * time.Minute
	}
	maxUploadBytes := config.MaxUploadBytes
	if maxUploadBytes <= 0 {
		maxUploadBytes = defaultMaxUploadBytes
	}
	if maxUploadBytes > maximumUploadBytes {
		maxUploadBytes = maximumUploadBytes
	}
	return &Service{
		aiopsRoot:      strings.TrimSpace(config.AIOpsRoot),
		outputDir:      strings.TrimSpace(config.OutputDir),
		buildTimeout:   timeout,
		maxUploadBytes: maxUploadBytes,
		now:            time.Now,
	}
}

func (s *Service) MaxUploadBytes() int64 {
	return s.maxUploadBytes
}

func (s *Service) MaxRequestBytes() int64 {
	return s.maxUploadBytes + (1 << 20)
}

func (s *Service) Build(ctx context.Context, req model.PackageBuildRequest) (model.PackageBuildResponse, error) {
	started := s.now().UTC()
	if req.Preset != PresetAIOpsGeon {
		return model.PackageBuildResponse{}, invalidRequest("preset must be aiops-geon-service-control")
	}
	port, err := validateServicePort(req.ServicePort, defaultPort)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	version, err := packageVersion(req.AppVersion, started)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}

	sourceRoot, err := resolveConfiguredPath(s.aiopsRoot)
	if err != nil {
		return model.PackageBuildResponse{}, apperrors.New(model.ErrAppArtifactNotFound, "ai-ops-geon source is not configured on this server", http.StatusNotFound, false)
	}
	outputRoot, err := s.prepareOutputRoot()
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	serviceRoot := filepath.Join(sourceRoot, "go", "service-control-api")
	required := []archiveEntry{
		{source: filepath.Join(sourceRoot, "config", "agent_registry.json"), name: "config/agent_registry.json", mode: 0o644},
		{source: filepath.Join(sourceRoot, "config", "ops_llm_benchmark.json"), name: "config/ops_llm_benchmark.json", mode: 0o644},
		{source: filepath.Join(sourceRoot, "config", "inference_optimization.json"), name: "config/inference_optimization.json", mode: 0o644},
		{source: filepath.Join(sourceRoot, "docs", "submission", "openapi_service_control.yaml"), name: "docs/submission/openapi_service_control.yaml", mode: 0o644},
	}
	if !regularFile(filepath.Join(serviceRoot, "go.mod")) {
		return model.PackageBuildResponse{}, apperrors.New(model.ErrAppArtifactNotFound, "ai-ops-geon service-control source is unavailable", http.StatusNotFound, false)
	}
	for _, item := range required {
		if !regularFile(item.source) {
			return model.PackageBuildResponse{}, apperrors.New(model.ErrAppArtifactNotFound, "ai-ops-geon package source files are incomplete", http.StatusNotFound, false)
		}
	}

	buildDir, err := os.MkdirTemp(outputRoot, ".aiops-build-")
	if err != nil {
		return model.PackageBuildResponse{}, storageError("package build directory could not be created")
	}
	defer removeBuildDir(outputRoot, buildDir)

	binaryPath := filepath.Join(buildDir, aiopsBinaryName)
	if err := s.runGoBuild(ctx, serviceRoot, "./cmd/service-control-api", binaryPath, sourceRoot, outputRoot); err != nil {
		return model.PackageBuildResponse{}, err
	}

	createdAt := s.now().UTC()
	archiveName := fmt.Sprintf("aiops-geon-service-control-linux-amd64-%d.tar.gz", createdAt.UnixNano())
	entries := append([]archiveEntry{{source: binaryPath, name: aiopsBinaryName, mode: 0o755}}, required...)
	archivePath, checksum, size, err := createArchive(ctx, outputRoot, archiveName, entries, createdAt)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	artifactURI := toFileURI(archivePath)
	appSpec := aiopsPackageAppSpec(version, port, archiveName, artifactURI, checksum)
	return s.completedResponse(ctx, started, createdAt, PresetAIOpsGeon, archiveName, artifactURI, checksum, size, appSpec), nil
}

func (s *Service) BuildUploaded(ctx context.Context, req model.PackageBuildRequest, sourceName string, source io.Reader) (model.PackageBuildResponse, error) {
	started := s.now().UTC()
	options, err := normalizeUploadedBuildRequest(req, started)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	if source == nil {
		return model.PackageBuildResponse{}, apperrors.New(model.ErrAppArtifactNotFound, "source file is required", http.StatusBadRequest, false)
	}

	outputRoot, err := s.prepareOutputRoot()
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	buildDir, err := os.MkdirTemp(outputRoot, ".upload-build-")
	if err != nil {
		return model.PackageBuildResponse{}, storageError("package build directory could not be created")
	}
	defer removeBuildDir(outputRoot, buildDir)

	contentRoot, err := s.prepareUploadedSource(ctx, options.packageType, sourceName, source, buildDir, outputRoot)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	resolvedEntrypoint, entries, err := s.prepareUploadedEntries(ctx, options.packageType, contentRoot, options.entrypoint, buildDir, outputRoot)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	if len(entries) == 0 {
		return model.PackageBuildResponse{}, apperrors.New(model.ErrAppArtifactNotFound, "uploaded source contains no package files", http.StatusBadRequest, false)
	}

	createdAt := s.now().UTC()
	archiveName := fmt.Sprintf("%s-%s-linux-amd64-%d.tar.gz", options.appName, options.packageType, createdAt.UnixNano())
	archivePath, checksum, size, err := createArchive(ctx, outputRoot, archiveName, entries, createdAt)
	if err != nil {
		return model.PackageBuildResponse{}, err
	}
	artifactURI := toFileURI(archivePath)
	appSpec := uploadedPackageAppSpec(options.packageType, options.appName, options.version, options.runtimeType, options.port, options.healthPath, resolvedEntrypoint, archiveName, artifactURI, checksum)
	return s.completedResponse(ctx, started, createdAt, options.packageType, archiveName, artifactURI, checksum, size, appSpec), nil
}

func (s *Service) completedResponse(ctx context.Context, started, createdAt time.Time, packageType, archiveName, artifactURI, checksum string, size int64, appSpec model.AppSpec) model.PackageBuildResponse {
	elapsed := time.Since(started)
	log.Info().
		Str("request_id", requestid.FromContext(ctx)).
		Str("component", "artifact-packager").
		Str("stage", "PACKAGE_BUILD").
		Str("package_type", packageType).
		Int64("size_bytes", size).
		Int64("elapsed_ms", elapsed.Milliseconds()).
		Msg("package created")
	return model.PackageBuildResponse{
		PackageType: packageType,
		ArtifactURI: artifactURI,
		ArchiveName: archiveName,
		SizeBytes:   size,
		Checksum:    "sha256:" + checksum,
		CreatedAt:   createdAt,
		ElapsedMS:   elapsed.Milliseconds(),
		AppSpec:     appSpec,
	}
}
