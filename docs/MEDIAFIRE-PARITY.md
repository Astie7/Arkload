# MediaFire parity checklist

The upstream JDownloader 2 MediaFire implementation
(`svn_trunk/src/jd/plugins/hoster/MediafireCom.java` and
`svn_trunk/src/jd/plugins/decrypter/MediafireComFolder.java`, **GPLv3**) is the reference for
ArkLoad's MediaFire behaviour. It is **read, never copied** - ArkLoad keeps its own permissive
implementation.

This checklist compares the two, so a regression in ArkLoad is visible.

## Link types

| Link | Upstream | ArkLoad | Test |
|---|---|---|---|
| `https://www.mediafire.com/folder/<key>` | `MediafireComFolder` (folder ids, `folder/get_content`) | `dl.parse_source` -> folder | `test_mediafire_dl.py::test_parse_source` |
| `https://www.mediafire.com/folder/<key>/shared` | `MediafireComFolder` | supported | `test_parse_source` |
| `https://www.mediafire.com/folder/<key1>,<key2>` | `MediafireComFolder` (multiple ids) | not yet | - |
| `https://www.mediafire.com/file/<key>/<name>` | `MediafireCom` (host plugin) | `dl.parse_source` -> file | `test_parse_source` |
| `https://www.mediafire.com/download/file/<key>/<name>` | `MediafireCom` | supported | `test_parse_source` |
| `https://mediafire.com/?<key>` (legacy) | `MediafireComFolder.getFileIDFRomURL` | supported | `test_parse_source` |
| `/download.php?id=<key>`, `/i?id=<key>` (legacy) | `MediafireComFolder.getFileIDFRomURL` | not yet | - |
| `download<N>.mediafire(cdn).com/...` (direct) | `MediafireComFolder.TYPE_DIRECT` | resolved on demand, never stored | `test_mediafire_dl.py` |
| a bare folder key (`dpqhf3ughd4j5`) | - | supported (ArkLoad addition) | `test_parse_source` |

## Resolution and API

| Behaviour | Upstream | ArkLoad |
|---|---|---|
| API | core API 1.5 | core API 1.5 |
| Folder listing | `folder/get_content` (files + folders, recursive) | the same |
| File info | `file/get_info` | the same |
| Direct URL | `file/get_links`, stored per account type, re-resolved when it expires | `resolve_download_url()`, re-resolved per attempt |
| API without permission | falls back to the file page | falls back to scraping the page |
| API key | a `key` parameter | none needed for the public API |
| Session token | `user/get_info.php` + `session_token` | not used (anonymous + cookies) |
| Accounts | free / premium / lifetime, with a captcha helper | not implemented |
| Rate limit | `Browser.setRequestIntervalLimitGlobal(host, 250)` | one request per 250 ms in the planner |
| Cookies | `ukey` | `ukey=…; user=…`, never written to disk |
| Password protected files | `PasswordSolver` | not implemented |
| Domains | `mediafire.com`, `mfi.re` | `mediafire.com` |

## Transfer behaviour

| Behaviour | Upstream | ArkLoad |
|---|---|---|
| Resume | `isResumeable() -> true` | HTTP `Range` from the `.part` offset |
| Connections per file | `getMaxChunks()` (free: -15, premium: 0) | `connections` (1-16), the ranges are fetched in parallel |
| Part state | engine internal | `<file>.part.chunks.json`, so a split transfer also resumes |
| Integrity | hash based (`HashInfo`) | SHA-256 from the API `hash` field; a local baseline when the source has none |
| Retries | engine internal | 3 attempts per file, exponential backoff, plus recovery passes |
| Speed limit | engine internal | a real throttle in the stream loop |
| Naming | the plugin name | Windows safe names, reserved names prefixed, duplicates suffixed |
| Folder packages | `FilePackage` | the destination folder mirrors the remote tree |

## Open parity gaps

1. multiple ids in one folder link (`/folder/<a>,<b>`),
2. the legacy `/download.php?id=` and `/i?id=` forms,
3. the `mfi.re` domain,
4. accounts (free/premium), session tokens and the captcha flow,
5. password protected files.

They are listed in the order of how often a user hits them; each one is a small, self contained
addition to `tools/mediafire_dl.py` plus a test.
