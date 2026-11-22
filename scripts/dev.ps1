[CmdletBinding()]
param(
  [switch] $NoBrowser,
  [switch] $NoAuth
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
. (Join-Path $PSScriptRoot "dev-runtime.ps1")
$apiRoot = Join-Path $repoRoot "apps\api"
$webRoot = Join-Path $repoRoot "apps\web"
$apiPython = Join-Path $apiRoot ".venv\Scripts\python.exe"
$apiSource = Join-Path $apiRoot "src"
$viteEntry = Join-Path $webRoot "node_modules\vite\bin\vite.js"

if (-not (Test-Path -LiteralPath $apiPython -PathType Leaf)) {
  throw "API virtual environment is missing: $apiPython"
}
if (-not (Test-Path -LiteralPath $viteEntry -PathType Leaf)) {
  throw "Web dependencies are missing: run npm.cmd install --prefix apps/web"
}

$node = (Get-Command node.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {
  throw "Node.js executable is missing: $node"
}

$runtime = Resolve-AiCanvasDevelopmentRuntime `
  -RepoRoot $repoRoot `
  -NoAuth:$NoAuth `
  -NoBrowser:$NoBrowser `
  -ApiPort 8765 `
  -VitePort 5173 `
  -BootstrapFactory {
    $tokenBytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
      $random.GetBytes($tokenBytes)
    } finally {
      $random.Dispose()
    }
    [Convert]::ToBase64String($tokenBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  }
$developmentData = $runtime.DataDirectory
$instanceId = $runtime.ApiEnvironment["AI_CANVAS_INSTANCE_ID"]

function Wait-ChildEndpoint(
  [string] $Url,
  [System.Diagnostics.Process] $Process,
  [ScriptBlock] $Matches,
  [int] $Seconds = 15
) {
  $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    $Process.Refresh()
    if ($Process.HasExited) {
      throw "Child process $($Process.Id) exited before owning $Url"
    }

    $response = $null
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 1
    } catch {
      $response = $null
    }

    $Process.Refresh()
    if ($Process.HasExited) {
      throw "Child process $($Process.Id) exited while probing $Url"
    }
    if ($null -ne $response -and $response.StatusCode -eq 200 -and (& $Matches $response)) {
      return
    }
    Start-Sleep -Milliseconds 150
  }
  throw "Timed out verifying child process $($Process.Id) at $Url"
}

$apiProcess = $null
$webProcess = $null
$initialAiCanvasEnvironment = Get-AiCanvasProcessEnvironmentSnapshot

try {
  try {
    New-Item -ItemType Directory -Force -Path $developmentData | Out-Null
    Set-AiCanvasApiProcessEnvironment `
      -Runtime $runtime `
      -InitialEnvironment $initialAiCanvasEnvironment

    $apiProcess = Start-Process -FilePath $apiPython -ArgumentList @(
      "-m", "uvicorn", "ai_canvas.main:app",
      "--app-dir", "`"$apiSource`"",
      "--host", "127.0.0.1", "--port", "8765", "--workers", "1"
    ) -WorkingDirectory $apiRoot -WindowStyle Hidden -PassThru
    Set-AiCanvasViteProcessEnvironment -Runtime $runtime
    $webProcess = Start-Process -FilePath $node -ArgumentList @(
      "`"$viteEntry`"", "--host", "127.0.0.1", "--port", "5173", "--strictPort"
    ) -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru
  } finally {
    Restore-AiCanvasProcessEnvironment -Snapshot $initialAiCanvasEnvironment
  }

  Wait-ChildEndpoint "$($runtime.ApiOrigin)/api/v1/health" $apiProcess {
    param($response)
    $response.Headers["X-AI-Canvas-Instance"] -eq $instanceId
  }
  Wait-ChildEndpoint "$($runtime.WebOrigin)/__ai_canvas_instance" $webProcess {
    param($response)
    $response.Content.Trim() -eq $instanceId
  }

  if ($null -ne $runtime.Warning) {
    Write-Warning "============================================================"
    Write-Warning $runtime.Warning
    Write-Warning "Data directory: $($runtime.DataDirectory)"
    Write-Warning "Local URL: $($runtime.LaunchUrl)"
    Write-Warning "============================================================"
  }
  if ($runtime.OpenBrowser) {
    Start-Process -FilePath $runtime.LaunchUrl
  }
  Write-Host "AI Canvas is running. Press Ctrl+C to stop it."

  while (-not $apiProcess.HasExited -and -not $webProcess.HasExited) {
    Start-Sleep -Seconds 1
    $apiProcess.Refresh()
    $webProcess.Refresh()
  }
  throw "A development server exited unexpectedly."
} finally {
  foreach ($process in @($webProcess, $apiProcess)) {
    if ($null -ne $process) {
      $process.Refresh()
      if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
      }
    }
  }
}
