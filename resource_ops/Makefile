GO ?= go
SWAG_VERSION ?= v1.16.6

.PHONY: test vet swag

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

swag:
	$(GO) run github.com/swaggo/swag/cmd/swag@$(SWAG_VERSION) init \
		-d cmd/server,internal/api,internal/resource -g main.go \
		-o docs/swagger --parseInternal --outputTypes json,yaml
