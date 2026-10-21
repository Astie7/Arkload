"""The browser extension.

The application writes a small unpacked extension into ``~/.mediafire-dl/browser-extension``.
It puts the MediaFire link of the current tab on the clipboard, so the download can be added in
ArkLoad with Ctrl+V (or the Paste button of the new download window).  No local server and no
registry entry are needed for that.
"""
from __future__ import annotations

import json
from pathlib import Path

EXTENSION_DIR = Path.home() / ".mediafire-dl" / "browser-extension"

MANIFEST = {
    "manifest_version": 3,
    "name": "ArkLoad - Modern Download Manager",
    "version": "1.0",
    "description": "Put a MediaFire link on the clipboard for the ArkLoad download manager.",
    "permissions": ["tabs", "clipboardWrite"],
    "host_permissions": ["*://*.mediafire.com/*"],
    "background": {"service_worker": "background.js"},
    "action": {"default_popup": "popup.html"},
}

BACKGROUND = """// Copies the MediaFire link of the current tab to the clipboard.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === 'copy-link') {
    chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
      const url = tabs && tabs[0] ? tabs[0].url : '';
      chrome.tabs.query({active: true, currentWindow: true}, (tabs2) => {
        const tab = tabs2 && tabs2[0];
        if (tab) {
          chrome.tabs.update(tab.id, {url: tab.url});
        }
        navigator.clipboard.writeText(url).then(
          () => sendResponse({ok: true, url: url}),
          () => sendResponse({ok: false, url: url}));
      });
    });
    return true;
  }
  return false;
});
"""

POPUP_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { width: 250px; margin: 0; padding: 14px; background: #1A1A1B; color: #E4E4E7;
         font: 13px "Segoe UI", system-ui, sans-serif; }
  h1 { font-size: 14px; margin: 0 0 10px; }
  p { color: #8E8E93; margin: 10px 0 0; }
  button { margin-top: 12px; width: 100%; background: #4CD3FF; color: #06212B; border: none;
           border-radius: 6px; padding: 8px; font-weight: 600; cursor: pointer; }
  #status { margin-top: 8px; color: #22C55E; min-height: 16px; }
</style></head><body>
<h1>ArkLoad - Modern Download Manager</h1>
<p>Copies the link of this page to the clipboard. Paste it in ArkLoad with Ctrl+V.</p>
<button id="copy">Copy the link</button>
<div id="status"></div>
<script src="popup.js"></script>
</body></html>
"""

POPUP_JS = """const status = document.getElementById('status');
document.getElementById('copy').addEventListener('click', () => {
  chrome.runtime.sendMessage({type: 'copy-link'}, (answer) => {
    status.textContent = (answer && answer.ok)
      ? 'Copied - paste it in ArkLoad.'
      : 'Could not copy the link.';
  });
});
"""

README = """ArkLoad browser extension
====================

This folder holds an unpacked browser extension for the ArkLoad download manager.

1. Open the extension page of your browser
   (Chrome/Edge: chrome://extensions or edge://extensions).
2. Turn on "Developer mode".
3. Press "Load unpacked" and choose this folder.
4. Press the extension button on a MediaFire page: it copies the link to the clipboard.
5. In ArkLoad press "Add download" and then "Paste" (or Ctrl+V in the link field).

The extension only reads the address of the tab you are looking at.
"""


def write_extension(target: Path | None = None) -> Path:
    """Write the extension files and return the folder."""
    folder = Path(target) if target else EXTENSION_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    (folder / "background.js").write_text(BACKGROUND, encoding="utf-8")
    (folder / "popup.html").write_text(POPUP_HTML, encoding="utf-8")
    (folder / "popup.js").write_text(POPUP_JS, encoding="utf-8")
    (folder / "README.txt").write_text(README, encoding="utf-8")
    return folder
