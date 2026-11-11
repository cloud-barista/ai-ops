[CmdletBinding()]
param(
    [string]$CatalogPath = '',
    [string]$ScenariosPath = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($CatalogPath)) {
    $CatalogPath = Join-Path $PSScriptRoot '..\examples\llm-op\ai-service-catalog.json'
}
if ([string]::IsNullOrWhiteSpace($ScenariosPath)) {
    $ScenariosPath = Join-Path $PSScriptRoot '..\examples\llm-op\user-input-scenarios.json'
}

function Assert-Contract {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Read-JsonContract {
    param([string]$Path)
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $raw = Get-Content -Raw -Encoding utf8 -LiteralPath $resolved
    Assert-Contract ($raw.Length -le 1048576) "JSON contract exceeds the 1 MiB static validation envelope: $resolved"
    return [pscustomobject]@{
        Path = $resolved
        Raw = $raw
        Value = ($raw | ConvertFrom-Json)
    }
}

$catalogDocument = Read-JsonContract $CatalogPath
$scenarioDocument = Read-JsonContract $ScenariosPath
$catalog = $catalogDocument.Value
$scenarioCatalog = $scenarioDocument.Value

Assert-Contract ($catalog.schema_version -eq 'ai-ops.llm-op-demo-catalog/v1') 'Unexpected demo catalog schema_version'
Assert-Contract ($catalog.execution_policy.mode -eq 'offline_fixture_only') 'Demo execution mode must be offline_fixture_only'
Assert-Contract (-not $catalog.execution_policy.network_allowed) 'Demo catalog must disable network access'
Assert-Contract (-not $catalog.execution_policy.weights_loaded) 'Demo catalog must not claim loaded model weights'
Assert-Contract (-not $catalog.execution_policy.model_selection_enabled) 'Demo catalog must disable model selection'
Assert-Contract (-not $catalog.execution_policy.appdeploy_submit_enabled) 'Demo catalog must disable AppDeploy submission'
Assert-Contract ($catalogDocument.Raw -notmatch '"(?:endpoint|api_key|api_key_env)"\s*:') 'Demo catalog must not contain endpoint or API-key fields'

$bindings = @($catalog.planner_model_bindings)
Assert-Contract ($bindings.Count -eq 1) 'Offline demo must contain exactly one caller-pinned planner binding'
$candidateIds = @{}
foreach ($binding in $bindings) {
    Assert-Contract (-not [string]::IsNullOrWhiteSpace($binding.candidate_id)) 'Planner candidate_id is required'
    Assert-Contract (-not $candidateIds.ContainsKey($binding.candidate_id)) "Duplicate planner candidate_id: $($binding.candidate_id)"
    $candidateIds[$binding.candidate_id] = $true
    Assert-Contract ($binding.binding_mode -eq 'caller_pinned') 'Planner binding_mode must be caller_pinned'
    Assert-Contract ($binding.provider -eq 'offline-fixture') 'Planner provider must be offline-fixture'
    Assert-Contract ($binding.actual_model -eq 'fixture-qwen-contract-not-executed') 'Evidence actual_model must identify the non-executed fixture'
    Assert-Contract ($binding.intended_model -eq 'qwen3.5:4b') 'The demo intended_model must be the explicitly pinned Qwen contract label'
    Assert-Contract ($binding.initial_preference_weight -eq 1.0) 'Initial preference weight must be 1.0 metadata'
    Assert-Contract ($binding.weight_semantics -eq 'metadata_only_no_selection') 'Weight must be explicitly non-selecting metadata'
    Assert-Contract ($binding.json_mode) 'Planner fixture must use the JSON contract'
    Assert-Contract (-not $binding.network_allowed) 'Planner binding must disable network access'
    Assert-Contract (-not $binding.weights_loaded) 'Planner binding must not claim loaded weights'
    Assert-Contract ($binding.benchmark_status -eq 'not_executed') 'Planner fixture benchmark_status must be not_executed'
}

$services = @($catalog.ai_services)
Assert-Contract ($services.Count -ge 4) 'At least four AI service examples are required'
$serviceIds = @{}
$appVersionIds = @{}
foreach ($service in $services) {
    Assert-Contract (-not [string]::IsNullOrWhiteSpace($service.service_id)) 'AI service_id is required'
    Assert-Contract (-not $serviceIds.ContainsKey($service.service_id)) "Duplicate AI service_id: $($service.service_id)"
    Assert-Contract (-not $appVersionIds.ContainsKey($service.app_version_id)) "Duplicate AI service app_version_id: $($service.app_version_id)"
    $serviceIds[$service.service_id] = $true
    $appVersionIds[$service.app_version_id] = $true
    Assert-Contract ($service.lifecycle_status -eq 'demo_catalog_only') "AI service must be demo_catalog_only: $($service.service_id)"
    Assert-Contract (-not $service.network_endpoint_ready) "AI service must not claim an endpoint: $($service.service_id)"
    Assert-Contract (-not $service.weights_loaded) "AI service must not claim loaded weights: $($service.service_id)"
    foreach ($field in @('display_name', 'task', 'description', 'app_version_id', 'workload_model_ref', 'input_media_type', 'output_media_type')) {
        Assert-Contract (-not [string]::IsNullOrWhiteSpace($service.$field)) "AI service field $field is required: $($service.service_id)"
    }
    Assert-Contract ($service.default_resources.cpu -match '^[1-9][0-9]*$') "Invalid CPU resource: $($service.service_id)"
    Assert-Contract ($service.default_resources.gpu -match '^(0|[1-9][0-9]*)$') "Invalid GPU resource: $($service.service_id)"
    Assert-Contract ($service.default_resources.memory -match '^[1-9][0-9]*(Mi|Gi|Ti)$') "Invalid memory resource: $($service.service_id)"
    Assert-Contract ($service.default_resources.storage -match '^[1-9][0-9]*(Mi|Gi|Ti)$') "Invalid storage resource: $($service.service_id)"
    if ([int]$service.default_resources.gpu -eq 0) {
        Assert-Contract ($service.default_resources.accelerator -eq 'none') "CPU service must use accelerator=none: $($service.service_id)"
    }
    else {
        Assert-Contract ($service.default_resources.accelerator -eq 'nvidia') "GPU service must use accelerator=nvidia: $($service.service_id)"
    }
}

Assert-Contract ($scenarioCatalog.schema_version -eq 'ai-ops.llm-op-demo-scenarios/v1') 'Unexpected scenario schema_version'
Assert-Contract ($scenarioCatalog.validation_status -eq 'static_contract_catalog_not_executed') 'Scenario evidence status must remain explicit'
$scenarios = @($scenarioCatalog.scenarios)
Assert-Contract ($scenarios.Count -ge 24) 'At least 24 user-input scenarios are required'
$scenarioIds = @{}
$allowedCategories = @(
    'success',
    'success_with_excluded_context',
    'clarification',
    'security_rejection',
    'responsibility_rejection',
    'grammar_rejection',
    'contract_rejection',
    'normalization_rejection',
    'semantic_rejection',
    'proposal_contract_rejection',
    'model_output_rejection',
    'bridge_success',
    'bridge_rejection'
)
$allowedStages = @(
    'request_guard',
    'operation_context_normalization',
    'safeguard_llm',
    'safeguard_output_parser',
    'proposal_output_parser',
    'proposal_contract_guard',
    'proposal_semantic_guard',
    'common_json_bridge',
    'handoff'
)
$allowedStatuses = @(
    'HANDOFF_READY',
    'CLARIFICATION_REQUIRED',
    'REQUEST_REJECTED',
    'MANIFEST_REJECTED',
    'MODEL_UNAVAILABLE',
    'NO_LLMOP_RESULT'
)
foreach ($scenario in $scenarios) {
    Assert-Contract (-not [string]::IsNullOrWhiteSpace($scenario.id)) 'Scenario id is required'
    Assert-Contract (-not $scenarioIds.ContainsKey($scenario.id)) "Duplicate scenario id: $($scenario.id)"
    $scenarioIds[$scenario.id] = $true
    Assert-Contract ($allowedCategories -contains $scenario.category) "Invalid scenario category: $($scenario.id)"
    Assert-Contract ($serviceIds.ContainsKey($scenario.service_id)) "Unknown scenario service_id: $($scenario.id)"
    Assert-Contract ($candidateIds.ContainsKey($scenario.planner_candidate_id)) "Unknown planner_candidate_id: $($scenario.id)"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace($scenario.user_request)) "Scenario user_request is required: $($scenario.id)"
    Assert-Contract ($scenario.user_request.Length -le 8000) "Scenario user_request exceeds 8,000 characters: $($scenario.id)"
    Assert-Contract (-not [string]::IsNullOrWhiteSpace($scenario.context_profile)) "Scenario context_profile is required: $($scenario.id)"
    Assert-Contract ($allowedStages -contains $scenario.expected.stage) "Invalid expected stage: $($scenario.id)"
    Assert-Contract ($allowedStatuses -contains $scenario.expected.status) "Invalid expected status: $($scenario.id)"
    Assert-Contract ($scenario.expected.safeguard_calls -in 0, 1) "Invalid safeguard call count: $($scenario.id)"
    Assert-Contract ($scenario.expected.proposal_calls -in 0, 1) "Invalid proposal call count: $($scenario.id)"
    Assert-Contract ($scenario.expected.proposal_calls -le $scenario.expected.safeguard_calls) "Proposal cannot run without Safeguard: $($scenario.id)"
    if ($scenario.expected.stage -eq 'request_guard') {
        Assert-Contract (-not [string]::IsNullOrWhiteSpace($scenario.expected.guard_check)) "Request Guard scenario needs guard_check: $($scenario.id)"
        Assert-Contract ($scenario.expected.safeguard_calls -eq 0) "Request Guard rejection must make zero model calls: $($scenario.id)"
    }
    if ($scenario.expected.stage -eq 'proposal_semantic_guard') {
        Assert-Contract (-not [string]::IsNullOrWhiteSpace($scenario.expected.semantic_guard_code)) "Semantic scenario needs internal guard code: $($scenario.id)"
    }
    if ($scenario.expected.stage -eq 'common_json_bridge') {
        Assert-Contract (-not [string]::IsNullOrWhiteSpace($scenario.expected.bridge_error_contains)) "Bridge scenario needs an error substring: $($scenario.id)"
    }
    if ($scenario.expected.status -eq 'HANDOFF_READY') {
        Assert-Contract ($scenario.expected.stage -eq 'handoff') "HANDOFF_READY must terminate at handoff: $($scenario.id)"
    }
}

Write-Output "Validated offline LLM_Op demo data: $($services.Count) services, $($bindings.Count) caller-pinned model binding, $($scenarios.Count) scenarios."
