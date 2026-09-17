$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendUrl = "http://127.0.0.1:8000/health"
$webUrl = "http://127.0.0.1:3000"

function Start-CortexProcess {
  param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Command
  )

  Start-Process powershell.exe -WindowStyle Normal -WorkingDirectory $repoRoot -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
  )
}

function Wait-ForUrl {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Url,
    [int]$TimeoutSeconds = 45
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
      Write-Host "$Name is ready: $Url"
      return
    } catch {
      Start-Sleep -Seconds 1
    }
  }

  throw "$Name did not become ready at $Url within $TimeoutSeconds seconds."
}

Write-Host "Starting Cortex backend and web..."
Start-CortexProcess -Title "Cortex Backend" -Command "npm run dev:backend"
Start-CortexProcess -Title "Cortex Web" -Command "npm run dev:web"

Wait-ForUrl -Name "Backend" -Url $backendUrl
Wait-ForUrl -Name "Web" -Url $webUrl

Write-Host "Opening Cortex desktop app..."
npm run dev:electron
