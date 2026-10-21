# Building ArkLoad

Everything below was executed on the machine that produced the v1.0.0 release. The versions are
pinned here on purpose: a build that cannot be repeated is not a release.

## The tool chain

| Tool | Version | Notes |
|---|---|---|
| Python | 3.14.7 (64 bit) | 3.10 or newer works, the release used 3.14 |
| PySide6 | 6.12.0 | LGPLv3, ships the Qt 6 runtime |
| PyInstaller | 6.22.3 | the folder and the single file build |
| Inno Setup | 6.7.3 | the installer (`ISCC.exe`) |
| Windows | 10 / 11, 64 bit | the target platform |

## The one command

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

It produces exactly three artifacts and keeps the temporaries in `build\`:

```
dist\ArkLoad\ArkLoad.exe          the folder build (starts instantly)
dist\ArkLoad.exe                  the single file build (extracts itself on the first start)
release\ArkLoad-Setup.exe         the installer
```

Useful switches:

```powershell
packaging\build.ps1 -SkipOneFile        # only the folder build and the installer
packaging\build.ps1 -SkipInstaller      # only the two builds
```

## The individual steps

```powershell
# 1. the icon (packaging\assets\arkload.ico, generated, not in the repository)
python packaging\make_icon.py

# 2. the folder build
pyinstaller packaging\arkload.spec

# 3. the single file build
$env:ARKLOAD_ONEFILE = "1"
pyinstaller packaging\arkload.spec
Remove-Item Env:\ARKLOAD_ONEFILE

# 4. the installer (needs Inno Setup 6)
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

`packaging\arkload.spec` is the single source of truth for both builds: the `ARKLOAD_ONEFILE`
environment variable decides between the folder and the single file build, so the two can never
drift apart.

## Running the tests

```powershell
pip install -r requirements.txt
pytest -q tests
```

The suite is offline except for the checks that talk to MediaFire; it covers the URL parsing, the
planning, the ranges, the resuming, the hashing, the manifests, the session lifecycle (including a
cancelled and a paused transfer), the model, the engine, the window and the dialogs.

## Verifying a fresh clone

```powershell
git clone https://github.com/Astie7/Arkload.git
cd ArkLoad
pytest -q tests
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The build must end with the three artifacts listed above and the installer must carry the ArkLoad
identity (`ProductName: ArkLoad`, `CompanyName: Astie7`).

## How the application finds its data

Nothing is written into the installation folder.

| | |
|---|---|
| Settings | `%LOCALAPPDATA%\ArkLoad\gui-settings.json` |
| Queue and manifests | `%LOCALAPPDATA%\ArkLoad\jobs\` |
| Add-ons | `%LOCALAPPDATA%\ArkLoad\addons\` |
| Log | `%LOCALAPPDATA%\ArkLoad\mfd.log` |

A portable copy keeps the same data next to itself: a `portable.marker` file beside the
application switches the paths over (`packaging\portable.py` builds that copy).

## The command line

```
ArkLoad.exe --add <url>                 add a link and download it
ArkLoad.exe --add <url> --destination D:\Mods
ArkLoad.exe --add <url> --headless --exit-when-done     for scripts
ArkLoad.exe --state-dir <folder>        use another folder for the manifests
ArkLoad.exe --settings <file>           use another settings file
```

## Signing

The binaries are not code signed, so Windows SmartScreen warns on the first start. A release that
has to avoid that needs a certificate and a `signtool` step in `packaging\build.ps1`.
