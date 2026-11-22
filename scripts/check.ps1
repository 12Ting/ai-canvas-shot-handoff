$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$apiPython = Join-Path $repoRoot "apps\api\.venv\Scripts\python.exe"
$apiSource = Join-Path $repoRoot "apps\api\src"
$apiTests = Join-Path $repoRoot "apps\api\tests"
$generateClient = Join-Path $repoRoot "scripts\generate-api-client.ps1"
$testDevelopmentRuntime = Join-Path $repoRoot "scripts\test-dev-runtime.ps1"
$webRoot = Join-Path $repoRoot "apps\web"
$checkTempRoot = Join-Path $repoRoot ".local\check"

if (-not (Test-Path -LiteralPath $apiPython -PathType Leaf)) {
  throw "API virtual environment is missing: $apiPython"
}
if (-not (Test-Path -LiteralPath $generateClient -PathType Leaf)) {
  throw "API client generator is missing: $generateClient"
}
if (-not (Test-Path -LiteralPath $testDevelopmentRuntime -PathType Leaf)) {
  throw "Development runtime test is missing: $testDevelopmentRuntime"
}

$git = (Get-Command git.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $testDevelopmentRuntime
if ($LASTEXITCODE -ne 0) {
  throw "Development runtime plan tests failed with exit code $LASTEXITCODE."
}

& $apiPython -m ruff check $apiSource $apiTests
if ($LASTEXITCODE -ne 0) {
  throw "Ruff failed with exit code $LASTEXITCODE."
}

$pytestTemp = Join-Path $checkTempRoot ("pytest-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $pytestTemp | Out-Null
$pytestExitCode = 0
try {
  & $apiPython -m pytest --basetemp $pytestTemp $apiTests
  $pytestExitCode = $LASTEXITCODE
} finally {
  $resolvedCheckTempRoot = [System.IO.Path]::GetFullPath($checkTempRoot).TrimEnd("\")
  $resolvedPytestTemp = [System.IO.Path]::GetFullPath($pytestTemp)
  if ([System.IO.Path]::GetDirectoryName($resolvedPytestTemp) -ne $resolvedCheckTempRoot) {
    throw "Refusing to clean unexpected pytest directory: $resolvedPytestTemp"
  }
  if (Test-Path -LiteralPath $resolvedPytestTemp) {
    Remove-Item -LiteralPath $resolvedPytestTemp -Recurse -Force
  }
}
if ($pytestExitCode -ne 0) {
  throw "pytest failed with exit code $pytestExitCode."
}

& $generateClient
if ($LASTEXITCODE -ne 0) {
  throw "API client generation failed with exit code $LASTEXITCODE."
}

& $git -C $repoRoot diff --exit-code -- "apps/web/src/api/openapi.json" "apps/web/src/api/generated-schema.ts"
if ($LASTEXITCODE -ne 0) {
  throw "Generated API files differ between the working tree and index."
}

& $git -C $repoRoot diff --cached --exit-code -- "apps/web/src/api/openapi.json" "apps/web/src/api/generated-schema.ts"
if ($LASTEXITCODE -ne 0) {
  throw "Staged generated API files differ from HEAD."
}

& $npm run test --prefix $webRoot
if ($LASTEXITCODE -ne 0) {
  throw "Frontend tests failed with exit code $LASTEXITCODE."
}

& $npm run build --prefix $webRoot
if ($LASTEXITCODE -ne 0) {
  throw "Frontend build failed with exit code $LASTEXITCODE."
}
