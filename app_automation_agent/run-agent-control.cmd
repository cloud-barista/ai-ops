@echo off
setlocal

set "AIOPS_REPO_ROOT=%~dp0"
if not defined AIOPS_BIND_ADDRESS set "AIOPS_BIND_ADDRESS=127.0.0.1"
if not defined AIOPS_DEPLOYMENT_ADAPTER set "AIOPS_DEPLOYMENT_ADAPTER=mock"
if not defined AIOPS_REQUIREMENT_ANALYSIS_MODE set "AIOPS_REQUIREMENT_ANALYSIS_MODE=local_rule"
if not defined AIOPS_LLM_CANDIDATES_PATH set "AIOPS_LLM_CANDIDATES_PATH=config/ops_llm_eval_candidates.local_ollama.json"
if not defined AIOPS_LLMOP_ALLOW_LIVE_COMPLETION set "AIOPS_LLMOP_ALLOW_LIVE_COMPLETION=true"
if not defined AIOPS_LEGACY_API_ENABLED set "AIOPS_LEGACY_API_ENABLED=false"
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

echo Starting geon deployment-agent at http://%AIOPS_BIND_ADDRESS%:%PORT%/
echo Deployment adapter: %AIOPS_DEPLOYMENT_ADAPTER%
echo Requirement analysis: %AIOPS_REQUIREMENT_ANALYSIS_MODE%
echo LLM_Op Safeguard: Qwen via Ollama
echo Input: ApplicationProfile + ResourceRecommendation
echo Legacy all-in-one API: %AIOPS_LEGACY_API_ENABLED%
go run ./cmd/service-control-api
