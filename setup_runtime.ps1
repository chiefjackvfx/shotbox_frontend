# Frontend-owned Python provisioning; no system Python, PATH, or registry changes.
$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$version = (Get-Content -Raw -LiteralPath (Join-Path $project "runtime_python.txt")).Trim()
if ($args -contains "--qualify-python313") { $version = "3.13" }
if ($version -notin @("3.12", "3.13")) { throw "Unsupported Shotbox Python version." }
$tools = Join-Path $project ".runtime\tools"
$uv = Join-Path $tools "bin\uv.exe"
$downloads = Join-Path $project ".cache\temp\runtime-downloads"
$leases = Join-Path $project ".cache\temp\.active-tasks"
$lease = Join-Path $leases "runtime-bootstrap-$PID.json"
try {
    $env:UV_UNMANAGED_INSTALL = Join-Path $tools "bin"
    $env:UV_NO_MODIFY_PATH = "1"
    $env:UV_NO_CONFIG = "1"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $tools "python"
    $env:UV_CACHE_DIR = Join-Path $downloads "uv"
    $env:PYTHONUTF8 = "1"
    $env:PYTHON_JIT = "0"
    foreach ($name in @("TMPDIR", "TEMP", "TMP")) { Set-Item -Path "Env:$name" -Value $downloads }
    foreach ($name in @("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV")) {
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $tools "bin"), $downloads, $leases | Out-Null
    @{ pid = $PID } | ConvertTo-Json -Compress | Set-Content -LiteralPath $lease -Encoding ASCII
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        $installer = Join-Path $downloads "install-uv.ps1"
        Write-Host "[Shotbox] Downloading the local setup tool..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/0.12.21/install.ps1" -OutFile $installer
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { throw "uv installation failed." }
    }
    Write-Host "[Shotbox] Preparing local Python $version..."
    & $uv python install $version --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw "Python $version provisioning failed." }
    $python = & $uv python find --managed-python --no-project $version
    if ($LASTEXITCODE -ne 0) { throw "Managed Python $version was not found." }
    $python = $python.Trim()
    & $python (Join-Path $project "runtime_setup.py") --uv $uv @args
    exit $LASTEXITCODE
} catch {
    Write-Host "[Shotbox] Setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Remove-Item -LiteralPath $lease -ErrorAction SilentlyContinue
}
