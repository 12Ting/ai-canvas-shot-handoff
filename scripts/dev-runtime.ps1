function Resolve-AiCanvasDevelopmentRuntime {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)]
    [string] $RepoRoot,

    [switch] $NoAuth,
    [switch] $NoBrowser,

    [ValidateRange(1, 65535)]
    [int] $ApiPort = 8765,

    [ValidateRange(1, 65535)]
    [int] $VitePort = 5173,

    [Parameter(Mandatory)]
    [ScriptBlock] $BootstrapFactory
  )

  $resolvedRepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
  $apiOrigin = "http://127.0.0.1:$ApiPort"
  $webOrigin = "http://127.0.0.1:$VitePort"
  $instanceId = [Guid]::NewGuid().ToString("N")
  $authMode = if ($NoAuth) { "development-auto-session" } else { "session" }
  $dataLeaf = if ($NoAuth) { "dev-noauth" } else { "dev" }
  $dataDirectory = Join-Path $resolvedRepoRoot ".local\$dataLeaf"

  $apiOnlyEnvironmentNames = @(
    "AI_CANVAS_BOOTSTRAP_TOKEN",
    "AI_CANVAS_PUBLIC_ORIGIN",
    "AI_CANVAS_DATA_DIR",
    "AI_CANVAS_RUNTIME_PROFILE",
    "AI_CANVAS_AUTH_MODE",
    "AI_CANVAS_DEVELOPMENT_ORIGINS",
    "AI_CANVAS_ALLOWED_HOSTS"
  )
  $viteEnvironmentNames = @(
    "AI_CANVAS_INSTANCE_ID",
    "AI_CANVAS_E2E_API_PORT"
  )

  $apiEnvironment = [ordered]@{
    AI_CANVAS_PUBLIC_ORIGIN = $webOrigin
    AI_CANVAS_DATA_DIR = $dataDirectory
    AI_CANVAS_RUNTIME_PROFILE = "development"
    AI_CANVAS_AUTH_MODE = $authMode
    AI_CANVAS_DEVELOPMENT_ORIGINS = "[`"$webOrigin`"]"
    AI_CANVAS_ALLOWED_HOSTS = '["127.0.0.1"]'
    AI_CANVAS_INSTANCE_ID = $instanceId
  }

  if (-not $NoAuth) {
    $apiEnvironment["AI_CANVAS_BOOTSTRAP_TOKEN"] = & $BootstrapFactory
  }

  $viteEnvironment = [ordered]@{
    AI_CANVAS_INSTANCE_ID = $instanceId
    AI_CANVAS_E2E_API_PORT = [string] $ApiPort
  }

  [pscustomobject]@{
    Mode = $authMode
    RuntimeProfile = "development"
    AuthMode = $authMode
    ApiOrigin = $apiOrigin
    WebOrigin = $webOrigin
    DataDirectory = $dataDirectory
    LaunchUrl = if ($NoAuth) {
      "$webOrigin/"
    } else {
      "$webOrigin/#bootstrap=$($apiEnvironment['AI_CANVAS_BOOTSTRAP_TOKEN'])"
    }
    OpenBrowser = -not [bool] $NoBrowser
    Warning = if ($NoAuth) {
      "LOCAL DEVELOPMENT RISK: automatic sessions are enabled only for 127.0.0.1 debugging."
    } else {
      $null
    }
    ApiEnvironment = $apiEnvironment
    ViteEnvironment = $viteEnvironment
    ApiOnlyEnvironmentNames = $apiOnlyEnvironmentNames
    ViteEnvironmentNames = $viteEnvironmentNames
    EnvironmentNames = @($apiOnlyEnvironmentNames + $viteEnvironmentNames | Select-Object -Unique)
  }
}

function Get-AiCanvasProcessEnvironmentSnapshot {
  [CmdletBinding()]
  param()

  $snapshot = [System.Collections.Generic.Dictionary[string, string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  $variables = [Environment]::GetEnvironmentVariables("Process")
  foreach ($key in @($variables.Keys)) {
    $name = [string] $key
    if ($name.StartsWith("AI_CANVAS_", [StringComparison]::OrdinalIgnoreCase)) {
      $snapshot[$name] = [string] $variables[$key]
    }
  }
  ,$snapshot
}

function Clear-AiCanvasProcessEnvironment {
  [CmdletBinding()]
  param()

  $variables = [Environment]::GetEnvironmentVariables("Process")
  foreach ($key in @($variables.Keys)) {
    $name = [string] $key
    if ($name.StartsWith("AI_CANVAS_", [StringComparison]::OrdinalIgnoreCase)) {
      [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
  }
}

function Set-AiCanvasProcessEnvironmentValues {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)]
    [System.Collections.IDictionary] $Values
  )

  foreach ($entry in $Values.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable(
      [string] $entry.Key,
      [string] $entry.Value,
      "Process"
    )
  }
}

function Set-AiCanvasApiProcessEnvironment {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)]
    [psobject] $Runtime,

    [Parameter(Mandatory)]
    [System.Collections.IDictionary] $InitialEnvironment
  )

  $controlledNames = [System.Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($name in $Runtime.EnvironmentNames) {
    $null = $controlledNames.Add([string] $name)
  }

  Clear-AiCanvasProcessEnvironment
  foreach ($entry in $InitialEnvironment.GetEnumerator()) {
    if (-not $controlledNames.Contains([string] $entry.Key)) {
      [Environment]::SetEnvironmentVariable(
        [string] $entry.Key,
        [string] $entry.Value,
        "Process"
      )
    }
  }
  Set-AiCanvasProcessEnvironmentValues -Values $Runtime.ApiEnvironment
}

function Set-AiCanvasViteProcessEnvironment {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)]
    [psobject] $Runtime
  )

  Clear-AiCanvasProcessEnvironment
  Set-AiCanvasProcessEnvironmentValues -Values $Runtime.ViteEnvironment
}

function Restore-AiCanvasProcessEnvironment {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)]
    [System.Collections.IDictionary] $Snapshot
  )

  Clear-AiCanvasProcessEnvironment
  Set-AiCanvasProcessEnvironmentValues -Values $Snapshot
}
