
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

### The engines

ArkLoad brings its engines with it - the user does not install or configure anything:

* **ArkLoad itself** downloads the MediaFire links and verifies every file against the hash
  MediaFire publishes,
* **JDownloader 2** (with the Java runtime it needs) downloads the hosters and the websites it
  knows the plugins for,
* **aria2** downloads the direct links and the other simple protocols.

ArkLoad picks the engine on its own: a host it knows itself goes to its own downloader, a website
to JDownloader and a direct file to aria2.  When an engine reports that it does not know a
link, the next one may try it - but a download that failed because of a password, a permission or
an expired link is never handed to a second engine.

The engines are started in the background when they are needed, driven through their local
interfaces and stopped again when ArkLoad closes.  The preferences tell the user which engines
are there.

The engines are separate programs with their own licences (JDownloader 2 is GPLv3+/AGPL, aria2
is Apache 2.0, the Java runtime is GPLv2 with the Classpath Exception); the notice of them is
in the `engines` folder of the installation.

### Building the engines

The engines are not part of the repository (they are 260 MB of binaries).  They are built and
staged with one script:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_engines.ps1
```

It builds the JDownloader 2 engine from the official sources (or reuses the build of
`packaging\jdownloader\build_engine.ps1`), downloads the Java runtime (Eclipse Temurin) and the
official Windows build of aria2, and stages everything into `packaging\engines`.  The installer
puts them into `%LOCALAPPDATA%\ArkLoad\engines`, where the application finds them on its own.
