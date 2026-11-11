package model

import "kyunghee-aiops/service-control-api/internal/controlrun"

type PackageControlRunErrorResponse struct {
	ErrorResponse
	controlrun.Run
}
