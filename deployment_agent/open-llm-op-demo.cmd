@echo off
setlocal

set "AIOPS_LLM_OP_DEMO=%~dp0go\service-control-api\internal\webui\static\llm_op_demo.html"

if not exist "%AIOPS_LLM_OP_DEMO%" (
  echo [ERROR] LLM_Op demo page was not found.
  echo Switch to the LLM_Op branch and try again.
  exit /b 1
)

echo Opening the server-free LLM_Op demo in the default browser.
echo No Go server, model API, or AppDeploy endpoint will be started.
start "" "%AIOPS_LLM_OP_DEMO%"
