#!/usr/bin/env python3
"""Download a MediaFire folder (recursively) or a single file to a directory of your choice.

    python tools/mediafire_dl.py https://www.mediafire.com/folder/abc123/My+Mod -o "D:\\Downloads"
    python tools/mediafire_dl.py https://www.mediafire.com/folder/abc123/My+Mod -o D:\\Downloads --flat
    python tools/mediafire_dl.py https://www.mediafire.com/file/abc123/thing.zip/file -o D:\\Downloads

A folder creates <out>/<folder name>/ and keeps MediaFire's subfolders. --flat drops the files straight
into <out> instead. A single-file link goes straight into <out>.

Downloads are resumable: each file is written to <name>.part and only renamed when the byte count matches
MediaFire's, so re-running the same command after Ctrl+C, a dropped connection or a timeout continues where
it stopped. Files that already exist at the right size are skipped unless --overwrite is given.

Only the public MediaFire web/API endpoints are used, with no third-party packages (Python 3.10+):
the folder API lists the tree, and each file's normal download page is scraped for its current CDN link,
which works even when the uploader disabled API direct downloads.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import html
import http.client
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

API = "https://www.mediafire.com/api/1.5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PAGE = 100          # folder/get_content caps chunk_size at 100
BLOCK = 1 << 20     # 1 MiB

# Windows refuses these characters in a name; MediaFire accepts them.
_BAD_CHARS = set('<>:"/\\|?*')
_RESERVED = ({f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
             | {"CON", "PRN", "AUX", "NUL"})
_MAX_NAME = 120     # keep individual path components well under Windows' 260-character limit


class MediaFireError(RuntimeError):
    """A MediaFire/API/network failure worth retrying (rate limit, timeout, dropped connection)."""


class PermanentError(MediaFireError):
    """A file that will not appear on a retry: password-protected, removed or blocked."""


class Cancelled(Exception):
    """The user stopped the transfer; the .part file stays behind for the next run."""


# --------------------------------------------------------------------------- URLs and names

@dataclasses.dataclass(frozen=True)
class Source:
    kind: str   # "folder" | "file"
    key: str


def parse_source(url: str) -> Source:
    """Accept a MediaFire folder/file URL, a bare folder key or a short file link."""
    raw = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        return Source("folder", raw)  # the old console client's `ArkLoad <folder key>`
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host != "mediafire.com" and not host.endswith(".mediafire.com"):
        raise MediaFireError(f"not a MediaFire URL: {url}")
    path = urllib.parse.unquote(parts.path)
    m = re.search(r"/folder/([^/]+)", path, re.I)
    if m:
        return Source("folder", m.group(1))
    # Very old folder links put the key in the fragment: /folder/#folderkey
    fragment = urllib.parse.unquote(parts.fragment).strip()
    if re.fullmatch(r"/folder/?", path, re.I) and re.fullmatch(r"[A-Za-z0-9_-]+", fragment):
        return Source("folder", fragment)
    m = re.search(r"/(?:file|download(?:/file)?)/([^/]+)", path, re.I)
    if m:
        return Source("file", m.group(1))
    # Old short links: https://www.mediafire.com/?quickkey
    key = urllib.parse.unquote(parts.query).strip()
    if parts.path in ("", "/") and re.fullmatch(r"[A-Za-z0-9_-]+", key or ""):
        return Source("file", key)
    raise MediaFireError("not a MediaFire folder or file link (expected /folder/<key> or /file/<key>)")


def safe_name(name: str, limit: int = _MAX_NAME) -> str:
    """Make one path component safe on Windows, macOS and Linux without changing meaningful characters."""
    name = unicodedata.normalize("NFC", str(name))
    cleaned = "".join("_" if (ch in _BAD_CHARS or ord(ch) < 32) else ch for ch in name)
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned or cleaned in (".", ".."):
        cleaned = "_"
    if cleaned.split(".", 1)[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    if len(cleaned) > limit:
        stem, ext = split_ext(cleaned)
        ext = ext[:20]
        cleaned = stem[:max(1, limit - len(ext))].rstrip(". ") + ext
    return cleaned


def split_ext(name: str) -> tuple[str, str]:
    i = name.rfind(".")
    return (name[:i], name[i:]) if i > 0 else (name, "")


def unique_name(name: str, used: set[str]) -> str:
    """MediaFire allows two files (or a file and a folder) with the same name; suffix the second one."""
    candidate = name
    stem, ext = split_ext(name)
    n = 2
    while candidate.casefold() in used:
        suffix = f" ({n})"
        candidate = f"{stem[:max(1, _MAX_NAME - len(ext) - len(suffix))].rstrip('. ')}{suffix}{ext}"
        n += 1
    return candidate


def _file_hash(value) -> str | None:
    """MediaFire's API exposes a SHA-256 in `hash`; ignore anything else rather than trust it."""
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
        return value.strip().lower()
    return None


def fmt_size(n: int | None) -> str:
    if n is None:
        return "unknown size"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def _int_size(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- MediaFire API

def _request(url: str, headers: dict | None = None, timeout: float = 60) -> urllib.request.Request:
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
    if headers:
        h.update(headers)
    return urllib.request.Request(url, headers=h)


def _cookie_header(cookie: str | None, session_token: str | None) -> str | None:
    """Browser cookies (`ukey=...; user=...`) and/or an API session token, as one Cookie header."""
    parts = [cookie.strip()] if cookie and cookie.strip() else []
    if session_token:
        parts.append(f"session_token={session_token}")
    return "; ".join(parts) or None


def _get(url: str, headers: dict | None = None, timeout: float = 60, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(_request(url, headers, timeout), timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raw = e.read(2000).decode("utf-8", "replace")
            detail = ""
            try:  # MediaFire puts the useful sentence in a JSON body even on 404
                response = (json.loads(raw).get("response") or {})
                detail = str(response.get("message") or response.get("error") or "")
            except (ValueError, AttributeError):
                detail = " ".join(raw.split())[:300]
            last = MediaFireError(f"HTTP {e.code} from MediaFire" + (f": {detail}" if detail else ""))
            if e.code not in (408, 425, 429, 500, 502, 503, 504) or attempt == attempts:
                raise last from e
        except urllib.error.URLError as e:
            last = MediaFireError(f"cannot reach MediaFire: {e.reason}")
            if attempt == attempts:
                raise last from e
        time.sleep(min(10.0, 1.0 * (2 ** (attempt - 1))))
    raise last or MediaFireError("cannot reach MediaFire")


def _api(action: str, session_token: str | None = None, cookie: str | None = None, **params):
    """Call an API v1.5 action and return its `response` object, raising on MediaFire's soft errors."""
    params = {k: v for k, v in params.items() if v is not None}
    params["response_format"] = "json"
    if session_token:
        params["session_token"] = session_token
    url = f"{API}/{action}.php?{urllib.parse.urlencode(params)}"
    cookie_header = _cookie_header(cookie, session_token)
    body = _get(url, {"Cookie": cookie_header} if cookie_header else None)
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as e:
        raise MediaFireError(f"MediaFire returned {len(body)} bytes of non-JSON for {action}") from e
    resp = doc.get("response") or {}
    if resp.get("result") != "Success":
        message = resp.get("message") or resp.get("error") or "unknown error"
        raise MediaFireError(f"{action}: {message}")
    return resp


def folder_info(key: str, session_token: str | None = None, cookie: str | None = None) -> dict:
    return _api("folder/get_info", session_token, cookie, folder_key=key).get("folder_info") or {}


def file_info(key: str, session_token: str | None = None, cookie: str | None = None) -> dict:
    return _api("file/get_info", session_token, cookie, quick_key=key).get("file_info") or {}


def folder_content(key: str, kind: str, session_token: str | None = None, cookie: str | None = None):
    """Yield every file or folder entry in a folder, following the API's 100-entry chunks."""
    chunk = 1
    while True:
        resp = _api("folder/get_content", session_token, cookie, folder_key=key, content_type=kind,
                    chunk=chunk, chunk_size=PAGE, order_by="name", order_direction="asc")
        content = resp.get("folder_content") or {}
        yield from content.get(kind) or []
        if content.get("more_chunks") != "yes":
            return
        chunk += 1


# --------------------------------------------------------------------------- planning

@dataclasses.dataclass(frozen=True)
class PlannedFile:
    quickkey: str
    rel: PurePosixPath
    size: int | None
    page_url: str | None
    sha256: str | None = None


@dataclasses.dataclass
class Plan:
    kind: str
    root_name: str
    files: list[PlannedFile]
    folders: int

    @property
    def total_bytes(self) -> int:
        return sum(f.size or 0 for f in self.files)


def plan_folder(key: str, name: str | None = None, session_token: str | None = None,
                cookie: str | None = None) -> Plan:
    info = folder_info(key, session_token, cookie)
    root_name = safe_name(name or info.get("name") or key)
    files: list[PlannedFile] = []
    used_by_dir: dict[str, set[str]] = {}
    folders = 0

    def walk(folder_key: str, rel_dir: PurePosixPath, ancestors: frozenset[str]):
        nonlocal folders
        if folder_key in ancestors:  # a malformed tree must not recurse forever
            return
        used = used_by_dir.setdefault(rel_dir.as_posix(), set())

        for entry in folder_content(folder_key, "files", session_token, cookie):
            base = safe_name(entry.get("filename") or entry.get("name") or entry.get("quickkey") or "file")
            child = unique_name(base, used)
            used.add(child.casefold())
            links = entry.get("links") or {}
            page = links.get("normal_download") or (
                f"https://www.mediafire.com/file/{entry.get('quickkey', '')}/"
                f"{urllib.parse.quote(child, safe='')}/file")
            files.append(PlannedFile(str(entry.get("quickkey") or ""), rel_dir / child,
                                     _int_size(entry.get("size")), page,
                                     _file_hash(entry.get("hash"))))

        for entry in folder_content(folder_key, "folders", session_token, cookie):
            child_key = str(entry.get("folderkey") or "")
            if not child_key:
                continue
            base = safe_name(entry.get("name") or child_key)
            child = unique_name(base, used)
            used.add(child.casefold())
            folders += 1
            walk(child_key, rel_dir / child, ancestors | {folder_key})

    walk(key, PurePosixPath(), frozenset())
    return Plan("folder", root_name, files, folders)


def plan_file(key: str, session_token: str | None = None, cookie: str | None = None) -> Plan:
    info = file_info(key, session_token, cookie)
    name = safe_name(info.get("filename") or key)
    links = info.get("links") or {}
    page = links.get("normal_download") or (
        f"https://www.mediafire.com/file/{key}/{urllib.parse.quote(name, safe='')}/file")
    return Plan("file", "", [PlannedFile(key, PurePosixPath(name), _int_size(info.get("size")), page,
                                        _file_hash(info.get("hash")))], 0)


def make_plan(source: Source, name: str | None = None, session_token: str | None = None,
              cookie: str | None = None) -> Plan:
    if source.kind == "folder":
        return plan_folder(source.key, name, session_token, cookie)
    return plan_file(source.key, session_token, cookie)


# --------------------------------------------------------------------------- download links

_BUTTON = re.compile(r"<a\b[^>]*\bid=[\"']downloadButton[\"'][^>]*>", re.I)
_HREF = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.I)
_CDN = re.compile(r"https://download\d*\.mediafire\.com/[^\s\"'<>]+")
_TAG = re.compile(r"<[^>]+>")


def scrape_download_url(page: str) -> str:
    """Find the current CDN URL on a file page (direct API links are often disabled by the uploader)."""
    button = _BUTTON.search(page)
    if button:
        href = _HREF.search(button.group(0))
        if href:
            return html.unescape(href.group(1))
    fallback = _CDN.search(page)
    if fallback:
        return html.unescape(fallback.group(0))

    text = " ".join(_TAG.sub(" ", page).split()).lower()
    if "password" in text and ("form" in text or "enter" in text):
        raise PermanentError("file is password-protected; download it once in a browser and use that link")
    if "removed" in text or "deleted" in text:
        raise PermanentError("file was removed or deleted from MediaFire")
    if "dangerous file" in text or "blocked" in text:
        raise PermanentError("MediaFire blocked this file as dangerous")
    raise PermanentError("could not find a download link on the file page (private, removed or blocked file?)")


def resolve_download_url(item: PlannedFile, session_token: str | None = None,
                         cookie: str | None = None) -> str:
    """Prefer the API's direct link; otherwise scrape the normal download page for the CDN URL."""
    try:
        links = _api("file/get_links", session_token, cookie, quick_key=item.quickkey,
                     link_type="direct_download").get("links") or []
        direct = (links[0] or {}).get("direct_download") if links else None
        if direct:
            return direct
    except MediaFireError:
        pass  # direct downloads off is normal; the page always has a link
    if not item.page_url:
        raise MediaFireError("no download page for this file")
    cookie_header = _cookie_header(cookie, session_token)
    page = _get(item.page_url, {"Cookie": cookie_header} if cookie_header else None).decode("utf-8", "replace")
    return scrape_download_url(page)


# --------------------------------------------------------------------------- downloading

@dataclasses.dataclass
class Result:
    status: str                 # "ok" | "skip" | "failed" | "cancelled"
    size: int = 0
    seconds: float = 0.0
    error: str = ""
    sha256: str = ""
    verified: bool = False      # the bytes matched MediaFire's own SHA-256


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    """Hash a file without loading it into memory; cancellable between blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            block = fh.read(BLOCK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _wait_while_paused(pause: threading.Event | None,
                       cancel: threading.Event | None) -> None:
    while pause is not None and pause.is_set():
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        time.sleep(0.1)


def _throttle(started: float, transferred: int, rate_limit: int,
              cancel: threading.Event | None, pause: threading.Event | None) -> None:
    """Keep the transfer under `rate_limit` bytes per second (0 means no limit).

    The sleep is split into small steps so that a cancel or a pause is noticed quickly.
    """
    if not rate_limit or rate_limit <= 0:
        return
    target = transferred / float(rate_limit)
    elapsed = time.monotonic() - started
    delay = target - elapsed
    if delay <= 0:
        return
    deadline = time.monotonic() + delay
    while True:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if pause is not None and pause.is_set():
            _wait_while_paused(pause, cancel)
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _stream(url: str, part: Path, expected: int | None, fresh: bool,
            progress=None, timeout: float = 60, cookie: str | None = None,
            cancel: threading.Event | None = None,
            pause: threading.Event | None = None,
            rate_limit: int = 0) -> int:
    """Download `url` into `part`, resuming with Range when the server supports it. Returns bytes on disk."""
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    _wait_while_paused(pause, cancel)
    if fresh:
        part.unlink(missing_ok=True)
    offset = part.stat().st_size if part.exists() else 0
    if expected is not None and offset == expected:
        if not part.exists():  # a zero-byte file never needs a request
            part.parent.mkdir(parents=True, exist_ok=True)
            part.touch()
        return offset
    if expected is not None and offset > expected:
        part.unlink(missing_ok=True)
        offset = 0

    headers = {"Accept": "*/*"}
    if cookie:
        headers["Cookie"] = cookie
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        resp = urllib.request.urlopen(_request(url, headers, timeout), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416 and offset:
            # The .part is longer than what the server has now; start over.
            part.unlink(missing_ok=True)
            raise MediaFireError("resume range rejected; restarting from zero") from e
        raise MediaFireError(f"HTTP {e.code} while downloading") from e
    except urllib.error.URLError as e:
        raise MediaFireError(f"download failed: {e.reason}") from e

    with resp:
        if offset and resp.status != 206:
            offset = 0  # server ignored Range and sent the whole file
        total = expected
        if total is None:
            length = resp.headers.get("Content-Length")
            total = (offset + int(length)) if length and length.isdigit() else None
        mode = "ab" if offset else "wb"
        written = offset
        transferred = 0            # only the bytes of this session count against the limit
        started = time.monotonic()
        part.parent.mkdir(parents=True, exist_ok=True)
        with open(part, mode) as fh:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                _wait_while_paused(pause, cancel)
                block = resp.read(BLOCK)
                if not block:
                    break
                fh.write(block)
                written += len(block)
                transferred += len(block)
                _throttle(started, transferred, rate_limit, cancel, pause)
                if progress:
                    progress(written, total)
    return written


def _chunk_ranges(size: int, count: int) -> list[tuple[int, int]]:
    """Split [0, size) into `count` ranges that cover the file exactly once."""
    count = max(1, min(count, 32))
    if size <= 0:
        return [(0, 0)]
    per = size // count
    ranges = []
    start = 0
    for index in range(count):
        end = size - 1 if index == count - 1 else start + per - 1
        if end < start:
            end = start
        ranges.append((start, end))
        start = end + 1
    return ranges


def _range_supported(url: str, headers: dict, timeout: float) -> bool:
    """Ask the server for one byte; a 206 means it can send parts of the file."""
    request = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 206
    except Exception:  # noqa: BLE001 - any problem means we simply do not split
        return False


def _load_chunk_state(path: Path, url: str, size: int, ranges: list[tuple[int, int]]):
    """The state of a split download, so a restart continues with the finished parts."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("url") != url or int(data.get("size") or 0) != size:
        return None
    stored = data.get("ranges")
    if not isinstance(stored, list) or [tuple(item) for item in stored] != ranges:
        return None
    done = [int(value) for value in (data.get("done") or [])]
    if len(done) != len(ranges):
        return None
    return done


def _save_chunk_state(path: Path, url: str, size: int, ranges: list[tuple[int, int]],
                      done: list[int]) -> None:
    payload = {"url": url, "size": size, "ranges": [list(item) for item in ranges], "done": done}
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _download_chunked(url: str, part: Path, expected: int | None, connections: int,
                      progress=None, timeout: float = 60, cookie: str | None = None,
                      cancel: threading.Event | None = None,
                      pause: threading.Event | None = None,
                      rate_limit: int = 0) -> int:
    """Download one file with several connections at once (the "acceleration" of FDM).

    The file is split into ranges that are fetched in parallel and written into the same .part
    file.  The state of every range is stored next to it, so an interrupted download continues
    with the parts that are already there.  Returns the number of bytes on disk.
    """
    headers = {"Accept": "*/*"}
    if cookie:
        headers["Cookie"] = cookie
    if not _range_supported(url, headers, timeout):
        raise MediaFireError("the server does not support partial downloads")

    if expected is None:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            expected = int(length) if length and length.isdigit() else None
    if not expected:
        raise MediaFireError("the size of the file is unknown")

    ranges = _chunk_ranges(expected, connections)
    state_path = part.with_name(part.name + ".chunks.json")
    done = _load_chunk_state(state_path, url, expected, ranges) or [0] * len(ranges)
    done = [min(max(0, value), ranges[index][1] - ranges[index][0] + 1)
            for index, value in enumerate(done)]

    part.parent.mkdir(parents=True, exist_ok=True)
    if not part.exists() or part.stat().st_size != expected:
        with open(part, "wb") as handle:
            handle.truncate(expected)
    total_done = sum(done)
    if progress:
        progress(total_done, expected)

    lock = threading.Lock()
    errors: list[Exception] = []
    started = time.monotonic()

    def worker(index: int) -> None:
        start, end = ranges[index]
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    return
                _wait_while_paused(pause, cancel)
                already = done[index]
                if already >= end - start + 1:
                    return
                request_headers = dict(headers)
                request_headers["Range"] = f"bytes={start + already}-{end}"
                response = urllib.request.urlopen(
                    urllib.request.Request(url, headers=request_headers), timeout=timeout)
                with response:
                    if response.status != 206:
                        raise MediaFireError("the server ignored the range request")
                    with open(part, "r+b") as handle:
                        handle.seek(start + already)
                        while True:
                            if cancel is not None and cancel.is_set():
                                return
                            _wait_while_paused(pause, cancel)
                            block = response.read(BLOCK)
                            if not block:
                                break
                            handle.write(block)
                            with lock:
                                done[index] += len(block)
                                total = sum(done)
                            _throttle(started, total, rate_limit, cancel, pause)
                            if progress:
                                progress(total, expected)
                return
        except Exception as e:  # noqa: BLE001 - the caller retries the file
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(index,), daemon=True)
               for index in range(len(ranges))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if cancel is not None and cancel.is_set():
        _save_chunk_state(state_path, url, expected, ranges, done)
        raise Cancelled()
    if errors:
        _save_chunk_state(state_path, url, expected, ranges, done)
        raise errors[0]
    if sum(done) != expected:
        _save_chunk_state(state_path, url, expected, ranges, done)
        raise MediaFireError(f"only {sum(done)} of {expected} bytes arrived")
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass
    return expected


def download_one(item: PlannedFile, root: Path, *, overwrite: bool, retries: int, timeout: float,
                 session_token: str | None, cookie: str | None = None,
                 cancel: threading.Event | None = None, pause: threading.Event | None = None,
                 progress=None, force: bool = False, rate_limit: int = 0,
                 connections: int = 1) -> Result:
    """Download one file, resuming a .part when possible and verifying MediaFire's SHA-256.

    `force` bypasses the "already complete" shortcut so a file whose local hash is known to be
    wrong can be replaced without deleting it before a successful download.
    `rate_limit` caps the transfer in bytes per second (0 means unlimited).
    `connections` splits the file into that many parts that are fetched at the same time.
    """
    if cancel is not None and cancel.is_set():
        return Result("cancelled")
    final = root.joinpath(*item.rel.parts)
    part = final.with_name(final.name + ".part")
    redownload = force
    try:
        if not overwrite and not force and final.is_file():
            actual_size = final.stat().st_size
            if item.size is None or actual_size == item.size:
                actual_hash = sha256_file(final, cancel)
                if not item.sha256 or actual_hash.lower() == item.sha256.lower():
                    return Result("skip", actual_size, sha256=actual_hash,
                                  verified=bool(item.sha256))
                # The file on disk is not the file MediaFire is serving now.
                redownload = True
    except Cancelled:
        return Result("cancelled")

    final.parent.mkdir(parents=True, exist_ok=True)
    if item.size == 0:
        final.write_bytes(b"")
        try:
            actual_hash = sha256_file(final, cancel)
        except Cancelled:
            return Result("cancelled")
        if item.sha256 and actual_hash.lower() != item.sha256.lower():
            final.unlink(missing_ok=True)
            return Result("failed", error="sha256 mismatch on empty file")
        return Result("ok", 0, sha256=actual_hash, verified=bool(item.sha256))

    started = time.monotonic()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        if cancel is not None and cancel.is_set():
            return Result("cancelled")
        try:
            url = resolve_download_url(item, session_token, cookie)
            if connections > 1 and item.size:
                written = _download_chunked(
                    url, part, item.size, connections, progress=progress, timeout=timeout,
                    cookie=cookie, cancel=cancel, pause=pause, rate_limit=rate_limit)
            else:
                written = _stream(url, part, item.size,
                                  fresh=(overwrite or redownload) and attempt == 1,
                                  progress=progress, timeout=timeout, cookie=cookie, cancel=cancel,
                                  pause=pause, rate_limit=rate_limit)
            if item.size is not None and written != item.size:
                raise MediaFireError(f"size mismatch: expected {item.size} bytes, got {written}")
            actual_hash = sha256_file(part, cancel)
            if item.sha256 and actual_hash.lower() != item.sha256.lower():
                # Never leave corrupt bytes behind for the next attempt to resume.
                part.unlink(missing_ok=True)
                raise MediaFireError(
                    f"sha256 mismatch: expected {item.sha256}, got {actual_hash}")
            os.replace(part, final)
            return Result("ok", written, time.monotonic() - started, sha256=actual_hash,
                          verified=bool(item.sha256))
        except Cancelled:
            return Result("cancelled", seconds=time.monotonic() - started)
        except PermanentError as e:
            return Result("failed", error=str(e), seconds=time.monotonic() - started)
        except (MediaFireError, urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException) as e:
            last = e
            if attempt < retries:
                time.sleep(min(20.0, 1.5 * (2 ** (attempt - 1))))
    return Result("failed", error=str(last), seconds=time.monotonic() - started)


# --------------------------------------------------------------------------- reporting

class Reporter:
    """Thread-safe per-file lines, plus a single-file progress bar when stderr is a terminal."""

    def __init__(self, total: int, quiet: bool = False, bar: bool = False):
        self.total = total
        self.quiet = quiet
        self.bar = bar and not quiet
        self.lock = threading.Lock()
        self.last_update = 0.0
        self.bar_len = 0

    def _out(self, text: str, end: str = "\n"):
        print(text, file=sys.stderr, end=end, flush=True)

    def start(self, index: int, item: PlannedFile):
        if not self.bar:
            return
        with self.lock:
            self.last_update = 0.0
            self._bar(index, item, 0, item.size)

    def update(self, index: int, item: PlannedFile, done: int, total: int | None):
        if not self.bar:
            return
        now = time.monotonic()
        if now - self.last_update < 0.15 and done != total:
            return
        self.last_update = now
        with self.lock:
            self._bar(index, item, done, total)

    def _bar(self, index: int, item: PlannedFile, done: int, total: int | None):
        pct = f"{100 * done / total:3.0f}%" if total else " ??%"
        line = f"[{index}/{self.total}] {item.rel.as_posix()}  {pct} ({fmt_size(done)})"
        pad = " " * max(0, self.bar_len - len(line))
        self._out("\r" + line + pad, end="")
        self.bar_len = len(line)

    def result(self, index: int, item: PlannedFile, result: Result):
        if self.quiet and result.status != "failed":
            return
        stamp = f"{result.seconds:5.1f}s" if result.seconds else "     "
        if result.status == "ok":
            line = f"[{index}/{self.total}] ok   {item.rel.as_posix()}  ({fmt_size(result.size)}, {stamp})"
        elif result.status == "skip":
            line = f"[{index}/{self.total}] skip {item.rel.as_posix()}  ({fmt_size(result.size)})"
        elif result.status == "cancelled":
            line = f"[{index}/{self.total}] stop {item.rel.as_posix()}"
        else:
            line = f"[{index}/{self.total}] FAIL {item.rel.as_posix()}  {result.error}"
        with self.lock:
            if self.bar:
                line += " " * max(0, self.bar_len - len(line))
                self._out("\r" + line)
                self.bar_len = 0
            else:
                self._out(line)


def print_plan(plan: Plan, root: Path, as_json: bool):
    if as_json:
        print(json.dumps({
            "kind": plan.kind,
            "root": str(root),
            "files": [{"path": f.rel.as_posix(), "size": f.size, "quickkey": f.quickkey,
                       "sha256": f.sha256}
                      for f in plan.files],
            "folders": plan.folders,
            "total_bytes": plan.total_bytes,
        }, indent=2))
        return
    print(f"{root}{os.sep}")
    width = max((len(f.rel.as_posix()) for f in plan.files), default=0)
    for f in plan.files:
        print(f"  {f.rel.as_posix():<{width}}  {fmt_size(f.size)}")
    print(f"{len(plan.files)} files in {plan.folders} folders, {fmt_size(plan.total_bytes)}")


# --------------------------------------------------------------------------- CLI

def _die(message: str, code: int = 1):
    print(f"mediafire-dl: {message}", file=sys.stderr)
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    ap = argparse.ArgumentParser(
        prog="mediafire_dl.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="MediaFire folder/file link or a bare folder key")
    ap.add_argument("-o", "--out", required=True, metavar="DIR",
                    help="destination directory (a folder link also creates <DIR>/<folder name>)")
    ap.add_argument("--flat", action="store_true", help="put a folder's contents straight into DIR")
    ap.add_argument("--name", help="override the folder name created inside DIR")
    ap.add_argument("-j", "--jobs", type=int, default=4, metavar="N",
                    help="parallel downloads (default 4)")
    ap.add_argument("--retries", type=int, default=3, metavar="N", help="attempts per file (default 3)")
    ap.add_argument("--timeout", type=float, default=60, metavar="SEC", help="network timeout (default 60)")
    ap.add_argument("--overwrite", action="store_true", help="re-download files that already exist")
    ap.add_argument("--session-token", help="MediaFire session token, for a private/shared folder")
    ap.add_argument("--cookie", metavar="'ukey=...; user=...'",
                    help="browser cookies, for blocked files copied into your own MediaFire account")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded and stop")
    ap.add_argument("--json", action="store_true", help="machine-readable plan/results on stdout")
    ap.add_argument("-q", "--quiet", action="store_true", help="only errors and the summary")
    args = ap.parse_args(argv)

    if args.jobs < 1:
        _die("--jobs must be at least 1")
    if args.retries < 1:
        _die("--retries must be at least 1")
    if args.flat and args.name:
        _die("--name and --flat cannot be combined")

    try:
        source = parse_source(args.url)
        plan = make_plan(source, args.name if source.kind == "folder" else None,
                         args.session_token, args.cookie)
    except MediaFireError as e:
        _die(str(e))

    out = Path(args.out).expanduser()
    root = out if (source.kind == "file" or args.flat) else out / plan.root_name
    if args.dry_run:
        print_plan(plan, root, args.json)
        return 0
    if not plan.files:
        if args.json:
            print(json.dumps({"root": str(root), "files": 0, "downloaded": 0, "skipped": 0,
                              "failed": 0, "bytes": 0, "seconds": 0.0, "errors": []}, indent=2))
        elif not args.quiet:
            print(f"{root}: the folder is empty")
        return 0

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _die(f"cannot create {root}: {e}")

    if not args.json and not args.quiet:
        print(f"Downloading {len(plan.files)} files ({fmt_size(plan.total_bytes)}) to {root}", file=sys.stderr)
    reporter = Reporter(len(plan.files), quiet=args.quiet,
                        bar=args.jobs == 1 and sys.stderr.isatty())
    stats = {"ok": 0, "skip": 0, "failed": 0, "bytes": 0, "verified": 0}
    failures: list[tuple[str, str]] = []
    started = time.monotonic()

    def work(index: int, item: PlannedFile) -> tuple[int, PlannedFile, Result]:
        reporter.start(index, item)
        try:
            result = download_one(
                item, root, overwrite=args.overwrite, retries=args.retries, timeout=args.timeout,
                session_token=args.session_token, cookie=args.cookie,
                progress=lambda done, total, i=index, it=item: reporter.update(i, it, done, total))
        except Exception as e:  # one unwritable file must not abandon the rest of the folder
            result = Result("failed", error=f"{type(e).__name__}: {e}")
        return index, item, result

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(work, i, f) for i, f in enumerate(plan.files, 1)]
            for future in concurrent.futures.as_completed(futures):
                index, item, result = future.result()
                reporter.result(index, item, result)
                stats[result.status] = stats.get(result.status, 0) + 1
                if result.status == "ok":
                    stats["bytes"] += result.size
                if result.verified:
                    stats["verified"] += 1
                if result.status == "failed":
                    failures.append((item.rel.as_posix(), result.error))
    except KeyboardInterrupt:
        print("\ninterrupted; partial files are kept as .part and the next run resumes", file=sys.stderr)
        return 130

    elapsed = time.monotonic() - started
    if args.json:
        print(json.dumps({
            "root": str(root), "files": len(plan.files),
            "downloaded": stats["ok"], "skipped": stats["skip"], "failed": stats["failed"],
            "verified": stats["verified"],
            "bytes": stats["bytes"], "seconds": round(elapsed, 1),
            "errors": [{"path": path, "error": error} for path, error in failures],
        }, indent=2))
    else:
        print(f"Done in {elapsed:.1f}s: {stats['ok']} downloaded, {stats['skip']} skipped, "
              f"{stats['failed']} failed, {stats['verified']} verified against MediaFire's "
              f"SHA-256, {fmt_size(stats['bytes'])} -> {root}", file=sys.stderr)
    if failures:
        for path, error in failures:
            print(f"  {path}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
