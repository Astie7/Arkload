# ArkLoad

**Modern Download Manager** - a desktop download manager for **MediaFire** folders and files and
for every other host, with a dark interface in the style of the new Free Download Manager.

ArkLoad is a normal Windows application: it installs with a setup program, keeps its data in your
user profile and needs no Python of its own.

## What it does

* **Downloads whole folders** - a MediaFire folder link is expanded into every file and
  sub-folder, with a file filter (`*.jar`, `mods/*`, …) if you want only some of them.
* **Resumes** - an interrupted transfer continues at the byte it stopped at, after a crash, a
  restart or a lost connection.
* **Detects what is missing** - every file of a download is tracked on its own
  (*missing*, *incomplete*, *downloading*, *completed*, *verification failed*, *failed*), so a
  folder that was only half downloaded is finished instead of started again.
* **Verifies every file** - MediaFire publishes a SHA-256 for each file; ArkLoad compares the
  download with it. A file that does not match is downloaded again and checked again.
* **Downloads several parts of a file at once** (`Connections per file` in the preferences), with
  the state of every part kept on disk, so even a split transfer continues where it stopped.
* **A real speed limiter** - the snail button in the status bar caps the transfer rate.
* **MediaFire folder keys** - a bare key such as `dpqhf3ughd4j5` works as well as a link.
* **Keeps its list** - the downloads, their tags and their priority survive a restart.
* **Knows thousands of hosts** - every link that is not a MediaFire link is downloaded by the
  **JDownloader 2 engine**, which runs hidden in the background.  You never see a JDownloader
  window: ArkLoad is the program you work with.  The MediaFire downloads keep running on
  ArkLoad's own engine, with the SHA-256 verification.

### The JDownloader engine

The engine is a separate program (it is licensed under GPLv3+/AGPL), so ArkLoad does not install
it for you.  Build it with `packaging/jdownloader/build_engine.ps1` (it checks out the official
sources and builds them) and point ArkLoad at it under **Preferences → Download Engine**.  From
then on ArkLoad starts it in the background when it is needed, drives it through its local API
and stops it again when ArkLoad closes.

## The interface

The window follows the two references it is built on: the frame of **Free Download Manager** and
the download management of **JDownloader 2**.  It has three rows at the top and one at the
bottom.

* **The menu bar** (JDownloader 2) holds the commands that do not fit into the bars:
  *File*, *Downloads*, *Settings*, *Tools* and *Help*.  The *Settings* menu carries the
  preferences, the shutdown and the download settings of the reference: *Max. chunks per
  download*, *Max. simultaneous downloads*, *Max. simultaneous downloads per hoster* and the
  *Speed limit*.
* **The command bar** holds the buttons that work on the downloads - start, pause, stop and add -
  on the left; on the right are the **+ Add download** button, the search field and the menu.
* **The navigation bar** holds the tabs of JDownloader 2 - **Downloads** (what still has work
  left), **LinkGrabber** (the links that wait for their turn, with an input to paste new ones)
  and **History** (what is finished) - and, on the right, the pills of Free Download Manager:
  the sort, the filter, the selection and a quiet line with the numbers of the list.  While a
  download is selected, the checkbox gives way to the actions of the selection.
* **The list** has the columns of the references - `#`, name, size, status with the progress
  bar, the two speeds, the time that is left and the date - and every row carries the facts of
  the download under its name.  A right click opens the menu of the download under the cursor,
  the way the file manager does it.
* **The details card** opens with the arrow in the bottom bar: the name, the path, the progress
  and the facts of the download on the left, the files it consists of on the right.
* **The overview** under the list is the "Download Overview" of JDownloader 2: the packages, the
  total speed, the bytes that are loaded, the time that is left, the open connections and the
  downloads that are running.
* **The bar at the bottom** shows the speed limiter, the settings, the live speeds and the
  download that is selected.
* **The menu of a download** is grouped - the file, the transfer, the list and the link - and
  every action knows when it can be used.  A right click opens the menu of the download under
  the cursor, the way the file manager does it, and the *Delete* key takes the download out of
  the list (with the choice between deleting the files and only removing the entry).

## Install

Download the setup program from the releases and run it.  It installs into
`%ProgramFiles%\ArkLoad`, adds a Start Menu entry and never touches your downloads
or your settings when you uninstall it.

## Run it from the sources

```bash
pip install -r requirements.txt
python tools/arkload/main.py
```

The classic window (Tkinter, no PySide6 needed) is still there:

```bash
python tools/mediafire_gui.py
```

## Build the Windows application

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

It creates

| | |
|---|---|
| `dist\ArkLoad\` | the folder build (starts instantly) |
| `dist\ArkLoad.exe` | the single file build |
| `release\ArkLoad-Setup.exe` | the installer (Inno Setup) |

The build needs `pyinstaller` and, for the installer, [Inno Setup 6](https://jrsoftware.org/isinfo.php).

## Use it from the command line

```
ArkLoad.exe --add https://www.mediafire.com/folder/abc123 --destination D:\Mods
ArkLoad.exe --add <url> --headless --exit-when-done
```

`--headless` runs the transfers without a window, which is handy for scripts and for the browser
extension.

## The browser extension

`Main menu → Browser Integration → Create the extension` writes an unpacked extension into
`%LOCALAPPDATA%\ArkLoad\browser-extension`.  Load it in Chrome or Edge ("Load unpacked") and it puts
the MediaFire link of the current page on the clipboard, ready for `Ctrl+V` in ArkLoad.

## Where the data lives

| | |
|---|---|
| Settings | `%LOCALAPPDATA%\ArkLoad\gui-settings.json` |
| Download list and manifests | `%LOCALAPPDATA%\ArkLoad\jobs\` |
| Add-ons | `%LOCALAPPDATA%\ArkLoad\addons\` |
| Log | `%LOCALAPPDATA%\ArkLoad\arkload.log` |

Nothing is ever written into the installation folder.

## Layout

```
tools/arkload/            the PySide6 application
  arkload_window.py       the main window
  download_model.py   the records, the states and the table model
  download_engine.py  the queue and the two engines behind it
  jdownloader.py     the JDownloader engine: the process and the backend
  engine.py          the client for the local API of the JDownloader engine
  hosts.py           the host plugins (MediaFire and the add-ons)
  preferences_view.py the preferences
  dialogs.py          the windows
  styles.py           the palette and the stylesheet
  settings.py         where the data lives
tools/mediafire_dl.py     the downloader (HTTP, ranges, hashes)
tools/mediafire_resume.py the manifests, the status detection, the verification
tools/mediafire_session.py the worker that plans, verifies and downloads a job
tools/mediafire_gui.py    the classic Tkinter window
packaging/             the PyInstaller and Inno Setup configuration
packaging/jdownloader/ the build of the JDownloader engine
tests/                 the test suite
```

## Tests

```bash
pip install pytest PySide6
pytest -q tests
```

The tests that talk to the real JDownloader engine are skipped when it is not installed; point
them at your build with `ARKLOAD_JDOWNLOADER`:

```bash
ARKLOAD_JDOWNLOADER=D:\\path\\to\\JDownloader.jar pytest -q tests/test_arkload_engine_live.py
```

## License

MIT - see [LICENSE](LICENSE).
