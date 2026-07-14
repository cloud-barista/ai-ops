package artifact

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/requestid"
	"github.com/rs/zerolog/log"
)

func (s *Service) runGoBuild(ctx context.Context, sourceRoot, target, binaryPath string, maskedPaths ...string) error {
	goBinary, err := exec.LookPath("go")
	if err != nil {
		return apperrors.New(model.ErrDeploymentFailed, "Go toolchain is not available on the AppDeploy server", http.StatusInternalServerError, false)
	}
	buildCtx, cancel := context.WithTimeout(ctx, s.buildTimeout)
	defer cancel()
	command := exec.CommandContext(buildCtx, goBinary, "build", "-trimpath", "-o", binaryPath, target)
	command.Dir = sourceRoot
	command.Env = crossCompileEnv(os.Environ())
	output, buildErr := command.CombinedOutput()
	if buildErr == nil {
		return nil
	}
	log.Error().
		Err(buildErr).
		Str("request_id", requestid.FromContext(ctx)).
		Str("component", "artifact-packager").
		Str("stage", "PACKAGE_BUILD").
		Str("build_output", maskedBuildOutput(string(output), append(maskedPaths, sourceRoot, binaryPath)...)).
		Msg("package build command failed")
	if errors.Is(buildCtx.Err(), context.DeadlineExceeded) {
		return apperrors.New(model.ErrDeploymentFailed, "package build timed out", http.StatusGatewayTimeout, true)
	}
	if errors.Is(buildCtx.Err(), context.Canceled) {
		return apperrors.New(model.ErrDeploymentFailed, "package build was canceled", http.StatusRequestTimeout, true)
	}
	return apperrors.New(model.ErrDeploymentFailed, "package build did not complete", http.StatusBadRequest, false)
}

func crossCompileEnv(values []string) []string {
	filtered := make([]string, 0, len(values)+3)
	for _, value := range values {
		key, _, found := strings.Cut(value, "=")
		if found {
			switch strings.ToUpper(key) {
			case "GOOS", "GOARCH", "CGO_ENABLED":
				continue
			}
		}
		filtered = append(filtered, value)
	}
	return append(filtered, "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0")
}

func maskedBuildOutput(output string, paths ...string) string {
	masked := strings.TrimSpace(output)
	for _, path := range paths {
		if path == "" {
			continue
		}
		masked = strings.ReplaceAll(masked, path, "<local-path>")
		masked = strings.ReplaceAll(masked, filepath.ToSlash(path), "<local-path>")
	}
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		masked = strings.ReplaceAll(masked, home, "<user-home>")
		masked = strings.ReplaceAll(masked, filepath.ToSlash(home), "<user-home>")
	}
	const maxLength = 3000
	if len(masked) > maxLength {
		masked = masked[len(masked)-maxLength:]
	}
	return masked
}
