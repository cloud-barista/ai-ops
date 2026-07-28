package appdeploy

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"strconv"
	"strings"
)

func (client Client) BuildPackage(ctx context.Context, upload PackageUpload) (PackageBuildResponse, error) {
	var response PackageBuildResponse
	if upload.Source == nil {
		return response, fmt.Errorf("package source is required")
	}
	if strings.TrimSpace(upload.Filename) == "" {
		return response, fmt.Errorf("package filename is required")
	}

	err := client.doMultipart(ctx, "/artifacts/packages", upload, &response)
	if err != nil {
		return response, err
	}
	if len(strings.TrimSpace(string(response.AppSpec))) == 0 {
		return response, fmt.Errorf("AppDeploy response did not contain app_spec")
	}
	return response, nil
}

func (client Client) RegisterApp(ctx context.Context, appSpec json.RawMessage) (AppRegistrationResponse, error) {
	var response AppRegistrationResponse
	if len(strings.TrimSpace(string(appSpec))) == 0 {
		return response, fmt.Errorf("app_spec is required")
	}
	payload := struct {
		AppSpec json.RawMessage `json:"app_spec"`
	}{AppSpec: appSpec}
	if err := client.doJSON(ctx, http.MethodPost, "/apps", payload, &response); err != nil {
		return response, err
	}
	if strings.TrimSpace(response.AppID) == "" {
		return response, fmt.Errorf("AppDeploy response did not contain app_id")
	}
	if strings.TrimSpace(response.AppVersionID) == "" {
		return response, fmt.Errorf("AppDeploy response did not contain app_version_id")
	}
	return response, nil
}

func (client Client) doMultipart(ctx context.Context, path string, upload PackageUpload, output any) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	reader, writer := io.Pipe()
	multipartWriter := multipart.NewWriter(writer)
	writeErr := make(chan error, 1)
	go func() {
		err := writePackageUpload(multipartWriter, upload)
		if closeErr := multipartWriter.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			_ = writer.CloseWithError(err)
		} else {
			_ = writer.Close()
		}
		writeErr <- err
	}()

	endpoint := *client.baseURL
	endpoint.Path = strings.TrimRight(client.baseURL.Path, "/") + path
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), reader)
	if err != nil {
		_ = reader.Close()
		return err
	}
	request.Header.Set("accept", "application/json")
	request.Header.Set("content-type", multipartWriter.FormDataContentType())

	response, err := client.httpClient.Do(request)
	if err != nil {
		_ = reader.Close()
		return err
	}
	defer func() {
		_ = response.Body.Close()
	}()
	content, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
	if err != nil {
		return err
	}
	if err := <-writeErr; err != nil {
		return err
	}
	if len(content) > maxResponseBytes {
		return fmt.Errorf("AppDeploy response exceeds %d bytes", maxResponseBytes)
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return decodeAPIError(response.StatusCode, content)
	}
	if err := json.Unmarshal(content, output); err != nil {
		return fmt.Errorf("decode AppDeploy response: %w", err)
	}
	return nil
}

func writePackageUpload(writer *multipart.Writer, upload PackageUpload) error {
	fields := map[string]string{
		"package_type":     upload.PackageType,
		"app_name":         upload.AppName,
		"app_version":      upload.AppVersion,
		"entrypoint":       upload.Entrypoint,
		"runtime_type":     upload.RuntimeType,
		"service_port":     strconv.Itoa(upload.ServicePort),
		"healthcheck_path": upload.HealthcheckPath,
	}
	for name, value := range fields {
		if err := writer.WriteField(name, value); err != nil {
			return err
		}
	}
	part, err := writer.CreateFormFile("source", upload.Filename)
	if err != nil {
		return err
	}
	_, err = io.Copy(part, upload.Source)
	return err
}
