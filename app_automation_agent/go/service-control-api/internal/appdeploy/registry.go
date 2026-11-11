package appdeploy

import (
	"context"
	"net/http"
)

func (client Client) ListApps(ctx context.Context) ([]AppRegistrationResponse, error) {
	var response struct {
		Apps []AppRegistrationResponse `json:"apps"`
	}
	err := client.doJSON(ctx, http.MethodGet, "/apps", nil, &response)
	return response.Apps, err
}
