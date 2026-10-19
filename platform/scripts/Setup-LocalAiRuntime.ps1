$ErrorActionPreference='Stop'
$Workspace=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Root=Join-Path $Workspace 'output\khu-platform-validation'
$Python=Join-Path $Root 'ai-venv\Scripts\python.exe'
if(-not (Test-Path -LiteralPath $Python)) {
    & python -m venv (Join-Path $Root 'ai-venv')
    if($LASTEXITCODE -ne 0){throw 'Dedicated Python environment creation failed'}
}
& $Python -m pip install --disable-pip-version-check torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
if($LASTEXITCODE -ne 0){throw 'CPU Torch installation failed'}
& $Python -m pip install --disable-pip-version-check transformers==4.46.2 tokenizers==0.20.3 numpy==2.2.6 safetensors==0.8.0 psutil==7.0.0 huggingface-hub==0.26.2
if($LASTEXITCODE -ne 0){throw 'Inference dependencies installation failed'}
& $Python -u (Join-Path $Workspace 'platform\runtime-app\bert_worker.py') --cache-dir (Join-Path $Root 'ai-models') --prefetch
if($LASTEXITCODE -ne 0){throw 'Pinned BERT Tiny download and load check failed'}
Write-Output 'Project CPU runtime ready. No AWS VM or lab server was used.'
