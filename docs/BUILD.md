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

## The engines of ArkLoad

ArkLoad ships two engines next to the application: the **JDownloader 2** engine (with the Java
runtime it needs) and **aria2**.  They are not part of the repository - they are 260 MB of
binaries - so they are built and staged with one script:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_engines.ps1
```

The script

1. builds the JDownloader 2 engine from the official upstream repositories (or reuses the build
   of `packaging\jdownloader\build_engine.ps1`),
2. downloads the Java runtime the engine needs (Eclipse Temurin, 21 LTS),
3. downloads the official Windows build of aria2,

and stages everything into `packaging\engines`:

```
packaging/engines/
  jdownloader/     the engine, its plugins, its licences and the Java runtime (jre/)
  aria2/           aria2c.exe and its licence
  NOTICE.txt       the licences of the engines
```

The installer copies that folder into `%LOCALAPPDATA%\ArkLoad\engines` (the task *Install the
download engines*), where the application finds it on its own - the user does not have to install
or to point at anything.  The source tree is the second place the application looks, so a
development build works without the installer.

### Building the JDownloader engine from the sources

```powershell
powershell -ExecutionPolicy Bypass -File packaging\jdownloader\build_engine.ps1
```

The script checks out the four upstream SVN repositories (`svn://svn.appwork.org/utils`,
`svn://svn.jdownloader.org/jdownloader/{browser,trunk,MyJDownloaderClient}`), applies the two
documented build fixes and runs the documented Ant build:

```powershell
ant -Dbasedir=JDownloader -f JDownloader/build/newBuild/build_standalone.xml
```

The result is `JDownloader/standalone/dist/JDownloader.jar`.

### How ArkLoad talks to the engines

* **JDownloader 2** through its local API (port 3128, bound to localhost).  ArkLoad writes the
  settings of the engine into its `cfg` folder and starts it headless, so no JDownloader window
  appears:

  ```json
  {"deprecatedapiport":3128,"externinterfaceenabled":true,"deprecatedapienabled":true,
   "jdanywhereapienabled":false,"externinterfacelocalhostonly":true,"enabled":true}
  ```

  The client is `tools/arkload/engine.py`, the engine behind the common interface is
  `tools/arkload/engines/jdownloader.py`.

* **aria2** through its JSON-RPC interface.  ArkLoad starts `aria2c` with the RPC interface on a
  local port and a secret that is generated for every run, and talks to it with
  `tools/arkload/engines/aria2.py`.

## The Nuitka build (an alternative to PyInstaller)

ArkLoad can also be built with Nuitka, which compiles the Python modules into native code.  The
result is a folder build, the same shape as the PyInstaller one, and it is verified to start
without a Python installation and to download a file through the engine.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_nuitka.ps1
```

The build needs:

* Python 3.14 with Nuitka installed (`python -m pip install nuitka`),
* the C compiler of Visual Studio 2022 Community (the script finds it itself through
  `vcvars64.bat`).

The result is `build\ArkLoad\ArkLoad.exe`.  The engines are not part of the build - they are
installed by the installer or staged with `packaging\build_engines.ps1`, and the application
finds them where they are.

### PyInstaller or Nuitka?

Both are supported.  PyInstaller is the one the installer uses; Nuitka is available for the cases
where the native build matters.  The two produce the same application with the same behaviour.
