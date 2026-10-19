GO ?= $(shell if command -v go >/dev/null 2>&1; then command -v go; elif [ -x /usr/local/go/bin/go ]; then echo /usr/local/go/bin/go; else echo go; fi)
GOLANGCI_LINT ?= $(shell if command -v golangci-lint >/dev/null 2>&1; then command -v golangci-lint; elif [ -x /home/geonhae/go/bin/golangci-lint ]; then echo /home/geonhae/go/bin/golangci-lint; else echo golangci-lint; fi)
GO_BIN_DIR := $(dir $(GO))
SWAG_VERSION ?= v1.16.6
LLMOP_API_DIR := go/service-control-api
LLMOP_PACKAGES := ./cmd/llmop-api ./cmd/ops-llm-benchmark ./internal/applicationprofile ./internal/benchmark ./internal/profileapi ./internal/llmclient ./internal/llmselection

.PHONY: build test vet lint swag license-report

build:
	mkdir -p build
	cd $(LLMOP_API_DIR) && $(GO) build -o ../../build/llmop-api ./cmd/llmop-api
	cd $(LLMOP_API_DIR) && $(GO) build -o ../../build/ops-llm-benchmark ./cmd/ops-llm-benchmark

test:
	cd $(LLMOP_API_DIR) && $(GO) test $(LLMOP_PACKAGES)

vet:
	cd $(LLMOP_API_DIR) && $(GO) vet $(LLMOP_PACKAGES)

lint:
	cd $(LLMOP_API_DIR) && PATH="$(GO_BIN_DIR):$$PATH" $(GOLANGCI_LINT) run $(LLMOP_PACKAGES)

swag:
	cd $(LLMOP_API_DIR) && $(GO) run github.com/swaggo/swag/cmd/swag@$(SWAG_VERSION) init \
		-d cmd/llmop-api,internal/profileapi,internal/applicationprofile,internal/llmselection \
		-g main.go \
		-o docs/swagger \
		--parseInternal --outputTypes json,yaml

license-report:
	: > docs/submission/go_module_inventory.txt
	cd $(LLMOP_API_DIR) && $(GO) list -m all >> ../../docs/submission/go_module_inventory.txt
