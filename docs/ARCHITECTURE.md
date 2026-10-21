# ArkLoad - Phase 1 audit and architecture report

**Date:** 2026-10-21 · **Author:** development audit · **Status:** Phase 1 complete, Phase 2 pending
the JDownloader 2 source.

## 1. What was inspected

| Input | Status | Evidence |
|---|---|---|
| The MediaFire downloader (this repository, formerly `tools/` of universal-modder) | **Inspected, first hand** | The full source (14 modules, ~4,500 lines), 147 passing tests, live downloads against `mediafire.com` |
| JDownloader 2 source / official development checkout | **NOT AVAILABLE** | `jdmn/jdownloader`, `jdmn/jdownloader-beta`, `jdmn/jd-gui`, `jdmn/jd-plugins`, `AppWork2/JDownloader` all return *Repository not found*; the `jdmn` account now lists a single unrelated repository. The upstream development repositories are no longer public. |

Because the JDownloader source could not be reached, **no JDownloader-specific claim in this
document is based on inspected code**. Everything about JDownloader below is either a licensing
fact or a stated requirement, and the parts that need code inspection are marked as *open
questions for the source handover*.

The Java toolchain on this machine: **Temurin JDK 25** is installed; **Maven and Gradle are
not**, and JDownloader 2 is built with Gradle.

## 2. MediaFire downloader audit (inspected)

### 2.1 Structure and entry points

```
tools/mediafire_dl.py        the downloader: URL parsing, planning, HTTP, ranges, hashing
tools/mediafire_resume.py    manifests, per file state detection, integrity verification
tools/mediafire_session.py   the worker: plan -> verify -> download -> verify -> recover
tools/mediafire_gui.py       the classic Tkinter window
tools/arkload/               the PySide6 application (window, model, engine, preferences)
```

* CLI entry point: `python tools/mediafire_dl.py <url> -o <dir>`
* GUI entry points: `python tools/arkload/main.py` (PySide6), `python tools/mediafire_gui.py` (Tk)
* Package entry point: `python -m tools.arkload.main`, and `ArkLoad.exe` after packaging

### 2.2 Link resolution

`dl.parse_source()` accepts:

| Input | Parsed as |
|---|---|
| `https://www.mediafire.com/folder/<key>` | folder (`kind="folder"`) |
| `https://www.mediafire.com/folder/#<key>` (legacy) | folder |
| `https://www.mediafire.com/file/<key>/<name>` | file |
| `https://www.mediafire.com/download/file/<key>/<name>` | file |
| `https://mediafire.com/?<key>` (legacy short link) | file |
| a bare key such as `dpqhf3ughd4j5` | folder |

`dl.make_plan()` expands a folder through the public API 1.5 (`folder/get_content`, both
`files` and `folders`, recursively) and builds a `PlannedFile` per entry with
`quickkey`, relative path, size, page URL and **the SHA-256 that MediaFire publishes** in the
`hash` field (verified: the API hash equals the SHA-256 of the downloaded file).

Direct download URLs are **not** hardcoded: `resolve_download_url()` asks the API for the
`normal_download` link, and falls back to scraping the file page when the API answers
`Insufficient Permissions`. Those direct URLs expire, so they are resolved per attempt.

### 2.3 Folders, files, duplicates

* A folder download mirrors the remote tree; names are sanitised for Windows
  (`safe_name()`), reserved names are prefixed, and duplicates inside one folder get
  ` (2)`, ` (3)` … suffixes.
* A file filter (`*.jar`, `mods/*`, comma separated) is applied after planning.

### 2.4 HTTP behaviour

* `urllib` only, no third party HTTP library.
* `User-Agent` is set, `Range: bytes=<offset>-` is used for resuming, and a `416` response
  restarts the file from zero.
* Cookies are supported (`ukey=…; user=…`) for files that MediaFire reports as blocked
  (typically files copied into a personal account). **They are never written to disk.**
* Retries with exponential backoff (default 3 attempts per file).

### 2.5 State, status detection, verification

`mediafire_resume.py` keeps one JSON manifest per download in `~/.mediafire-dl/jobs`
(`%LOCALAPPDATA%\ArkLoad` when packaged):

* source, destination, options, per file state, attempts, error, expected hash, actual hash
* states: `missing`, `incomplete`, `downloading`, `complete`, `verification_failed`, `failed`
* `scan()` classifies every file by looking at the destination folder
* `verify_record()` compares the file with MediaFire's SHA-256; without an authoritative hash a
  locally recorded hash is used as the baseline, and the result is reported as *locally
  verified*, never as *verified against the source*
* `validate()` re-checks the files that were not touched since they were hashed
* `record_result()` keeps the manifest in step with every finished transfer

### 2.6 Recovery

`mediafire_session.py` runs one download:

```
plan -> reconcile with the manifest -> scan/verify what is on disk
      -> download only what is missing/incomplete/failed
      -> verify -> recovery pass (bounded) -> final validation
```

* `.part` files carry the partial data, HTTP range requests continue them
* **Acceleration**: a file can be split into N ranges that are fetched in parallel; the state of
  every range is kept in `<file>.part.chunks.json`, so even a split transfer continues where
  it stopped
* **Speed limit**: a real token-bucket throttle in the stream loop (bytes/second)
* **Pause / resume / cancel**: the session exposes `set_paused()` and `request_stop()`; the
  paused state is persisted, a cancelled download keeps its finished files and its `.part` files
* The job is only *complete* when every file is present **and** verified

### 2.7 Interface

* PySide6 window (`tools/arkload/`): toolbar, download table with a progress bar, priority
  chip and remaining time, a details card (progress, speeds, path, links, per file states),
  a status bar with the speed limiter, the live speeds, the traffic priority and the selection
* Classic Tkinter window (`tools/mediafire_gui.py`), still shipped as the fallback
* Both drive the **same** session code, so they behave identically

### 2.8 Tests

`147` tests, all offline except the live MediaFire checks: URL parsing, planning, scraping,
ranges, resuming, hashing, manifests, the session lifecycle (including a cancelled and a
paused transfer), the model, the engine, the window and the dialogs.

### 2.9 Known limitations

| Limitation | Detail |
|---|---|
| No host plugin system | one host (MediaFire) is hard coded |
| No link checking / link collection from a page | the browser extension only copies the current URL |
| No archive extraction | the files are stored as they are |
| No upload | the status bar shows an upload counter that always reads 0 |
| No torrent support | a `.torrent` is refused with a clear message |
| No virus scan | the action exists but is disabled (no scanner is configured) |

## 3. JDownloader 2 audit - blocked

The repositories that hold the JDownloader 2 sources are not reachable from this machine or
from the public internet any more (all five known URLs answer *Repository not found*). The
following therefore remain **open questions** that the source handover has to answer:

1. the current build procedure (Gradle modules, the AppWork2 dependencies, the JDK version),
2. the queue and task lifecycle API (`jd.plugins.DownloadLink`, `DownloadController`, …),
3. the plugin discovery mechanism and the host plugin interface,
4. whether a MediaFire host plugin exists in the current tree and what it supports,
5. the persistence layer, the configuration and the log handling,
6. the Swing UI and the realistic cost of a redesign.

**Licensing (known, not code dependent):** JDownloader 2 is distributed under the **GPLv2**
(the sources carry that licence, the plugins are published under the same terms). A derivative
that uses its code is therefore a GPLv2 work: the licence text, the copyright notices and the
corresponding source must stay with the distribution, and a proprietary or differently licensed
ArkLoad could not ship JDownloader code in it. This is the decisive constraint of the whole
project and it is independent of the audit.

## 4. Feature comparison

| Capability | MediaFire downloader (this repo) | JDownloader 2 (as required) | ArkLoad today |
|---|---|---|---|
| MediaFire folders and files | **yes, verified** | host plugin (open question) | yes |
| Per file state and verification | **yes, SHA-256 against the source** | package/plugin dependent | yes |
| Resume | **yes, range requests, per part** | yes | yes |
| Parallel connections per file | **yes** | yes | yes |
| Speed limit | **yes, real throttle** | yes | yes |
| Queue, pause, resume, cancel, retry | **yes** | yes | yes |
| Persistence across restarts | **yes, manifests** | yes | yes |
| Multi host | no | **the plugin ecosystem** | no (one host) |
| Link collection from a page | no | yes (hosthunter) | no |
| Archive extraction | no | yes | no |
| Scheduling | yes (start time) | yes | yes |
| Browser integration | clipboard extension | yes | yes |
| Plugin / add-on system | add-on folder + hooks | **the host plugin system** | partial |
| Desktop interface | PySide6 + Tkinter | Swing | PySide6 (ArkLoad) |
| Windows packaging | PyInstaller + Inno Setup, working | none | working |

## 5. Reusable components and duplication

| Component | Where it lives | Verdict |
|---|---|---|
| MediaFire link resolution, folder planning, name sanitising | `mediafire_dl.py` | **keep, reuse as the MediaFire host implementation** |
| HTTP range / resume, retries, hashing, throttling | `mediafire_dl.py` | **keep** |
| Manifest, state detection, verification | `mediafire_resume.py` | **keep, extend with a host neutral model** |
| Session lifecycle, recovery passes | `mediafire_session.py` | **keep** |
| Table model, states, filters | `arkload/download_model.py` | **keep** |
| Window, dialogs, preferences, styles | `arkload/*` | **keep, they are the ArkLoad interface** |
| Queue / scheduling / chunking / link collection / extraction | JDownloader | **not available; would have to be written** |
| Host plugin system | JDownloader | **not available; a slimmer Python plugin API is proposed instead** |

Nothing in the MediaFire downloader is duplicated by JDownloader in a way that blocks the
project: the overlap is the download loop, and the JDownloader one is not reachable today.

## 6. Proposed architecture

Given the audit, the recommendation is **one engine, one queue, one source of truth**, built on
the code that exists and is proven, with a host boundary that leaves room for more sources later.

```
+---------------------------------------------------------------+
|  ArkLoad.exe  (PySide6)                                        |
|  window / table model / preferences / dialogs / packaging      |
+-----------------------------+---------------------------------+
|  ArkLoad core (Python)                                        |
|  queue:  task states, persistence, scheduling, limits, retries  |
|  hosts:  mediafire (done) | generic http (planned) | <plugin>  |
|  plugins: addon.json + hooks (host resolvers, post processors) |
+-----------------------------+---------------------------------+
|  transport:  http (ranges, chunks, hashing, throttle)          |
|  storage:    manifests + verified baselines                    |
+---------------------------------------------------------------+
```

* **No second download engine.** One queue, one state machine, one persistence layer.
* **Host boundary**: a host is an object that turns a URL into a list of
  `DownloadTask(url, filename, size, hash, headers, cookies)`. The MediaFire host is the
  implementation that already exists; a generic HTTP host and a user written plugin follow the
  same contract.
* **Plugin API**: an add-on is a folder with `addon.json` and a `setup(api)` function (this
  already exists in `arkload/addons.py`). It can register a host resolver, a post processor or
  a menu action. The JDownloader host plugin idea is preserved, without its GPLv2 baggage.
* **If the JDownloader source is supplied** and GPLv2 is acceptable for the product, the
  alternative is a documented localhost protocol between the ArkLoad UI and a Java engine
  process (`POST /resolve`, `POST /start`, `POST /pause`, event stream). That is a bigger
  project (two runtimes, two queues unless the Python side is reduced to a resolver only) and
  it is only worth it if the host plugin ecosystem is a hard requirement.

## 7. Risks and licensing

| Risk | Level | Mitigation |
|---|---|---|
| JDownloader 2 is GPLv2; a derivative ArkLoad would be GPLv2 | **high, decisive** | Decide the licence of the product before writing code. The recommended path (own engine, MIT) keeps ArkLoad free of copyleft. |
| The JDownloader sources may be unobtainable | high | The recommended architecture does not need them; the plugin ecosystem becomes our own, smaller API. |
| No Java build tool (Maven/Gradle) on this machine | medium | Only relevant if the Java route is chosen. |
| MediaFire's public API is undocumented and may change | medium | The API calls are isolated in one module, the planner falls back to scraping, and the tests use recorded fixtures. |
| Expiring direct download URLs | low | They are resolved per attempt, never stored. |
| Two queues (ours + a Java engine) fighting over state | high (only for the Java route) | Never run two engines; the Python side would be a resolver only. |
| Naming: "ArkLoad" and "Modern Download Manager" may collide with trademarks | low | A trademark and name availability check is planned before the first public release. |
| Bundled licences (PySide6 LGPLv3, Qt, Python PSF) | low | PySide6 is LGPL, fine for a redistributed app; the notices are collected in the installer. |

## 8. Phases

| Phase | Content | Acceptance |
|---|---|---|
| 1 | audit, licences, architecture (this document) | done |
| 2 | reproducible baseline: build and run ArkLoad from the sources on a clean environment | the documented commands work from a fresh clone |
| 3 | MediaFire proof of concept in the ArkLoad queue (already largely done) | a real download reaches the destination, is verified and shows the right state |
| 4 | core manager: queue, persistence, retries, settings, limits | the operations survive a restart, the UI stays responsive |
| 5 | ArkLoad interface: sidebar, list, add dialog, task controls, settings | every visible action is wired to real behaviour |
| 6 | advanced: plugin API, generic HTTP host, scheduling, extraction | each feature has a test |
| 7 | QA: interruption, invalid links, conflicts, cancellation, concurrency | the critical paths are covered |
| 8 | packaging and release | the installer works on a clean Windows machine |

**Next concrete step (Phase 2):** make the build reproducible from a fresh clone
(`packaging/build.ps1`), publish the repository, and record the exact tool versions
(Python 3.14, PySide6 6.12, PyInstaller 6.22, Inno Setup 6.7) in the release notes.
