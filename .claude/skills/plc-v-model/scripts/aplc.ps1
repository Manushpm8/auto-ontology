$ErrorActionPreference = "Continue"

function Warn-Skip([string]$Reason) {
    Write-Warning "Agentic PLC aplc shim skipped: $Reason"
}

$Candidates = @()
$Candidates += Join-Path (Get-Location).Path ".plc\tools\python-venv\Scripts\aplc.cmd"
if ($env:USERPROFILE) {
    $Candidates += Join-Path $env:USERPROFILE ".agentic-plc\tools\python-venv\Scripts\aplc.cmd"
}
if ($env:HOME) {
    $Candidates += Join-Path $env:HOME ".agentic-plc\tools\python-venv\Scripts\aplc.cmd"
}

$Aplc = $null
foreach ($Candidate in $Candidates) {
    if ($Candidate -and (Test-Path $Candidate)) {
        $Aplc = $Candidate
        break
    }
}

if (-not $Aplc) {
    Warn-Skip "aplc CLI not found"
    exit 0
}

if ($MyInvocation.ExpectingInput) {
    $InputText = [Console]::In.ReadToEnd()
    if ($InputText.Length -gt 0) {
        $InputText | & $Aplc @args
    } else {
        & $Aplc @args
    }
} else {
    & $Aplc @args
}

exit $LASTEXITCODE
