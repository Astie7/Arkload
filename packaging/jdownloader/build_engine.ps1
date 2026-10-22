<#
    Checks out the official JDownloader 2 repositories and builds the standalone engine.

        powershell -ExecutionPolicy Bypass -File packaging\jdownloader\build_engine.ps1

    The result is a running engine that ArkLoad can drive through
    tools/arkload/engine.py (the local HTTP API on port 3128).
#>
param(
    [string]$Root = "D:\AstieProjects\_jd_official",
    [string]$Svn = "C:\cygwin64\bin\svn.exe",
    [string]$Ant = "D:\AstieProjects\_tools\ant\bin\ant.bat",
    [string]$JavaHome = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot",
    [switch]$SkipCheckout
)

$ErrorActionPreference = "Stop"

function Info($text) { Write-Host "== $text" -ForegroundColor Cyan }

if (-not $SkipCheckout) {
    Info "the four upstream repositories"
    New-Item -ItemType Directory -Force $Root | Out-Null
    Push-Location $Root
    try {
        $repositories = @(
            @("svn://svn.appwork.org/utils", "AppWorkUtils"),
            @("svn://svn.jdownloader.org/jdownloader/browser", "JDBrowser"),
            @("svn://svn.jdownloader.org/jdownloader/trunk", "JDownloader"),
            @("svn://svn.jdownloader.org/jdownloader/MyJDownloaderClient", "MyJDownloaderClient")
        )
        foreach ($repository in $repositories) {
            if (Test-Path (Join-Path $Root $repository[1])) { continue }
            & $Svn co --non-interactive $repository[0] $repository[1]
            if ($LASTEXITCODE -ne 0) { throw "the checkout of $($repository[0]) failed" }
        }
    } finally { Pop-Location }
}

Info "the two build fixes"
$build = Join-Path $Root "JDownloader\build\newBuild\build_standalone.xml"
$text = Get-Content $build -Raw
if ($text -match '\*\*/ide/\*\*,') {
    $text = $text -replace '\*\*/ide/\*\*,', ''
    Set-Content -Path $build -Value $text -NoNewline
    Write-Host "   AppWorkUtils' ide package is compiled from the sources again"
}
$libs = Join-Path $Root "AppWorkUtils\libs"
foreach ($artifact in @("asm", "asm-tree", "asm-commons", "asm-util", "asm-analysis")) {
    $jar = Join-Path $libs "$artifact-9.7.jar"
    if (Test-Path $jar) { continue }
    $url = "https://repo1.maven.org/maven2/org/ow2/asm/$artifact/9.7/$artifact-9.7.jar"
    curl.exe -L --max-time 180 -o $jar $url | Out-Null
    Write-Host "   added $artifact-9.7.jar"
}

Info "the build"
$env:JAVA_HOME = $JavaHome
Push-Location $Root
try {
    & $Ant "-Dbasedir=JDownloader" "-f" "JDownloader\build\newBuild\build_standalone.xml"
    if ($LASTEXITCODE -ne 0) { throw "the build failed" }
} finally { Pop-Location }

Info "the result"
$dist = Join-Path $Root "JDownloader\standalone\dist"
Get-ChildItem $dist | Select-Object Name, Length
Write-Host ""
Write-Host "Start it with:  java -jar `"$dist\JDownloader.jar`""
Write-Host "Enable the local API in `"$dist\cfg\org.jdownloader.api.RemoteAPIConfig.json`":"
Write-Host '  {"port":3128,"deprecatedapienabled":true,"externinterfaceenabled":true,'
Write-Host '   "jdanywhereapienabled":false,"externinterfacelocalhostonly":true,"enabled":true}'

Info "the engine for the installer"
# the parts the engine needs (the tools of the platforms are not used)
$stage = Join-Path $PSScriptRoot "engine"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
foreach ($item in @("JDownloader.jar", "Core.jar", "jd", "libs", "themes", "translations",
                    "extensions", "licenses")) {
    $source = Join-Path $dist $item
    if (Test-Path $source) { Copy-Item $source $stage -Recurse -Force }
}
$size = [math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "   $stage ($size MB) - the installer offers it as an optional task"
