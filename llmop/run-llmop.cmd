@echo off
setlocal

set "AIOPS_REPO_ROOT=%~dp0"
if not defined AIOPS_BIND_ADDRESS set "AIOPS_BIND_ADDRESS=127.0.0.1"
if not defined AIOPS_LLMOP_CANDIDATE_ID set "AIOPS_LLMOP_CANDIDATE_ID=qwen3.5-profile-generator"
if not defined AIOPS_LLMOP_MAX_LLM_ATTEMPTS set "AIOPS_LLMOP_MAX_LLM_ATTEMPTS=2"
if not defined PORT set "PORT=18080"

where go >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Go was not found. Install Go 1.25 or later and restart the terminal.
  exit /b 1
)

cd /d "%AIOPS_REPO_ROOT%go\service-control-api"
if errorlevel 1 (
  echo [ERROR] go\service-control-api was not found under %AIOPS_REPO_ROOT%
  exit /b 1
)

echo Starting LLM_Op ApplicationProfile and model-selection API at http://%AIOPS_BIND_ADDRESS%:%PORT%/
echo ApplicationProfile candidate: %AIOPS_LLMOP_CANDIDATE_ID%
go run ./cmd/llmop-api
