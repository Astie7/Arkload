# ArkLoad

**Modern Download Manager**: a Windows desktop download manager for MediaFire folders and files, direct downloads, and other supported hosting services. Features include download resumption, per-file status detection, SHA-256 verification, segmented transfers, and a modern dark interface inspired by Free Download Manager.

ArkLoad is a normal Windows application that installs through a setup program, stores its data in your user profile, and does not require a separate Python installation.

## What it does

- **Downloads whole folders**: expands MediaFire folder links into their files and subfolders, with optional file filters such as `*.jar` or `mods/*`.
- **Resumes interrupted downloads**: continues transfers from saved progress after a crash, restart, or connection loss, whenever supported.
- **Tracks every file independently**: distinguishes between missing, incomplete, downloading, completed, verification failed, and failed files, allowing incomplete folders to resume without unnecessarily restarting completed files.
- **Verifies file integrity**: compares downloaded files against available SHA-256 checksums and retries downloads when verification fails.
- **Supports segmented downloads**: downloads multiple parts of a file concurrently, preserving part information where supported.
- **Limits download speed**: controls the transfer rate through the speed limiter in the status bar.
- **Accepts MediaFire folder keys**: supports bare folder keys as well as full links.
- **Persists download information**: retains the download list, tags, priorities, and supported metadata across restarts.
- **Supports multiple hosting services**: handles MediaFire and other supported hosting services, alongside direct downloads.
- **Provides a unified experience**: download management, progress reporting, and controls remain consistent throughout the application.

## The interface

ArkLoad features a modern dark interface inspired by Free Download Manager, with its own identity and download-management workflows.

- **Menu bar**: provides File, Downloads, Settings, Tools, and Help commands.
- **Command bar**: provides download creation and transfer controls, search, and additional actions.
- **Navigation bar**: organizes active downloads, links awaiting processing, and download history.
- **Download list**: displays filenames, sizes, statuses, progress, transfer speeds, remaining time, and other relevant details.
- **Context menus**: provide actions appropriate to the selected download. Right-clicking a row opens the menu for the item under the cursor.
- **Download details**: shows the selected transfer's destination, progress, and associated files.
- **Overview**: summarizes active transfers, total speed, transferred data, remaining time, and connection activity.
- **Status bar**: displays transfer speeds, speed-limit controls, settings access, and selected-download information.

The interface maintains a consistent experience across supported downloads and hosting services.

## Install

Download the setup program from the releases and run it. The installer places ArkLoad in `%ProgramFiles%\ArkLoad` and adds a Start Menu entry. Uninstalling the application does not remove your downloads or personal settings.

## Run it from the sources

```bash
pip install -r requirements.txt
python tools/arkload/main.py
```

The classic Tkinter window remains available:

```bash
python tools/mediafire_gui.py
```

## Build the Windows application

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The build produces:

- `dist\ArkLoad\`: folder distribution.
- `dist\ArkLoad.exe`: single-file executable.
- `release\ArkLoad-Setup.exe`: Windows installer.

The build requires PyInstaller and, for the installer, [Inno Setup 6](https://jrsoftware.org/isinfo.php).

## Command-line usage

```text
ArkLoad.exe --add https://www.mediafire.com/folder/abc123 --destination D:\Mods
ArkLoad.exe --add <url> --headless --exit-when-done
```

The `--headless` option runs transfers without opening a window, making it useful for scripts and browser integrations.

## Browser integration

Use **Main menu → Browser Integration → Create the extension** to generate an unpacked extension in `%LOCALAPPDATA%\ArkLoad\browser-extension`. Load it in Chrome or Edge using the browser's extension developer tools.

The extension helps transfer links from the browser to ArkLoad.

## Where the data lives

| Data | Location |
|---|---|
| Settings | `%LOCALAPPDATA%\ArkLoad\gui-settings.json` |
| Download list and manifests | `%LOCALAPPDATA%\ArkLoad\jobs\` |
| Add-ons | `%LOCALAPPDATA%\ArkLoad\addons\` |
| Logs | `%LOCALAPPDATA%\ArkLoad\arkload.log` |

ArkLoad keeps user data outside its installation directory.

## License

ArkLoad's licensing terms are defined in `LICENSE`. Third-party libraries and components retain their respective licenses.
