"""The download engines of ArkLoad.

* :mod:`~tools.arkload.engines.base` - the interface every engine offers,
* :mod:`~tools.arkload.engines.builtin` - ArkLoad's own downloader (MediaFire, with the hash
  verification),
* :mod:`~tools.arkload.engines.jdownloader` - the JDownloader 2 engine (the hosters),
* :mod:`~tools.arkload.engines.aria2` - aria2 (the direct links),
* :mod:`~tools.arkload.engines.registry` - the rule that decides which engine runs a download and
  the fallback when one of them does not know the url,
* :mod:`~tools.arkload.engines.resources` - where the bundled engines live.
"""
from __future__ import annotations

from .base import (Capabilities, DownloadEngine, DownloadRequest, EngineError, EngineStatus,
                    ErrorKind)
from .aria2 import Aria2Engine
from .builtin import BuiltinEngine
from .jdownloader import JDownloaderEngine
from .registry import EngineRegistry
from . import resources

__all__ = [
    "Aria2Engine",
    "BuiltinEngine",
    "Capabilities",
    "DownloadEngine",
    "DownloadRequest",
    "EngineError",
    "EngineRegistry",
    "EngineStatus",
    "ErrorKind",
    "JDownloaderEngine",
    "resources",
]
