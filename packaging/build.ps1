[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$specFiles = @(Get-ChildItem -LiteralPath $PSScriptRoot -File -Filter '*.spec')
if ($specFiles.Count -ne 1) {
    throw 'The packaging directory must contain exactly one .spec file.'
}
$specPath = $specFiles[0].FullName
$workPath = Join-Path $projectRoot 'build\pyinstaller'
$distPath = Join-Path $projectRoot 'dist'
$bundlePath = Join-Path $distPath 'ok-nte'
$externalResources = @('assets', 'icons', 'i18n', 'mid_lib\public', 'README.md', 'SPONSOR.md', 'LICENSE')

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Missing .venv. Install the application dependencies first (uv sync).'
}

foreach ($resource in $externalResources) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $resource))) {
        throw "Missing application resource: $resource."
    }
}

$buildEnvironmentNames = @('PATH', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH', 'PYINSTALLER_CONFIG_DIR')
$previousBuildEnvironment = @{}
foreach ($name in $buildEnvironmentNames) {
    $previousBuildEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

Push-Location $projectRoot
try {
    # Package hooks discover DLLs inside .venv. Keep unrelated tool DLLs out of PATH.
    $env:PATH = @(
        (Split-Path -Parent $pythonPath)
        (Join-Path $env:SystemRoot 'System32')
        $env:SystemRoot
        (Join-Path $env:SystemRoot 'System32\Wbem')
    ) -join [IO.Path]::PathSeparator
    foreach ($name in @('QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $workPath 'cache'

    # Isolated Python ignores PYTHONPATH, PYTHONHOME and user site-packages.
    & $pythonPath -I -c 'import PyInstaller, _pyinstaller_hooks_contrib'
    if ($LASTEXITCODE -ne 0) {
        throw 'Install build tools: uv pip install --python .venv\Scripts\python.exe -r packaging\requirements.txt'
    }

    # OpenVINO makes compiled cache blobs read-only after writing them.
    # Clear that attribute before PyInstaller removes the previous bundle.
    $openvinoCachePath = Join-Path $bundlePath 'cache\openvino'
    if (Test-Path -LiteralPath $openvinoCachePath -PathType Container) {
        $readOnlyCaches = @(Get-ChildItem -LiteralPath $openvinoCachePath -File -Force -Filter '*.blob' |
            Where-Object { $_.IsReadOnly })
        foreach ($cacheFile in $readOnlyCaches) {
            $cacheFile.IsReadOnly = $false
        }
    }

    & $pythonPath -I -m PyInstaller --noconfirm --clean --distpath $distPath --workpath $workPath $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    # Keep application resources at the paths used by the existing source code.
    foreach ($resource in $externalResources) {
        $resourceParent = Split-Path -Parent $resource
        $destination = $bundlePath
        if ($resourceParent) {
            $destination = Join-Path $bundlePath $resourceParent
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
        }
        Copy-Item -LiteralPath (Join-Path $projectRoot $resource) -Destination $destination -Recurse
    }

    $files = @(Get-ChildItem -LiteralPath $bundlePath -File -Recurse)
    $totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
    Write-Host ('Built: {0}' -f (Join-Path $bundlePath 'ok-nte.exe'))
    Write-Host ('Files: {0}; size: {1:N2} MiB' -f $files.Count, ($totalBytes / 1MB))
}
finally {
    Pop-Location
    foreach ($name in $buildEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousBuildEnvironment[$name], 'Process')
    }
}
