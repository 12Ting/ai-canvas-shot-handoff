$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiSource = Join-Path $repoRoot "apps\api\src"
$previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
try {
  $env:PYTHONPATH = if ([string]::IsNullOrEmpty($previousPythonPath)) {
    $apiSource
  } else {
    $apiSource + [IO.Path]::PathSeparator + $previousPythonPath
  }
  & "$repoRoot\apps\api\.venv\Scripts\python.exe" "$repoRoot\apps\api\scripts\export_openapi.py"
  if ($LASTEXITCODE -ne 0) {
    throw "OpenAPI export failed with exit code $LASTEXITCODE."
  }
} finally {
  if ($null -eq $previousPythonPath) {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
  } else {
    $env:PYTHONPATH = $previousPythonPath
  }
}
& npm.cmd exec --prefix "$repoRoot\apps\web" openapi-typescript -- "$repoRoot\apps\web\src\api\openapi.json" -o "$repoRoot\apps\web\src\api\generated-schema.ts"
if ($LASTEXITCODE -ne 0) {
  throw "API client generation failed with exit code $LASTEXITCODE."
}
