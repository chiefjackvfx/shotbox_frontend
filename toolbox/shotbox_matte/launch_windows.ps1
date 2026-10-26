# Bootstrap stays inside this folder; no system Python or Git is required.
$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$launcher = Join-Path $project "launcher.py"
$forwarded = @($args)

try {
    foreach ($directory in @(".venv-windows", ".venv")) {
        $python = Join-Path $project "$directory\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { continue }
        try {
            & $python -c "import sys; sys.exit(not ((3,12) <= sys.version_info[:2] < (3,14)))" 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
        } catch { continue }
        & $python $launcher --no-setup @forwarded
        if ($LASTEXITCODE -ne 75) { exit $LASTEXITCODE }
        break
    }

    $tools = Join-Path $project ".launcher"
    $bin = Join-Path $tools "bin"
    $uv = Join-Path $bin "uv.exe"
    $env:UV_UNMANAGED_INSTALL = $bin
    $env:UV_NO_MODIFY_PATH = "1"
    $env:UV_NO_CONFIG = "1"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $tools "python"
    if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $tools "cache" }
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path $bin | Out-Null
        New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR | Out-Null
        $installer = Join-Path $env:UV_CACHE_DIR "install-uv.ps1"
        Write-Host "Downloading the local setup tool..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/0.12.21/install.ps1" -OutFile $installer
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { throw "uv installation failed." }
        if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw "uv installation did not complete." }
    }
    Write-Host "Preparing local Python 3.12..."
    & $uv python install 3.12 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $python = & $uv python find --managed-python --no-project 3.12
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $python = $python.Trim()
    & $python $launcher --uv $uv @forwarded
    exit $LASTEXITCODE
} catch {
    Write-Host "ShotBox startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
