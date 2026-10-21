# ArkLoad - Phase 1 audit and architecture report

**Date:** 2026-10-21 · **Status:** Phase 1 complete, Phase 2 ready to start

## 1. Inputs that were inspected

| Input | Result | Evidence |
|---|---|---|
| The MediaFire downloader (this repository) | **Inspected, first hand** | 14 modules, ~4,500 lines, 147 passing tests, live downloads against `mediafire.com` |
| JDownloader 2 source (`jdownloader_mirror-main.zip`, 575 MB) | **Inspected, first hand** | the official upstream **SVN mirror** (daily auto-updated), unpacked to `D:\AstieProjects\_audit_jdownloader` |

The JDownloader mirror is **not a fork** - its README says it is "a daily auto-updated mirror of the
JDownloader SVN server". Layout: `svn_trunk` (the main source), `svn_MyJDownloaderClient`,
`svn_utils`, `svn_browser`.

## 2. The JDownloader 2 audit

### 2.1 What the mirror actually contains

| Area | Files | Present? |
|---|---|---|
| `svn_trunk/src` | 5,256 | the plugin API and the plugin ecosystem |
| `src/jd/plugins/hoster` | 1,422 | **yes** - the host plugins (MediaFire among them) |
| `src/jd/plugins/decrypter` | 858 | **yes** - the link/folder decrypter plugins |
| `src/jd/gui` | 322 | only the old Swing parts (`swing/components`, `swing/dialog`, `swing/laf`, `userio`) |
| `src/jd/controlling` | 271 | the queue controller is **missing** (`DownloadController` is absent) |
| `src/jd/http` | **0** | the HTTP stack is **missing** |
| `src/jd/plugins/Plugin.java`, `PluginForHost.java`, `DownloadLink.java` | - | **present** (the plugin API) |

**The engine core is closed source.** The download queue, the HTTP layer, the modern `jgui` and the
browser integration live in the modules `JDClosed`, `AppWorkUtils`, `JDBrowser`, `UpdateClientV2`,
`JDownloaderUpdater` and `JDownloaderServerOptions` - none of them are in the mirror and none of
them are public.

### 2.2 The build procedure

* `svn_trunk/build/newBuild/build.xml` (19.5 kB) and `build_standalone.xml` (16.7 kB) - **Apache Ant**,
  not Gradle or Maven. `svn_trunk/build/launch4j.xml` is the Windows `.exe` wrapper,
  `nsisant-1.2.jar` the installer, `ressourcen/libs` the third party jars (bouncycastle, antlr, …).
* The Ant build compiles **six sibling checkouts** next to the trunk:

  ```
  ../AppWorkUtils  ../JDClosed  ../JDownloaderServerOptions
  ../JDBrowser     ../UpdateClientV2  ../JDownloaderUpdater  ../MyJDownloaderClient
  ```

  Only `MyJDownloaderClient` is in the mirror. **`JDClosed` is not obtainable.**
* On this machine: **Temurin JDK 25** is installed, **Ant, Maven and Gradle are not**.

### 2.2.1 The baseline build (attempted, with the real tool chain)

Apache Ant 1.10.15 and Temurin JDK 25 were installed and the upstream build was run
unmodified.  The dependency layout the build expects was reconstructed (the build resolves
`../utils`, `../browser` and `../MyJDownloaderClient` relative to `svn_trunk/build/`, and the
mirror ships `svn_utils`, `svn_browser` and `svn_MyJDownloaderClient`):

```
ant -f build/newBuild/build_standalone.xml
```

Result: **the build fails inside the first compile step**, with 100 javac errors:

* `package org.objectweb.asm does not exist` - the mirror's `utils/libs` contains only the
  layout jars (`miglayout`, `jsvg`, `svgSalamander`); the libraries the sources compile against
  are not in the archive,
* `Response.java: cannot find symbol` - the snapshot does not even agree with itself,
* `JNAWindowsProcessHandler is not abstract and does not override abstract method
  isProcessesAlive(ProcessInfo...)` - the interface and the implementation in the same snapshot
  are from different revisions.

The trunk's own 5,256 files are never reached, and the engine core (`JDClosed`) is not in the
archive at all.

**Consequence: the supplied source cannot be built into a running JDownloader.** The "use
JDownloader as the engine" option is *not executable* with this input - independently of the
licence question below.

### 2.3 The MediaFire integration (the decisive finding)

The mirror contains a **complete MediaFire implementation**:

* `src/jd/plugins/hoster/MediafireCom.java` (904 lines) - the host plugin
  * the **core API 1.5** (`https://www.mediafire.com/api/1.5/<command>`), the same API the Python
    downloader uses
  * accounts (`FREE` / `PREMIUM` / `LIFETIME`), `session_token`, `ukey` cookies, a captcha helper
    and password protected files
  * `Browser.setRequestIntervalLimitGlobal(host, 250)` - a per host request interval
  * `isResumeable() -> true` and `getMaxChunks()` (free/anonymous: -15, premium: 0)
  * the **direct URL is stored per account type** (`getDirecturlProperty`) and therefore
    re-resolved when it expires - exactly what the Python downloader does
* `src/jd/plugins/decrypter/MediafireComFolder.java` (298 lines) - the link/folder decrypter
  * `/folder/<id>`, `/folder/<id>/shared`, comma separated multiple ids, single file links and the
    legacy `?quickkey=`, `/download.php?id=`, `/i?id=` forms, plus the direct CDN links
    (`download\d+.mediafire(?:cdn)?.com/...`)
  * `folder/get_content` over API 1.5
  * the domains `mediafire.com` and `mfi.re`

**This is the reference specification for ArkLoad's MediaFire support** - and it confirms that the
Python implementation is behaviourally equivalent (same API, same link types, same "resolve the
expiring direct URL when needed" rule, resume and chunking support).

### 2.4 Licence

The file headers state the **GNU General Public License, version 3 or later** (not v2, as a first
guess would suggest):

```
//    This program is free software: you can redistribute it and/or modify
//    it under the terms of the GNU General Public License as published by
//    the Free Software Foundation, either version 3 of the License, or
//    (at your option) any later version.
```

Consequences:

* Copying the MediaFire plugin (or any other code) into ArkLoad makes ArkLoad a **GPLv3** work: the
  licence text, the copyright notices and the corresponding source must be distributed with it.
* The closed modules cannot be used at all.
* Renaming the application does not remove these obligations.

## 3. The MediaFire downloader audit (this repository)

### 3.1 Structure and entry points

```
tools/mediafire_dl.py        the downloader: link parsing, planning, HTTP, ranges, hashing
tools/mediafire_resume.py    manifests, per file state detection, integrity verification
tools/mediafire_session.py   the worker: plan -> verify -> download -> verify -> recover
tools/mediafire_gui.py       the classic Tkinter window
tools/arkload/               the PySide6 application (window, model, engine, preferences)
```

* CLI: `python tools/mediafire_dl.py <url> -o <dir>`
* GUI: `python tools/arkload/main.py` (PySide6), `python tools/mediafire_gui.py` (Tk)
* Package: `python -m tools.arkload.main`, and `ArkLoad.exe` after packaging

### 3.2 Link resolution

`dl.parse_source()` accepts: `…/folder/<key>`, the legacy `…/folder/#<key>`, `…/file/<key>/<name>`,
`…/download/file/<key>/<name>`, the legacy `mediafire.com/?<key>` and a **bare folder key**
(`dpqhf3ughd4j5`). The same set the upstream decrypter handles, plus the bare key.

`dl.make_plan()` expands a folder through API 1.5 (`folder/get_content`, `files` and `folders`,
recursively) and produces one `PlannedFile` per entry with quickkey, relative path, size, page URL
and **the SHA-256 MediaFire publishes in the `hash` field** (verified: the API hash equals the
SHA-256 of the downloaded file).

Direct download URLs are never hardcoded: `resolve_download_url()` asks the API for
`normal_download` and falls back to scraping the file page when the API answers
`Insufficient Permissions`; they are re-resolved per attempt because they expire.

### 3.3 Names, duplicates, filters

Windows-safe names (`safe_name()`), reserved names prefixed, duplicates inside a folder get
` (2)`, ` (3)` …, and a file filter (`*.jar`, `mods/*`) is applied after planning.

### 3.4 HTTP

`urllib` only. A `User-Agent` is set, `Range: bytes=<offset>-` resumes, a `416` restarts from
zero, cookies (`ukey=…; user=…`) are supported for blocked files and **never written to disk**,
retries use exponential backoff (3 attempts per file by default).

### 3.5 State, detection, verification

One JSON manifest per download (`%LOCALAPPDATA%\ArkLoad\jobs` when packaged):

* source, destination, options, per file state, attempts, error, expected hash, actual hash
* states: `missing`, `incomplete`, `downloading`, `complete`, `verification_failed`, `failed`
* `scan()` classifies every file from the destination folder, `verify_record()` compares it with
  MediaFire's SHA-256; without an authoritative hash a locally recorded hash is the baseline and the
  result is reported as *locally verified*, never as *verified against the source*
* `validate()` re-checks files that were not touched since they were hashed

### 3.6 Recovery

```
plan -> reconcile with the manifest -> scan/verify what is on disk
      -> download only what is missing/incomplete/failed
      -> verify -> recovery pass (bounded) -> final validation
```

* `.part` files carry the partial data, HTTP range requests continue them
* **Acceleration**: a file can be split into N ranges fetched in parallel; the state of every range
  lives in `<file>.part.chunks.json`, so even a split transfer continues where it stopped
* **Speed limit**: a real throttle in the stream loop (bytes per second)
* **Pause / resume / cancel** are exposed by the session; the paused state is persisted, a cancelled
  download keeps its finished files and its `.part` files
* A job is *complete* only when every file is present **and** verified

### 3.7 Interface

* PySide6 (`tools/arkload/`): toolbar, download table with a progress bar, a priority chip and the
  remaining time, a details card (progress, speeds, path, links, per file states), a status bar
  with the speed limiter, the live speeds, the traffic priority and the selection
* Classic Tkinter window (`tools/mediafire_gui.py`), still shipped as the fallback
* Both drive the **same** session code

### 3.8 Tests

147 tests, offline apart from the live MediaFire checks: URL parsing, planning, scraping, ranges,
resuming, hashing, manifests, the session lifecycle (including a cancelled and a paused transfer),
the model, the engine, the window and the dialogs.

### 3.9 Known limitations

| Limitation | Detail |
|---|---|
| No host plugin system | one host (MediaFire) is built in |
| No link collection from a page | the browser extension only copies the current URL |
| No archive extraction | the files are stored as they are |
| No upload | the upload counter always reads 0 |
| No torrent support | a `.torrent` is refused with a clear message |
| No virus scan | the action exists but stays disabled (no scanner configured) |

## 4. Feature comparison

| Capability | JDownloader 2 (upstream) | ArkLoad (this repo) |
|---|---|---|
| MediaFire files and folders | complete, API 1.5, accounts, session tokens | complete, API 1.5, cookies, SHA-256 verification |
| Per file state | link status per task | per file: missing/incomplete/downloading/complete/verification failed/failed |
| Integrity verification | hash based (`HashInfo`) | SHA-256 against the source, plus a local baseline |
| Resume | yes (`isResumeable`) | yes, per range, with a persisted part state |
| Connections per file | `getMaxChunks()` | yes, parallel ranges + a persisted part state |
| Speed limit | yes | yes, a real throttle |
| Scheduling | yes | yes (start time) |
| Queue, pause, resume, cancel, retry | yes (closed core) | yes, one queue, one state machine |
| Persistence | yes (closed core) | yes, JSON manifests |
| Multi host | 1,422 host plugins | one host + an add-on API |
| Link collection | yes (hosthunter) | no |
| Archive extraction | yes | no |
| Accounts / premium | yes | no (anonymous + cookies) |
| Desktop interface | Swing (old parts public) | PySide6, ArkLoad's own design |
| Windows packaging | launch4j + NSIS | PyInstaller + Inno Setup, working |

## 5. Reusable components and duplication

| Component | Where | Verdict |
|---|---|---|
| MediaFire resolution, planning, name handling | `mediafire_dl.py` | **keep** - equivalent to the upstream plugin |
| Ranges, resume, retries, hashing, throttling | `mediafire_dl.py` | **keep** |
| Manifest, state detection, verification | `mediafire_resume.py` | **keep**, extend with a host neutral model |
| Session lifecycle, recovery passes | `mediafire_session.py` | **keep** |
| Table model, states, filters | `arkload/download_model.py` | **keep** |
| Window, dialogs, preferences, styles | `arkload/*` | **keep** - the ArkLoad interface |
| Upstream MediaFire plugin | JDownloader mirror | **use as the parity specification, not as code** (GPLv3) |
| Queue / scheduling / chunking / link collection / extraction | JDownloader closed core | **not available** - written by us where it matters |

## 6. The integration decision

The three approaches of the brief were evaluated against the audit:

| Approach | Verdict |
|---|---|
| **A - extend the Java implementation** | **Not executable.** The engine core is closed and the public snapshot does not compile (see 2.2.1). It would also make ArkLoad a GPLv3 work. |
| **B - Python as an internal helper** | **Its premise fails**: the Java side that would own the queue cannot be built or run. A helper without an engine to talk to is just the existing application. |
| **C - a separate frontend** | Only meaningful with a working engine interface. The existing PySide6 window already is that frontend, and it works. |

**Decision: keep one engine, one queue - the proven Python one - and take JDownloader's
*architecture* rather than its code.**

What is taken from JDownloader is the part that is genuinely good and can be reimplemented
without copyleft: the **host plugin contract**.  A host plugin recognises the links of one
service and resolves them into download links (the file name, the size, the checksum the
service publishes); the queue downloads whatever the plugin produced.  That is exactly what
`tools/arkload/hosts.py` does, with the MediaFire host as the first implementation and the
add-on folder as the discovery mechanism.

What is **not** taken: the Java engine, the Swing interface and the GPLv3 sources.  The
upstream MediaFire plugin is read as the specification (see `docs/MEDIAFIRE-PARITY.md`), not
copied.

The result is one queue, one state machine and one persistence layer - the property the brief
asks for - with the host plugin ecosystem as the extension point.

```
+---------------------------------------------------------------+
|  ArkLoad.exe  (PySide6)                                        |
|  window / sidebar / table / details card / preferences        |
+-----------------------------+---------------------------------+
|  ArkLoad core (Python)                                        |
|  queue:  TaskState, Task, TaskStore (queue.json)               |
|  hosts:  mediafire | <add-on host>  ->  DownloadLink            |
|  plugins: addon.json + setup(api) + create_host_plugin()       |
+-----------------------------+---------------------------------+
|  transport:  http (ranges, chunks, hashing, throttle)          |
|  storage:    manifests + verified baselines                    |
+---------------------------------------------------------------+
```

* **No second download engine.** One queue, one state machine, one persistence layer.
* **Host boundary**: a host turns a URL into `DownloadTask(url, filename, size, hash, headers,
  cookies)`. The MediaFire host is the implementation that already exists; a generic HTTP host and
  a user written plugin follow the same contract.
* **Plugin API**: an add-on is a folder with `addon.json` and `setup(api)` (already implemented in
  `arkload/addons.py`); it can register a host resolver, a post processor or a menu action.
* **The upstream plugin is the parity checklist** - see `docs/MEDIAFIRE-PARITY.md`.
* **The Java route is closed**: the engine core is not in the source and not open, so a Java based
  ArkLoad is not buildable from the supplied input. If the closed modules are ever supplied, the
  documented alternative (a localhost protocol between the ArkLoad UI and a Java engine) can be
  evaluated - but it would make ArkLoad a GPLv3 product.

## 7. Risks and licensing

| Risk | Level | Mitigation |
|---|---|---|
| JDownloader 2 is GPLv3 and its core is closed | **high, decisive** | ArkLoad keeps its own engine and stays permissively licensed. The upstream plugin is read as a specification, never copied. |
| The JDownloader engine cannot be built from the mirror | high | Confirmed by the missing `JDClosed` and the other five checkouts. The recommended architecture does not need it. |
| No Ant/Maven/Gradle installed | low | Only relevant for the Java route, which is not viable. |
| MediaFire's public API is undocumented and may change | medium | All API calls live in one module, the planner falls back to scraping, and the tests use recorded fixtures. |
| Expiring direct download URLs | low | Resolved per attempt, never stored (same rule as the upstream plugin). |
| Naming: "ArkLoad" / "Modern Download Manager" may collide with trademarks | low | A trademark and name availability check before the first public release. |
| Bundled licences (PySide6 LGPLv3, Qt, Python PSF) | low | Notices are collected in the installer. |

## 8. Phases

| Phase | Content | Acceptance |
|---|---|---|
| 1 | audit, licences, architecture (this document) | **done** |
| 2 | reproducible baseline from a fresh clone | the documented commands work end to end |
| 3 | MediaFire proof of concept in the ArkLoad queue | a real download reaches the destination, is verified, and the state is correct |
| 4 | core manager: queue, persistence, retries, settings, limits | the operations survive a restart, the UI stays responsive |
| 5 | ArkLoad interface: sidebar, list, add dialog, task controls, settings | every visible action is wired to real behaviour |
| 6 | advanced: plugin API, generic HTTP host, scheduling, extraction | each feature has a test |
| 7 | QA: interruption, invalid links, conflicts, cancellation, concurrency | the critical paths are covered |
| 8 | packaging and release | the installer works on a clean Windows machine |

**Next concrete step (Phase 2):** record the exact tool versions (Python 3.14, PySide6 6.12,
PyInstaller 6.22, Inno Setup 6.7) in the release notes, make `packaging\build.ps1` the single
documented build entry point, and verify a fresh clone produces the same three artifacts.
