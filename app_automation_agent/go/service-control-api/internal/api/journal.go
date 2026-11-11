package api

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"

	"github.com/rs/zerolog/log"
)

func saveJournalRecord(path string, record any, exclusive bool) error {
	data, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".journal-*")
	if err != nil {
		return err
	}
	defer func() {
		if removeErr := os.Remove(file.Name()); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
			log.Warn().Err(removeErr).Msg("remove temporary journal file")
		}
	}()
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if exclusive {
		return os.Link(file.Name(), path)
	}
	return os.Rename(file.Name(), path)
}
