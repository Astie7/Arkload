<#
    Builds the Windows distribution of ArkLoad with Nuitka.

        powershell -ExecutionPolicy Bypass -File packaging\build_nuitka.ps1

    It creates
        build\ArkLoad\       the folder build (ArkLoad.exe and the Qt plugins)
    and keeps the temporary files in build\.

    Nuitka compiles the Python modules into native code.  It needs a C compiler - the script
    uses the one of Visual Studio 2022 Community, which is the compiler that was verified.
#>
param(
    [string]$Python = "python",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }

try {
    if ($Clean) {
        Remove-Item build -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $Python "packaging\nuitka_build.py"
    if ($LASTEXITCODE -ne 0) { throw "the Nuitka build failed" }

    Step "the result"
    Get-ChildItem "build\ArkLoad" | Select-Object Name, @{n = "MB"; e = { if ($_.PSIsContainer) { "" } else { [math]::Round($_.Length / 1MB, 1) } } } | Format-Table -AutoSize
} finally {
    Pop-Location
}
