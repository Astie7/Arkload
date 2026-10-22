<#
    Builds the engines of ArkLoad and stages them for the installer.

        powershell -ExecutionPolicy Bypass -File packaging\build_engines.ps1

    It does three things:

    1. it builds the JDownloader 2 engine from the official sources (or reuses the build in
       the checkout) and stages it into packaging\engines\jdownloader,
    2. it downloads the Java runtime the engine needs (Eclipse Temurin) into the same folder,
       so a clean Windows system works without a Java installation,
    3. it downloads the official Windows build of aria2 into packaging\engines\aria2.

    The result is what the installer puts into %LOCALAPPDATA%\ArkLoad\engines; the application
    finds it there on its own.
#>
param(
    [string]$Checkout = "D:\AstieProjects\_jd_official",
    [string]$Staging = "",
    [switch]$SkipJava,
    [switch]$SkipAria2
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Staging) { $Staging = Join-Path $Root "packaging\engines" }

function Info($text) { Write-Host "== $text" -ForegroundColor Cyan }

# ---------------------------------------------------------------- the JDownloader engine
Info "the JDownloader 2 engine"
$source = Join-Path $Checkout "JDownloader\standalone\dist"
$target = Join-Path $Staging "jdownloader"
if (-not (Test-Path (Join-Path $source "JDownloader.jar"))) {
    Write-Warning "the engine was not built in $source - run packaging\jdownloader\build_engine.ps1 first"
} else {
    New-Item -ItemType Directory -Force $target | Out-Null
    foreach ($item in @("JDownloader.jar", "Core.jar", "jd", "libs", "themes", "translations",
                        "extensions", "licenses")) {
        $from = Join-Path $source $item
        $to = Join-Path $target $item
        if (-not (Test-Path $from)) { continue }
        if ((Test-Path $to) -and ((Get-Item $to).LastWriteTime -gt (Get-Item $from).LastWriteTime)) {
            continue
        }
        if (Test-Path $to) { Remove-Item $to -Recurse -Force }
        Copy-Item $from $to -Recurse -Force
    }
    Write-Host "   staged in $target"
}

# ---------------------------------------------------------------- the Java runtime
if (-not $SkipJava) {
    Info "the Java runtime (Eclipse Temurin)"
    $jre = Join-Path $Staging "jdownloader\jre"
    $version = "21.0.12.1+1"
    $url = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip"
    if (Test-Path (Join-Path $jre "bin\java.exe")) {
        Write-Host "   the runtime is already there"
    } else {
        $zip = Join-Path $env:TEMP "arkload-jre.zip"
        Write-Host "   downloading $version"
        curl.exe -L --max-time 900 -o $zip $url | Out-Null
        $extract = Join-Path $env:TEMP "arkload-jre"
        if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
        Expand-Archive $zip -DestinationPath $extract -Force
        $root = Get-ChildItem $extract -Directory | Select-Object -First 1
        New-Item -ItemType Directory -Force (Split-Path -Parent $jre) | Out-Null
        Move-Item $root.FullName $jre
        Remove-Item $extract -Recurse -Force
        Remove-Item $zip -Force
        Write-Host "   the runtime is in $jre"
    }
}

# ---------------------------------------------------------------- aria2
if (-not $SkipAria2) {
    Info "aria2"
    $aria = Join-Path $Staging "aria2"
    $exe = Join-Path $aria "aria2c.exe"
    if (Test-Path $exe) {
        Write-Host "   aria2 is already there"
    } else {
        $url = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
        $zip = Join-Path $env:TEMP "arkload-aria2.zip"
        Write-Host "   downloading aria2 1.37.0"
        curl.exe -L --max-time 600 -o $zip $url | Out-Null
        $extract = Join-Path $env:TEMP "arkload-aria2"
        if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
        Expand-Archive $zip -DestinationPath $extract -Force
        New-Item -ItemType Directory -Force $aria | Out-Null
        foreach ($name in @("aria2c.exe", "COPYING", "README.html", "AUTHORS")) {
            $file = Get-ChildItem $extract -Recurse -Filter $name | Select-Object -First 1
            if ($file) { Copy-Item $file.FullName (Join-Path $aria $name) -Force }
        }
        Remove-Item $extract -Recurse -Force
        Remove-Item $zip -Force
        Write-Host "   aria2 is in $aria"
    }
}

Info "the result"
Get-ChildItem $Staging | ForEach-Object {
    $size = if ($_.PSIsContainer) {
        [math]::Round(((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
    } else { [math]::Round($_.Length / 1MB, 1) }
    "{0,-16} {1,8} MB" -f $_.Name, $size
}
