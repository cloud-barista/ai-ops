package profileapi

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"regexp"

	"github.com/labstack/echo/v4"
	"github.com/rs/zerolog/log"
)

const (
	pathOpsLLMRuns    = "/api/v1/ops-llm/runs"
	opsLLMRunHeader   = "X-AIMCMP-OpsLLM-Run-ID"
	opsLLMUnconfirmed = "completion_unconfirmed"
)

var opsLLMRunID = regexp.MustCompile(`^opsllm-[a-z2-7]{26}$`)

type opsLLMRunRecord struct {
	RequestSHA256 string             `json:"request_sha256"`
	Result        OpsLLMIntentResult `json:"result"`
}

func (h handler) requireOpsLLM(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		if h.config.OpsLLMToken == "" {
			return c.JSON(http.StatusServiceUnavailable, ErrorResponse{Code: "OPS_LLM_UNAVAILABLE", Message: "Ops LLM execution is not configured."})
		}
		bearer := "Bearer " + h.config.OpsLLMToken
		if subtle.ConstantTimeCompare([]byte(c.Request().Header.Get(echo.HeaderAuthorization)), []byte(bearer)) != 1 {
			return c.JSON(http.StatusUnauthorized, ErrorResponse{Code: "UNAUTHORIZED", Message: "Valid Ops LLM authorization is required."})
		}
		return next(c)
	}
}

// getOpsLLMRun godoc
// @ID GetOpsLLMRun
// @Summary Read recorded Ops LLM execution evidence
// @Description Return the bounded result or completion_unconfirmed for an execution without a stored result.
// @Tags Ops LLM
// @Produce json
// @Security OpsLLMBearer
// @Param run_id path string true "OpsLLM execution ID"
// @Success 200 {object} OpsLLMIntentResult
// @Failure 400 {object} ErrorResponse
// @Failure 401 {object} ErrorResponse
// @Failure 404 {object} ErrorResponse
// @Failure 503 {object} ErrorResponse
// @Router /api/v1/ops-llm/runs/{run_id} [get]
func (h handler) getOpsLLMRun(c echo.Context) error {
	id := c.Param("run_id")
	if !opsLLMRunID.MatchString(id) {
		return invalidOpsLLMRunID(c)
	}
	record, err := readOpsLLMRun(h.opsLLMRunPath(id), id)
	if errors.Is(err, os.ErrNotExist) {
		return c.JSON(http.StatusNotFound, ErrorResponse{Code: "OPS_LLM_RUN_NOT_FOUND", Message: "No Ops LLM execution was recorded for this ID."})
	}
	if err != nil {
		return opsLLMRunStoreError(c, err)
	}
	return c.JSON(http.StatusOK, record.Result)
}

func invalidOpsLLMRunID(c echo.Context) error {
	return c.JSON(http.StatusBadRequest, ErrorResponse{Code: "INVALID_OPS_LLM_RUN_ID", Message: "Supply opsllm- followed by 26 lowercase base32 characters."})
}

func opsLLMRunStoreError(c echo.Context, err error) error {
	log.Error().Err(err).Msg("Ops LLM execution record unavailable")
	return c.JSON(http.StatusServiceUnavailable, ErrorResponse{Code: "OPS_LLM_RUN_STORE_UNAVAILABLE", Message: "The Ops LLM execution record is unavailable."})
}

func replayOpsLLMRun(c echo.Context, record opsLLMRunRecord, err error, digest string) error {
	if err != nil {
		return opsLLMRunStoreError(c, err)
	}
	if record.RequestSHA256 != digest {
		return c.JSON(http.StatusConflict, ErrorResponse{Code: "OPS_LLM_RUN_CONFLICT", Message: "The Ops LLM execution ID belongs to a different request."})
	}
	return respondOpsLLMInterpret(c, record.Result)
}

func respondOpsLLMInterpret(c echo.Context, result OpsLLMIntentResult) error {
	status := http.StatusBadGateway
	switch result.Status {
	case "completed":
		status = http.StatusOK
	case opsLLMUnconfirmed:
		status = http.StatusAccepted
	}
	return c.JSON(status, result)
}

func (h handler) opsLLMRunPath(id string) string {
	return filepath.Join(h.config.RepoRoot, "runs", "ops-llm", id+".json")
}

func readOpsLLMRun(path, id string) (opsLLMRunRecord, error) {
	var record opsLLMRunRecord
	raw, err := os.ReadFile(path)
	if err != nil {
		return record, err
	}
	if err := json.Unmarshal(raw, &record); err != nil {
		return record, err
	}
	if record.Result.RunID != id || len(record.RequestSHA256) != 64 || record.Result.Status == "" {
		return record, errors.New("invalid Ops LLM execution record")
	}
	return record, nil
}

func saveOpsLLMRun(path string, record opsLLMRunRecord, reserve bool) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(dir, ".ops-llm-*")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(file.Name()) }()
	err = json.NewEncoder(file).Encode(record)
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
	if reserve {
		return os.Link(file.Name(), path)
	}
	return os.Rename(file.Name(), path)
}
