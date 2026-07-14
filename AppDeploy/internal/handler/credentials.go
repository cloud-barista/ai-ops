package handler

import (
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	credentialsvc "github.com/khu/ai-app-deployer/internal/credential"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/labstack/echo/v4"
)

const maxCredentialRequestBytes = 128 << 10

type credentialResponse struct {
	RequestID          string    `json:"request_id,omitempty"`
	CredentialID       string    `json:"credential_id"`
	CredentialRef      string    `json:"credential_ref"`
	CredentialType     string    `json:"credential_type"`
	SSHUser            string    `json:"ssh_user"`
	AuthType           string    `json:"auth_type"`
	HostKeyFingerprint string    `json:"host_key_fingerprint"`
	SSHTimeoutSeconds  int       `json:"ssh_timeout_seconds"`
	Persistent         bool      `json:"persistent"`
	CreatedAt          time.Time `json:"created_at"`
}

type credentialDeleteResponse struct {
	RequestID     string    `json:"request_id,omitempty"`
	CredentialID  string    `json:"credential_id"`
	CredentialRef string    `json:"credential_ref"`
	Deleted       bool      `json:"deleted"`
	DeletedAt     time.Time `json:"deleted_at"`
}

type credentialListResponse struct {
	RequestID string               `json:"request_id"`
	Items     []credentialResponse `json:"items"`
}

func (a *API) createCredential(c echo.Context) error {
	setCredentialResponseHeaders(c)
	if err := a.authorizeCredentialRequest(c); err != nil {
		return a.error(c, err)
	}
	if err := requireCredentialJSON(c.Request()); err != nil {
		return a.error(c, err)
	}

	c.Request().Body = http.MaxBytesReader(c.Response(), c.Request().Body, maxCredentialRequestBytes)
	decoder := json.NewDecoder(c.Request().Body)
	decoder.DisallowUnknownFields()
	var req credentialsvc.CreateRequest
	if err := decoder.Decode(&req); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			return a.error(c, credentialRequestTooLarge())
		}
		return a.error(c, invalidCredentialRequest())
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			return a.error(c, credentialRequestTooLarge())
		}
		return a.error(c, invalidCredentialRequest())
	}

	record, err := a.credentials.Create(c.Request().Context(), req)
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusCreated, credentialRecordResponse(requestID(c), record))
}

func (a *API) listCredentials(c echo.Context) error {
	setCredentialResponseHeaders(c)
	if err := a.authorizeCredentialRequest(c); err != nil {
		return a.error(c, err)
	}
	records, err := a.credentials.List(c.Request().Context())
	if err != nil {
		return a.error(c, err)
	}
	items := make([]credentialResponse, 0, len(records))
	for _, record := range records {
		items = append(items, credentialRecordResponse("", record))
	}
	return c.JSON(http.StatusOK, credentialListResponse{RequestID: requestID(c), Items: items})
}

func (a *API) deleteCredential(c echo.Context) error {
	setCredentialResponseHeaders(c)
	if err := a.authorizeCredentialRequest(c); err != nil {
		return a.error(c, err)
	}
	deleted, err := a.credentials.Delete(c.Request().Context(), c.Param("credential_id"))
	if err != nil {
		return a.error(c, err)
	}
	return c.JSON(http.StatusOK, credentialDeleteResponse{
		RequestID:     requestID(c),
		CredentialID:  deleted.CredentialID,
		CredentialRef: deleted.CredentialRef,
		Deleted:       deleted.Deleted,
		DeletedAt:     deleted.DeletedAt,
	})
}

func (a *API) authorizeCredentialRequest(c echo.Context) error {
	request := c.Request()
	if !sameOriginCredentialRequest(request) {
		return credentialAccessDenied()
	}
	if a.credentialRemote || (isLoopbackAddress(request.RemoteAddr) && isLoopbackHost(request.Host)) {
		return nil
	}
	return credentialAccessDenied()
}

func credentialAccessDenied() error {
	return apperrors.New(
		model.ErrGatewayAuthFailed,
		"credential management is available only from localhost",
		http.StatusForbidden,
		false,
	)
}

func requireCredentialJSON(request *http.Request) error {
	mediaType, _, err := mime.ParseMediaType(request.Header.Get(echo.HeaderContentType))
	if err != nil || !strings.EqualFold(mediaType, echo.MIMEApplicationJSON) {
		return invalidCredentialRequest()
	}
	return nil
}

func sameOriginCredentialRequest(request *http.Request) bool {
	origin := strings.TrimSpace(request.Header.Get("Origin"))
	if origin == "" {
		return true
	}
	parsed, err := url.Parse(origin)
	if err != nil || parsed.Scheme == "" || parsed.User != nil || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return false
	}
	return strings.EqualFold(strings.TrimSuffix(parsed.Host, "."), strings.TrimSuffix(strings.TrimSpace(request.Host), "."))
}

func setCredentialResponseHeaders(c echo.Context) {
	c.Response().Header().Set(echo.HeaderCacheControl, "no-store")
	c.Response().Header().Set("Pragma", "no-cache")
}

func isLoopbackAddress(remoteAddress string) bool {
	host := addressHost(remoteAddress)
	if zone := strings.LastIndex(host, "%"); zone >= 0 {
		host = host[:zone]
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func isLoopbackHost(hostPort string) bool {
	host := addressHost(hostPort)
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func addressHost(hostPort string) string {
	hostPort = strings.TrimSpace(hostPort)
	host, _, err := net.SplitHostPort(hostPort)
	if err == nil {
		return strings.Trim(host, "[]")
	}
	return strings.Trim(hostPort, "[]")
}

func credentialRecordResponse(requestID string, record credentialsvc.Record) credentialResponse {
	return credentialResponse{
		RequestID:          requestID,
		CredentialID:       record.CredentialID,
		CredentialRef:      record.CredentialRef,
		CredentialType:     record.CredentialType,
		SSHUser:            record.SSHUser,
		AuthType:           record.AuthType,
		HostKeyFingerprint: record.HostKeyFingerprint,
		SSHTimeoutSeconds:  record.SSHTimeoutSeconds,
		Persistent:         record.Persistent,
		CreatedAt:          record.CreatedAt,
	}
}

func invalidCredentialRequest() error {
	return apperrors.New(model.ErrTargetProfileInvalid, "invalid credential request body", http.StatusBadRequest, false)
}

func credentialRequestTooLarge() error {
	return apperrors.New(model.ErrTargetProfileInvalid, "credential request body exceeds the allowed size", http.StatusRequestEntityTooLarge, false)
}
