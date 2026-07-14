package artifact

import (
	"encoding/binary"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
)

var (
	appNamePattern      = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,62}$`)
	appVersionPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	relativePathPattern = regexp.MustCompile(`^[A-Za-z0-9._/-]{1,256}$`)
)

type uploadedBuildOptions struct {
	packageType string
	appName     string
	version     string
	runtimeType string
	port        int
	healthPath  string
	entrypoint  string
}

func normalizeUploadedBuildRequest(req model.PackageBuildRequest, started time.Time) (uploadedBuildOptions, error) {
	packageType := strings.ToLower(strings.TrimSpace(req.PackageType))
	if !supportedUploadType(packageType) {
		return uploadedBuildOptions{}, invalidRequest("package_type must be one of go, python, node, binary, script")
	}
	appName := strings.TrimSpace(req.AppName)
	if !appNamePattern.MatchString(appName) {
		return uploadedBuildOptions{}, invalidRequest("app_name must use 2-63 lowercase letters, numbers, or hyphens")
	}
	version, err := packageVersion(req.AppVersion, started)
	if err != nil {
		return uploadedBuildOptions{}, err
	}
	runtimeType := strings.ToLower(strings.TrimSpace(req.RuntimeType))
	if runtimeType == "" {
		runtimeType = "cpu"
	}
	if runtimeType != "cpu" && runtimeType != "gpu" {
		return uploadedBuildOptions{}, invalidRequest("runtime_type must be cpu or gpu")
	}
	port, err := validateServicePort(req.ServicePort, 0)
	if err != nil {
		return uploadedBuildOptions{}, err
	}
	healthPath, err := validateHealthPath(req.HealthcheckPath, port)
	if err != nil {
		return uploadedBuildOptions{}, err
	}
	entrypoint := strings.TrimSpace(req.Entrypoint)
	if entrypoint == "" {
		return uploadedBuildOptions{}, invalidRequest("entrypoint is required")
	}
	return uploadedBuildOptions{
		packageType: packageType,
		appName:     appName,
		version:     version,
		runtimeType: runtimeType,
		port:        port,
		healthPath:  healthPath,
		entrypoint:  entrypoint,
	}, nil
}

func supportedUploadType(packageType string) bool {
	switch packageType {
	case PackageTypeGo, PackageTypePy, PackageTypeNode, PackageTypeBin, PackageTypeSh:
		return true
	default:
		return false
	}
}

func packageVersion(value string, started time.Time) (string, error) {
	version := strings.TrimSpace(value)
	if version == "" {
		version = "web-" + started.Format("20060102-150405-000")
	}
	if !appVersionPattern.MatchString(version) {
		return "", invalidRequest("app_version must contain only letters, numbers, dot, underscore, or hyphen")
	}
	return version, nil
}

func validateServicePort(value, fallback int) (int, error) {
	port := value
	if port == 0 {
		port = fallback
	}
	if port < 0 || port > 65535 {
		return 0, invalidRequest("service_port must be between 1 and 65535")
	}
	return port, nil
}

func validateHealthPath(value string, port int) (string, error) {
	if port == 0 {
		return "", nil
	}
	path := strings.TrimSpace(value)
	if path == "" {
		path = "/health"
	}
	if !strings.HasPrefix(path, "/") || len(path) > 256 || strings.ContainsAny(path, " \r\n\t") {
		return "", invalidRequest("healthcheck_path must be a relative HTTP path beginning with /")
	}
	return path, nil
}

func normalizeRelativePath(value string, allowDot bool) (string, error) {
	value = filepath.ToSlash(strings.TrimSpace(value))
	if allowDot && value == "." {
		return value, nil
	}
	if !relativePathPattern.MatchString(value) || strings.Contains(value, "//") || strings.Contains(value, "...") {
		return "", errors.New("relative path contains unsupported characters")
	}
	clean := filepath.ToSlash(filepath.Clean(filepath.FromSlash(value)))
	clean = strings.TrimPrefix(clean, "./")
	if clean == "" || clean == "." || clean == ".." || strings.HasPrefix(clean, "../") || filepath.IsAbs(filepath.FromSlash(clean)) || filepath.VolumeName(filepath.FromSlash(clean)) != "" {
		return "", errors.New("relative path escapes source root")
	}
	if allowDot {
		return "./" + clean, nil
	}
	return clean, nil
}

func validateLinuxAMD64Binary(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	header := make([]byte, 20)
	if _, err := io.ReadFull(file, header); err != nil {
		return err
	}
	if string(header[:4]) != "\x7fELF" || header[4] != 2 || header[5] != 1 || binary.LittleEndian.Uint16(header[18:20]) != 62 {
		return errors.New("not a Linux amd64 ELF executable")
	}
	return nil
}

func invalidRequest(message string) error {
	return apperrors.New(model.ErrAppSpecInvalid, message, http.StatusBadRequest, false)
}

func storageError(message string) error {
	return apperrors.New(model.ErrStorageUnavailable, message, http.StatusInternalServerError, false)
}
