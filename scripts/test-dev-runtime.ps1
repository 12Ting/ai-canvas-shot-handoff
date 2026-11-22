$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot ".."))
$helperPath = Join-Path $scriptRoot "dev-runtime.ps1"

function Assert-Equal($Expected, $Actual, [string] $Message) {
  if ($Expected -ne $Actual) {
    throw "$Message (expected '$Expected', got '$Actual')"
  }
}

function Assert-True([bool] $Condition, [string] $Message) {
  if (-not $Condition) {
    throw $Message
  }
}

function Assert-Null($Actual, [string] $Message) {
  if ($null -ne $Actual) {
    throw "$Message (got '$Actual')"
  }
}

function Get-TestAiCanvasEnvironment {
  $result = @{}
  $variables = [Environment]::GetEnvironmentVariables("Process")
  foreach ($key in @($variables.Keys)) {
    $name = [string] $key
    if ($name.StartsWith("AI_CANVAS_", [StringComparison]::OrdinalIgnoreCase)) {
      $result[$name] = [string] $variables[$key]
    }
  }
  $result
}

function Restore-TestAiCanvasEnvironment([System.Collections.IDictionary] $Snapshot) {
  $variables = [Environment]::GetEnvironmentVariables("Process")
  foreach ($key in @($variables.Keys)) {
    $name = [string] $key
    if ($name.StartsWith("AI_CANVAS_", [StringComparison]::OrdinalIgnoreCase)) {
      [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
  }
  foreach ($entry in $Snapshot.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable(
      [string] $entry.Key,
      [string] $entry.Value,
      "Process"
    )
  }
}

function Assert-EnvironmentEqual(
  [System.Collections.IDictionary] $Expected,
  [System.Collections.IDictionary] $Actual,
  [string] $Message
) {
  $expectedNames = @($Expected.Keys | ForEach-Object { [string] $_ } | Sort-Object)
  $actualNames = @($Actual.Keys | ForEach-Object { [string] $_ } | Sort-Object)
  Assert-Equal ($expectedNames -join "|") ($actualNames -join "|") "$Message names"
  foreach ($name in $expectedNames) {
    Assert-Equal $Expected[$name] $Actual[$name] "$Message value for $name"
  }
}

function Read-ChildAiCanvasEnvironment {
  $probeScript = @'
$variables = [Environment]::GetEnvironmentVariables("Process")
foreach ($key in @($variables.Keys) | Sort-Object) {
  $name = [string] $key
  if ($name.StartsWith("AI_CANVAS_", [StringComparison]::OrdinalIgnoreCase)) {
    "{0}={1}" -f $name, [string] $variables[$key]
  }
}
'@
  $encodedProbe = [Convert]::ToBase64String(
    [Text.Encoding]::Unicode.GetBytes($probeScript)
  )
  $lines = @(
    & powershell.exe `
      -NoLogo `
      -NoProfile `
      -NonInteractive `
      -OutputFormat Text `
      -EncodedCommand $encodedProbe 2>$null
  )
  if ($LASTEXITCODE -ne 0) {
    throw "Environment probe child failed with exit code $LASTEXITCODE"
  }
  $result = @{}
  foreach ($line in $lines) {
    $parts = [string] $line -split "=", 2
    if ($parts.Count -ne 2) {
      throw "Environment probe child returned an invalid line"
    }
    $result[$parts[0]] = $parts[1]
  }
  $result
}

. $helperPath

foreach ($functionName in @(
  "Get-AiCanvasProcessEnvironmentSnapshot",
  "Set-AiCanvasApiProcessEnvironment",
  "Set-AiCanvasViteProcessEnvironment",
  "Restore-AiCanvasProcessEnvironment"
)) {
  Assert-True ($null -ne (Get-Command $functionName -ErrorAction SilentlyContinue)) "Missing environment phase function $functionName"
}

$normalCalls = [pscustomobject]@{ Count = 0 }
$normal = Resolve-AiCanvasDevelopmentRuntime `
  -RepoRoot $repoRoot `
  -BootstrapFactory {
    $normalCalls.Count += 1
    "normal-bootstrap"
  }

Assert-Equal 1 $normalCalls.Count "Normal mode must create one bootstrap token"
Assert-Equal "session" $normal.Mode "Normal mode must use session authentication"
Assert-Equal "development" $normal.RuntimeProfile "Normal mode must be development"
Assert-Equal "session" $normal.AuthMode "Normal auth mode must be session"
Assert-Equal (Join-Path $repoRoot ".local\dev") $normal.DataDirectory "Normal data must stay isolated"
Assert-Equal "http://127.0.0.1:5173/#bootstrap=normal-bootstrap" $normal.LaunchUrl "Normal launch URL must carry the fragment"
Assert-True $normal.OpenBrowser "Normal mode must open the browser by default"
Assert-Null $normal.Warning "Normal mode must not display the no-auth warning"
Assert-Equal "normal-bootstrap" $normal.ApiEnvironment.AI_CANVAS_BOOTSTRAP_TOKEN "API must receive the bootstrap token"

$noAuthCalls = [pscustomobject]@{ Count = 0 }
$noAuth = Resolve-AiCanvasDevelopmentRuntime `
  -RepoRoot $repoRoot `
  -NoAuth `
  -NoBrowser `
  -BootstrapFactory {
    $noAuthCalls.Count += 1
    "must-not-be-created"
  }

Assert-Equal 0 $noAuthCalls.Count "Automatic mode must never create a bootstrap token"
Assert-Equal "development-auto-session" $noAuth.Mode "Automatic mode must be explicit"
Assert-Equal "development" $noAuth.RuntimeProfile "Automatic mode must be development-only"
Assert-Equal "development-auto-session" $noAuth.AuthMode "Automatic auth mode must be explicit"
Assert-Equal (Join-Path $repoRoot ".local\dev-noauth") $noAuth.DataDirectory "Automatic-mode data must stay isolated"
Assert-Equal "http://127.0.0.1:5173/" $noAuth.LaunchUrl "Automatic mode must use a naked loopback URL"
Assert-True (-not $noAuth.OpenBrowser) "-NoBrowser must suppress browser launch"
Assert-True ($noAuth.Warning -match "risk") "Automatic mode must return a prominent risk warning"
Assert-True (-not $noAuth.ApiEnvironment.Contains("AI_CANVAS_BOOTSTRAP_TOKEN")) "Automatic mode must omit the bootstrap token"

foreach ($plan in @($normal, $noAuth)) {
  Assert-Equal "http://127.0.0.1:5173" $plan.ApiEnvironment.AI_CANVAS_PUBLIC_ORIGIN "API public origin must be exact"
  Assert-Equal '["http://127.0.0.1:5173"]' $plan.ApiEnvironment.AI_CANVAS_DEVELOPMENT_ORIGINS "API development origins must be an exact JSON tuple"
  Assert-Equal '["127.0.0.1"]' $plan.ApiEnvironment.AI_CANVAS_ALLOWED_HOSTS "API hosts must be exact loopback JSON"
  Assert-Equal $plan.DataDirectory $plan.ApiEnvironment.AI_CANVAS_DATA_DIR "API data directory must match the plan"
  Assert-Equal "development" $plan.ApiEnvironment.AI_CANVAS_RUNTIME_PROFILE "API runtime profile must be explicit"
  Assert-Equal $plan.AuthMode $plan.ApiEnvironment.AI_CANVAS_AUTH_MODE "API auth mode must match the plan"
  Assert-True (-not [string]::IsNullOrWhiteSpace($plan.ApiEnvironment.AI_CANVAS_INSTANCE_ID)) "API must receive an instance id"
  Assert-Equal $plan.ApiEnvironment.AI_CANVAS_INSTANCE_ID $plan.ViteEnvironment.AI_CANVAS_INSTANCE_ID "API and Vite must share the instance id"
  Assert-Equal "8765" $plan.ViteEnvironment.AI_CANVAS_E2E_API_PORT "Vite must receive only the API proxy port"

  $expectedApiOnly = @(
    "AI_CANVAS_BOOTSTRAP_TOKEN",
    "AI_CANVAS_PUBLIC_ORIGIN",
    "AI_CANVAS_DATA_DIR",
    "AI_CANVAS_RUNTIME_PROFILE",
    "AI_CANVAS_AUTH_MODE",
    "AI_CANVAS_DEVELOPMENT_ORIGINS",
    "AI_CANVAS_ALLOWED_HOSTS"
  )
  Assert-Equal ($expectedApiOnly -join "|") ($plan.ApiOnlyEnvironmentNames -join "|") "API-only environment names must be complete"
  Assert-Equal "AI_CANVAS_INSTANCE_ID|AI_CANVAS_E2E_API_PORT" ($plan.ViteEnvironmentNames -join "|") "Vite environment names must be minimal"
  foreach ($forbiddenName in @("AI_CANVAS_BOOTSTRAP_TOKEN", "AI_CANVAS_RUNTIME_PROFILE", "AI_CANVAS_AUTH_MODE", "AI_CANVAS_PUBLIC_ORIGIN", "AI_CANVAS_DEVELOPMENT_ORIGINS", "AI_CANVAS_ALLOWED_HOSTS", "AI_CANVAS_DATA_DIR")) {
    Assert-True (-not $plan.ViteEnvironment.Contains($forbiddenName)) "Vite must not receive $forbiddenName"
  }
}

$initialAiCanvasEnvironment = Get-TestAiCanvasEnvironment
try {
  [Environment]::SetEnvironmentVariable(
    "AI_CANVAS_FFPROBE_PATH",
    "C:\sentinel\ffprobe.exe",
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "AI_CANVAS_WEB_DIST",
    "C:\sentinel\web-dist",
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "AI_CANVAS_UNRELATED_SENTINEL",
    "keep-for-api",
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "AI_CANVAS_BOOTSTRAP_TOKEN",
    "inherited-bootstrap-must-not-survive",
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "AI_CANVAS_RUNTIME_PROFILE",
    "release",
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "AI_CANVAS_E2E_API_PORT",
    "59999",
    "Process"
  )

  $sentinelSnapshot = Get-AiCanvasProcessEnvironmentSnapshot
  Assert-EnvironmentEqual (Get-TestAiCanvasEnvironment) $sentinelSnapshot "Snapshot must capture every AI Canvas value"

  $sideEffectProbe = Join-Path $repoRoot ".local\dev-noauth"
  $existedBefore = Test-Path -LiteralPath $sideEffectProbe
  $null = Resolve-AiCanvasDevelopmentRuntime -RepoRoot $repoRoot -NoAuth -BootstrapFactory { throw "must not run" }
  Assert-Equal $existedBefore (Test-Path -LiteralPath $sideEffectProbe) "Pure runtime resolution must not create a directory"

  Set-AiCanvasApiProcessEnvironment -Runtime $noAuth -InitialEnvironment $sentinelSnapshot
  $apiChildEnvironment = Read-ChildAiCanvasEnvironment
  Assert-Equal "C:\sentinel\ffprobe.exe" $apiChildEnvironment["AI_CANVAS_FFPROBE_PATH"] "API child must inherit the configured ffprobe path"
  Assert-Equal "C:\sentinel\web-dist" $apiChildEnvironment["AI_CANVAS_WEB_DIST"] "API child must inherit additional API settings"
  Assert-Equal "keep-for-api" $apiChildEnvironment["AI_CANVAS_UNRELATED_SENTINEL"] "API child must inherit unrelated AI Canvas settings"
  Assert-True (-not $apiChildEnvironment.Contains("AI_CANVAS_BOOTSTRAP_TOKEN")) "NoAuth API child must clear an inherited bootstrap token"
  Assert-True (-not $apiChildEnvironment.Contains("AI_CANVAS_E2E_API_PORT")) "API child must not receive the Vite proxy setting"
  Assert-Equal "development" $apiChildEnvironment["AI_CANVAS_RUNTIME_PROFILE"] "API child must receive the controlled runtime profile"
  Assert-Equal "development-auto-session" $apiChildEnvironment["AI_CANVAS_AUTH_MODE"] "API child must receive the controlled auth mode"
  Assert-Equal $noAuth.ApiEnvironment.AI_CANVAS_PUBLIC_ORIGIN $apiChildEnvironment["AI_CANVAS_PUBLIC_ORIGIN"] "API child must receive the controlled public origin"
  Assert-Equal $noAuth.ApiEnvironment.AI_CANVAS_DATA_DIR $apiChildEnvironment["AI_CANVAS_DATA_DIR"] "API child must receive the controlled data directory"

  Set-AiCanvasViteProcessEnvironment -Runtime $noAuth
  $viteChildEnvironment = Read-ChildAiCanvasEnvironment
  $expectedViteEnvironment = @{
    AI_CANVAS_INSTANCE_ID = [string] $noAuth.ViteEnvironment.AI_CANVAS_INSTANCE_ID
    AI_CANVAS_E2E_API_PORT = [string] $noAuth.ViteEnvironment.AI_CANVAS_E2E_API_PORT
  }
  Assert-EnvironmentEqual $expectedViteEnvironment $viteChildEnvironment "Vite child must receive exactly two AI Canvas values"

  Restore-AiCanvasProcessEnvironment -Snapshot $initialAiCanvasEnvironment
  Assert-EnvironmentEqual $initialAiCanvasEnvironment (Get-TestAiCanvasEnvironment) "Restore must reproduce the initial environment"
} finally {
  Restore-TestAiCanvasEnvironment $initialAiCanvasEnvironment
}

Write-Host "Development runtime plan tests passed."

$devSource = Get-Content -Raw -LiteralPath (Join-Path $scriptRoot "dev.ps1")
$checkSource = Get-Content -Raw -LiteralPath (Join-Path $scriptRoot "check.ps1")
Assert-True ($devSource -match '\[switch\]\s*\$NoAuth') "dev.ps1 must expose the public -NoAuth switch"
Assert-True ($devSource -match 'Resolve-AiCanvasDevelopmentRuntime') "dev.ps1 must use the pure runtime plan"
Assert-True ($devSource -match 'dev-runtime\.ps1') "dev.ps1 must dot-source the runtime helper"
Assert-True ($devSource -match 'Set-AiCanvasApiProcessEnvironment') "dev.ps1 must prepare the complete API environment"
Assert-True ($devSource -match 'Set-AiCanvasViteProcessEnvironment') "dev.ps1 must isolate the Vite environment"
Assert-True ($devSource -match 'Restore-AiCanvasProcessEnvironment') "dev.ps1 must restore the complete inherited environment"
Assert-True ($devSource -match '"--host",\s*"127\.0\.0\.1"') "API command must bind literal 127.0.0.1"
Assert-True ($devSource -match '"--workers",\s*"1"') "API command must use one worker"
Assert-True ($devSource -match 'finally\s*\{[\s\S]*Restore-AiCanvasProcessEnvironment') "dev.ps1 must restore inherited process environment in finally"
Assert-True ($checkSource -match 'test-dev-runtime\.ps1') "check.ps1 must run the development runtime plan tests"

Write-Host "Development launcher integration checks passed."
