<#
    Builds the Windows distribution of ArkLoad - MediaFire Download Manager.

        powershell -ExecutionPolicy Bypass -File packaging\build.ps1

    It creates
        dist\ArkLoadDownloader\           the folder build (the one the installer uses)
        dist\ArkLoadDownloader.exe        the single file build
        release\ArkLoadDownloader-Setup.exe   the installer
    and keeps the temporary files in build\.
#>
param(
    [switch]$SkipInstaller,
    [switch]$SkipOneFile,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root

function Step($text) { Write-Host "" ; Write-Host "== $text" -ForegroundColor Cyan }

try {
    Step "the application icon"
    & $Python "packaging\make_icon.py"
    if ($LASTEXITCODE -ne 0) { throw "the icon could not be created" }

    Step "the folder build"
    & $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\arkload.spec
    if ($LASTEXITCODE -ne 0) { throw "the folder build failed" }

    if (-not $SkipOneFile) {
        Step "the single file build"
        $env:ArkLoad_ONEFILE = "1"
        & $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\arkload.spec
        Remove-Item Env:\ArkLoad_ONEFILE -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) { throw "the single file build failed" }
    }

    if (-not $SkipInstaller) {
        Step "the installer"
        $iscc = @(
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) {
            Write-Warning "Inno Setup was not found - the installer was not built."
        } else {
            & $iscc "packaging\installer.iss"
            if ($LASTEXITCODE -ne 0) { throw "the installer failed" }
        }
    }

    Step "the result"
    Get-ChildItem dist, release -ErrorAction SilentlyContinue |
        Select-Object FullName, @{n = "MB"; e = { if ($_.PSIsContainer) { "" } else { [math]::Round($_.Length / 1MB, 1) } } } |
        Format-Table -AutoSize
}
finally {
    Pop-Location
}
