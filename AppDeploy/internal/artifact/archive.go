package artifact

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/rs/zerolog/log"
)

const maxArchiveFiles = 5000

type archiveEntry struct {
	source string
	name   string
	mode   int64
}

func (s *Service) prepareOutputRoot() (string, error) {
	outputRoot, err := resolveConfiguredPath(s.outputDir)
	if err != nil {
		return "", storageError("package output directory is not configured on this server")
	}
	if err := os.MkdirAll(outputRoot, 0o755); err != nil {
		return "", storageError("package output directory is not writable")
	}
	return outputRoot, nil
}

func collectArchiveEntries(root, excludedName string) ([]archiveEntry, error) {
	entries := make([]archiveEntry, 0)
	err := filepath.WalkDir(root, func(path string, item os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == root {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		name := filepath.ToSlash(relative)
		if shouldSkipSourcePath(name) {
			if item.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if item.IsDir() {
			return nil
		}
		info, err := item.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("source entry %q is not a regular file", name)
		}
		if name == excludedName {
			return nil
		}
		mode := int64(0o644)
		if info.Mode().Perm()&0o111 != 0 {
			mode = 0o755
		}
		entries = append(entries, archiveEntry{source: path, name: name, mode: mode})
		if len(entries) > maxArchiveFiles {
			return errors.New("source contains too many files")
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].name < entries[j].name })
	return entries, nil
}

func copyFile(source, destination string, mode os.FileMode) error {
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, mode)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(output, input)
	closeErr := output.Close()
	if copyErr != nil {
		return copyErr
	}
	return closeErr
}

func createArchive(ctx context.Context, outputRoot, archiveName string, entries []archiveEntry, createdAt time.Time) (string, string, int64, error) {
	archivePath := filepath.Join(outputRoot, archiveName)
	if err := writeTarGz(ctx, archivePath, entries, createdAt); err != nil {
		if removeErr := os.Remove(archivePath); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
			log.Warn().Err(removeErr).Str("component", "artifact-packager").Msg("partial package archive cleanup failed")
		}
		return "", "", 0, storageError("package archive could not be created")
	}
	checksum, size, err := archiveMetadata(archivePath)
	if err != nil {
		return "", "", 0, storageError("package archive could not be inspected")
	}
	return archivePath, checksum, size, nil
}

func writeTarGz(ctx context.Context, archivePath string, entries []archiveEntry, createdAt time.Time) error {
	file, err := os.OpenFile(archivePath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	gzipWriter := gzip.NewWriter(file)
	tarWriter := tar.NewWriter(gzipWriter)
	var writeErr error
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			writeErr = err
			break
		}
		if err := appendTarFile(ctx, tarWriter, entry, createdAt); err != nil {
			writeErr = err
			break
		}
	}
	tarErr := tarWriter.Close()
	gzipErr := gzipWriter.Close()
	fileErr := file.Close()
	if writeErr != nil {
		return writeErr
	}
	if tarErr != nil {
		return tarErr
	}
	if gzipErr != nil {
		return gzipErr
	}
	return fileErr
}

func appendTarFile(ctx context.Context, writer *tar.Writer, entry archiveEntry, createdAt time.Time) error {
	file, err := os.Open(entry.source)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	header := &tar.Header{
		Name:    filepath.ToSlash(entry.name),
		Mode:    entry.mode,
		Size:    info.Size(),
		ModTime: createdAt,
	}
	if err := writer.WriteHeader(header); err != nil {
		return err
	}
	_, err = io.Copy(writer, &contextReader{ctx: ctx, reader: file})
	return err
}

type contextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r *contextReader) Read(buffer []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(buffer)
}

func archiveMetadata(path string) (string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return "", 0, err
	}
	return fmt.Sprintf("%x", hash.Sum(nil)), size, nil
}

func toFileURI(path string) string {
	slashPath := filepath.ToSlash(path)
	if runtime.GOOS == "windows" && len(slashPath) >= 2 && slashPath[1] == ':' {
		slashPath = "/" + slashPath
	}
	return (&url.URL{Scheme: "file", Path: slashPath}).String()
}

func regularFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func resolveConfiguredPath(configured string) (string, error) {
	configured = strings.TrimSpace(configured)
	if configured == "" {
		return "", errors.New("path is empty")
	}
	if filepath.IsAbs(configured) {
		return filepath.Clean(configured), nil
	}
	root, err := projectRoot()
	if err != nil {
		return "", err
	}
	return filepath.Abs(filepath.Join(root, configured))
}

func projectRoot() (string, error) {
	wd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if regularFile(filepath.Join(wd, "go.mod")) {
			return wd, nil
		}
		parent := filepath.Dir(wd)
		if parent == wd {
			return "", errors.New("project root not found")
		}
		wd = parent
	}
}

func removeBuildDir(outputRoot, buildDir string) {
	relative, err := filepath.Rel(outputRoot, buildDir)
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return
	}
	if err := os.RemoveAll(buildDir); err != nil {
		log.Warn().Err(err).Str("component", "artifact-packager").Msg("temporary package build directory cleanup failed")
	}
}
