package artifact

import (
	"archive/zip"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/requestid"
	"github.com/rs/zerolog/log"
)

var errUploadTooLarge = errors.New("uploaded source exceeds size limit")

func (s *Service) prepareUploadedSource(ctx context.Context, packageType, sourceName string, source io.Reader, buildDir, outputRoot string) (string, error) {
	uploadPath := filepath.Join(buildDir, "source-upload")
	if err := copyUploadedSource(ctx, uploadPath, source, s.maxUploadBytes); err != nil {
		if errors.Is(err, errUploadTooLarge) {
			return "", apperrors.New(model.ErrAppSpecInvalid, fmt.Sprintf("source file must be at most %d MiB", s.maxUploadBytes>>20), http.StatusRequestEntityTooLarge, false)
		}
		return "", storageError("uploaded source could not be stored")
	}

	sourceRoot := filepath.Join(buildDir, "source")
	if err := os.MkdirAll(sourceRoot, 0o755); err != nil {
		return "", storageError("uploaded source directory could not be created")
	}
	isZip, err := isZipArchive(uploadPath)
	if err != nil {
		return "", apperrors.New(model.ErrAppArtifactNotFound, "uploaded source could not be inspected", http.StatusBadRequest, false)
	}
	if packageType == PackageTypeGo && !isZip {
		return "", invalidRequest("go package source must be a ZIP archive")
	}
	if isZip {
		if err := extractZip(ctx, uploadPath, sourceRoot, expandedSourceLimit(s.maxUploadBytes)); err != nil {
			log.Error().
				Str("request_id", requestid.FromContext(ctx)).
				Str("component", "artifact-packager").
				Str("stage", "PACKAGE_EXTRACT").
				Str("package_type", packageType).
				Str("cause", maskedBuildOutput(err.Error(), buildDir, outputRoot)).
				Msg("uploaded package source extraction failed")
			return "", invalidRequest("source ZIP is invalid, unsafe, or exceeds extraction limits")
		}
	} else {
		fileName, nameErr := safeUploadName(sourceName)
		if nameErr != nil {
			return "", invalidRequest("source filename must use letters, numbers, dot, underscore, or hyphen")
		}
		if err := copyFile(uploadPath, filepath.Join(sourceRoot, fileName), 0o644); err != nil {
			return "", storageError("uploaded source could not be prepared")
		}
	}

	contentRoot, err := packageContentRoot(sourceRoot)
	if err != nil {
		return "", apperrors.New(model.ErrAppArtifactNotFound, "uploaded source is empty", http.StatusBadRequest, false)
	}
	return contentRoot, nil
}

func (s *Service) prepareUploadedEntries(ctx context.Context, packageType, contentRoot, entrypoint, buildDir, outputRoot string) (string, []archiveEntry, error) {
	excludedName := ""
	if packageType == PackageTypeGo {
		excludedName = genericBinaryName
	}
	entries, err := collectArchiveEntries(contentRoot, excludedName)
	if err != nil {
		return "", nil, storageError("uploaded source could not be inspected")
	}
	if packageType == PackageTypeGo {
		if !regularFile(filepath.Join(contentRoot, "go.mod")) {
			return "", nil, apperrors.New(model.ErrAppArtifactNotFound, "go source ZIP must contain go.mod at its root", http.StatusBadRequest, false)
		}
		buildTarget, err := normalizeRelativePath(entrypoint, true)
		if err != nil {
			return "", nil, invalidRequest("go entrypoint must be a relative package path such as . or ./cmd/server")
		}
		targetPath := contentRoot
		if buildTarget != "." {
			targetPath = filepath.Join(contentRoot, filepath.FromSlash(strings.TrimPrefix(buildTarget, "./")))
		}
		info, statErr := os.Stat(targetPath)
		if statErr != nil || !info.IsDir() {
			return "", nil, apperrors.New(model.ErrAppArtifactNotFound, "go entrypoint package directory was not found", http.StatusBadRequest, false)
		}
		binaryPath := filepath.Join(buildDir, genericBinaryName)
		if err := s.runGoBuild(ctx, contentRoot, buildTarget, binaryPath, contentRoot, outputRoot); err != nil {
			return "", nil, err
		}
		entries = append([]archiveEntry{{source: binaryPath, name: genericBinaryName, mode: 0o755}}, entries...)
		return genericBinaryName, entries, nil
	}

	entryPath, err := normalizeRelativePath(entrypoint, false)
	if err != nil {
		return "", nil, invalidRequest("entrypoint must be a safe relative file path")
	}
	localEntrypoint := filepath.Join(contentRoot, filepath.FromSlash(entryPath))
	if !regularFile(localEntrypoint) {
		return "", nil, apperrors.New(model.ErrAppArtifactNotFound, "entrypoint file was not found in uploaded source", http.StatusBadRequest, false)
	}
	switch packageType {
	case PackageTypePy:
		if strings.ToLower(filepath.Ext(entryPath)) != ".py" {
			return "", nil, invalidRequest("python entrypoint must be a .py file")
		}
	case PackageTypeNode:
		extension := strings.ToLower(filepath.Ext(entryPath))
		if extension != ".js" && extension != ".mjs" && extension != ".cjs" {
			return "", nil, invalidRequest("node entrypoint must be a .js, .mjs, or .cjs file")
		}
	case PackageTypeSh:
		extension := strings.ToLower(filepath.Ext(entryPath))
		if extension != ".sh" && extension != ".bash" {
			return "", nil, invalidRequest("script entrypoint must be a .sh or .bash file")
		}
	case PackageTypeBin:
		if err := validateLinuxAMD64Binary(localEntrypoint); err != nil {
			return "", nil, invalidRequest("binary entrypoint must be a Linux amd64 ELF executable")
		}
	}
	return entryPath, entries, nil
}

func copyUploadedSource(ctx context.Context, destination string, source io.Reader, maxBytes int64) error {
	file, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	limited := &io.LimitedReader{R: &contextReader{ctx: ctx, reader: source}, N: maxBytes + 1}
	written, copyErr := io.Copy(file, limited)
	closeErr := file.Close()
	if copyErr != nil {
		return copyErr
	}
	if closeErr != nil {
		return closeErr
	}
	if written > maxBytes {
		return errUploadTooLarge
	}
	if written == 0 {
		return errors.New("uploaded source is empty")
	}
	return nil
}

func isZipArchive(path string) (bool, error) {
	file, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer file.Close()
	header := make([]byte, 4)
	if _, err := io.ReadFull(file, header); err != nil {
		return false, err
	}
	return string(header) == "PK\x03\x04" || string(header) == "PK\x05\x06" || string(header) == "PK\x07\x08", nil
}

func extractZip(ctx context.Context, archivePath, destination string, maxExpandedBytes int64) error {
	reader, err := zip.OpenReader(archivePath)
	if err != nil {
		return err
	}
	defer reader.Close()
	if len(reader.File) > maxArchiveFiles {
		return errors.New("ZIP contains too many files")
	}
	var expanded int64
	for _, item := range reader.File {
		if err := ctx.Err(); err != nil {
			return err
		}
		clean, err := safeArchivePath(item.Name)
		if err != nil {
			return err
		}
		if shouldSkipSourcePath(clean) {
			continue
		}
		mode := item.Mode()
		if mode&os.ModeSymlink != 0 || (!mode.IsDir() && !mode.IsRegular()) {
			return fmt.Errorf("ZIP entry %q is not a regular file", item.Name)
		}
		target := filepath.Join(destination, filepath.FromSlash(clean))
		if item.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		remaining := maxExpandedBytes - expanded
		if item.UncompressedSize64 > uint64(remaining) {
			return errors.New("ZIP expanded size exceeds limit")
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		source, err := item.Open()
		if err != nil {
			return err
		}
		file, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
		if err != nil {
			if closeErr := source.Close(); closeErr != nil {
				return errors.Join(err, fmt.Errorf("close ZIP entry: %w", closeErr))
			}
			return err
		}
		written, copyErr := io.Copy(file, &io.LimitedReader{R: &contextReader{ctx: ctx, reader: source}, N: remaining + 1})
		fileErr := file.Close()
		sourceErr := source.Close()
		if copyErr != nil {
			return copyErr
		}
		if fileErr != nil {
			return fileErr
		}
		if sourceErr != nil {
			return sourceErr
		}
		if written > remaining {
			return errors.New("ZIP expanded size exceeds limit")
		}
		expanded += written
	}
	return nil
}

func safeArchivePath(value string) (string, error) {
	value = strings.ReplaceAll(value, "\\", "/")
	clean := filepath.ToSlash(filepath.Clean(filepath.FromSlash(value)))
	if clean == "" || clean == "." || clean == ".." || strings.HasPrefix(clean, "../") || filepath.IsAbs(filepath.FromSlash(clean)) || filepath.VolumeName(filepath.FromSlash(clean)) != "" {
		return "", errors.New("ZIP entry escapes source root")
	}
	return clean, nil
}

func shouldSkipSourcePath(value string) bool {
	value = strings.TrimPrefix(filepath.ToSlash(value), "./")
	return value == ".DS_Store" || strings.HasPrefix(value, "__MACOSX/") || strings.HasPrefix(value, ".git/") || value == ".git"
}

func expandedSourceLimit(uploadLimit int64) int64 {
	const maximum = int64(1 << 30)
	if uploadLimit <= 0 || uploadLimit > maximum/5 {
		return maximum
	}
	return uploadLimit * 5
}

func packageContentRoot(sourceRoot string) (string, error) {
	items, err := os.ReadDir(sourceRoot)
	if err != nil {
		return "", err
	}
	visible := make([]os.DirEntry, 0, len(items))
	for _, item := range items {
		if item.Name() == ".DS_Store" || item.Name() == "__MACOSX" {
			continue
		}
		visible = append(visible, item)
	}
	if len(visible) == 0 {
		return "", errors.New("source is empty")
	}
	if len(visible) == 1 && visible[0].IsDir() {
		return filepath.Join(sourceRoot, visible[0].Name()), nil
	}
	return sourceRoot, nil
}

func safeUploadName(value string) (string, error) {
	value = strings.TrimSpace(value)
	base := filepath.Base(value)
	if base == "" || base == "." || base == ".." || len(base) > 128 {
		return "", errors.New("invalid source filename")
	}
	for _, char := range base {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '.' || char == '_' || char == '-') {
			return "", errors.New("invalid source filename")
		}
	}
	return base, nil
}
