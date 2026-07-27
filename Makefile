GO ?= $(shell if command -v go >/dev/null 2>&1; then command -v go; elif [ -x /usr/local/go/bin/go ]; then echo /usr/local/go/bin/go; else echo go; fi)
GOLANGCI_LINT ?= $(shell if command -v golangci-lint >/dev/null 2>&1; then command -v golangci-lint; elif [ -x /home/geonhae/go/bin/golangci-lint ]; then echo /home/geonhae/go/bin/golangci-lint; else echo golangci-lint; fi)
GO_BIN_DIR := $(dir $(GO))
SWAG_VERSION ?= v1.16.6
SERVICE_CONTROL_DIR := go/service-control-api

.PHONY: test vet lint swag license-report

test:
	cd go/aiops-guard && $(GO) test ./...
	cd $(SERVICE_CONTROL_DIR) && $(GO) test ./...

vet:
	cd go/aiops-guard && $(GO) vet ./...
	cd $(SERVICE_CONTROL_DIR) && $(GO) vet ./...

lint:
	cd go/aiops-guard && PATH="$(GO_BIN_DIR):$$PATH" $(GOLANGCI_LINT) run ./...
	cd $(SERVICE_CONTROL_DIR) && PATH="$(GO_BIN_DIR):$$PATH" $(GOLANGCI_LINT) run ./...

swag:
	cd $(SERVICE_CONTROL_DIR) && $(GO) run github.com/swaggo/swag/cmd/swag@$(SWAG_VERSION) init \
		-d cmd/service-control-api,internal/api,internal/controlrun,internal/appdeploy,internal/deploymentplanner,internal/plannerguard \
		-g main.go \
		-o docs/swagger \
		--parseInternal --outputTypes json,yaml

license-report:
	: > docs/submission/go_module_inventory.txt
	cd $(SERVICE_CONTROL_DIR) && $(GO) list -m all >> ../../docs/submission/go_module_inventory.txt
	cd go/aiops-guard && $(GO) list -m all >> ../../docs/submission/go_module_inventory.txt

