"""The host plugin layer of ArkLoad.

JDownloader 2 turns a URL into *download links* with a host plugin, and the queue downloads
them. ArkLoad uses the same idea, because it is the part of JDownloader's architecture that
makes sense on its own:

* a **host plugin** knows one service: it recognises its links and resolves them into
  :class:`DownloadLink` objects (the file name, the size, the checksum the service publishes),
* the **queue** downloads the links, whatever produced them,
* a **registry** finds the plugins: the built-in ones and every add-on in the add-on folder.

The MediaFire host is the first implementation; it reuses the proven link handling of
``tools/mediafire_dl.py`` (core API 1.5, folder expansion, expiring direct URLs, the published
SHA-256).

This is the contract the upstream plugin uses, reimplemented in Python:

    Plugin            ->  HostPlugin       (the base class)
    PluginForHost     ->  HostPlugin       (recognises the links of one service)
    DownloadLink      ->  DownloadLink     (name, size, hash, the direct url)
    decryptIt()       ->  resolve()       (a folder link becomes many links)
    getMaxChunks()    ->  the connections setting
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import settings as settings_store


class HostError(RuntimeError):
    """A link could not be resolved."""


@dataclass
class DownloadLink:
    """One downloadable file, the way a host plugin hands it to the queue."""

    url: str
    name: str
    size: int | None = None
    hash: str | None = None
    headers: dict = field(default_factory=dict)
    cookies: str | None = None
    source: str = ""
    package: str = ""
    item: object = None      # the host specific payload (a planned file, a magnet, ...)

    @property
    def verified(self) -> bool:
        """True when the source published a checksum we can verify against."""
        return bool(self.hash)


class HostPlugin:
    """The base class of a host plugin.

    A plugin implements :meth:`matches` and :meth:`resolve`.  ``resolve`` returns the links of
    a URL - a single file link returns one link, a folder link returns all of its files.
    """

    name: str = ""
    domains: tuple = ()
    #: the number of connections the host allows per file (0 means "no chunking")
    max_chunks: int = 1

    def matches(self, url: str) -> bool:
        lowered = url.lower()
        return any(domain in lowered for domain in self.domains)

    def resolve(self, url: str) -> list[DownloadLink]:
        raise NotImplementedError

    def plan(self, url: str):
        """The plan of a link, for the hosts whose downloads are planned as a tree."""
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} {self.name}>"


class MediafireHost(HostPlugin):
    """MediaFire folders, files and bare folder keys."""

    name = "MediaFire"
    domains = ("mediafire.com", "mfi.re")
    max_chunks = 8

    def plan(self, url: str):
        """The download plan of a MediaFire link (the tree of a folder)."""
        from .. import mediafire_dl as dl

        try:
            source = dl.parse_source(url)
            return dl.make_plan(source)
        except Exception as e:  # noqa: BLE001 - the host reports every problem the same way
            raise HostError(str(e)) from e

    def resolve(self, url: str) -> list[DownloadLink]:
        plan = self.plan(url)
        links = []
        for item in plan.files:
            links.append(DownloadLink(
                url=item.page_url or url,
                name=item.rel.as_posix(),
                size=item.size,
                hash=item.sha256,
                source=self.name,
                package=item.rel.parent.as_posix() if item.rel.parent.as_posix() != "." else "",
                item=item,
            ))
        if not links:
            raise HostError("the link does not contain any file")
        return links


class HostRegistry:
    """Finds the host plugins and resolves the links with them."""

    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else settings_store.addons_dir()
        self.plugins: list[HostPlugin] = []
        self.errors: list[str] = []

    # -- the plugins ----------------------------------------------------------
    def discover(self) -> list[HostPlugin]:
        """The built-in hosts plus every add-on that offers one."""
        self.plugins = [MediafireHost()]
        self.errors = []
        for plugin in self._load_addons():
            self.plugins.append(plugin)
        return self.plugins

    def _load_addons(self) -> list[HostPlugin]:
        """Load the host plugins from the add-on folder."""
        found: list[HostPlugin] = []
        try:
            folders = sorted(path for path in self.folder.iterdir() if path.is_dir())
        except OSError:
            return found
        for folder in folders:
            manifest = folder / "addon.json"
            if not manifest.is_file():
                continue
            try:
                import json
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self.errors.append(f"{folder.name}: addon.json ({e})")
                continue
            entry = str(data.get("entry") or "main.py")
            path = folder / entry
            if not path.is_file():
                self.errors.append(f"{folder.name}: {entry} is missing")
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"arkload_addon_{folder.name}", path)
                if spec is None or spec.loader is None:
                    raise ImportError("the add-on could not be imported")
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                plugin = module.create_host_plugin()
                found.append(plugin)
            except Exception as e:  # noqa: BLE001 - a broken add-on must not stop the app
                self.errors.append(f"{folder.name}: {type(e).__name__}: {e}")
        return found

    def register(self, plugin: HostPlugin) -> None:
        self.plugins.append(plugin)

    # -- resolving ------------------------------------------------------------
    def plugin_for(self, url: str) -> HostPlugin | None:
        for plugin in self.plugins:
            if plugin.matches(url):
                return plugin
        return None

    def resolve(self, url: str) -> list[DownloadLink]:
        """Turn a URL into the files it contains."""
        plugin = self.plugin_for(url)
        if plugin is None:
            raise HostError(f"no host plugin handles this link: {url}")
        return plugin.resolve(url)

    def plan_for(self, url: str):
        """The plan of a link, resolved by the host that knows it."""
        plugin = self.plugin_for(url)
        if plugin is None:
            raise HostError(f"no host plugin handles this link: {url}")
        plan = plugin.plan(url)
        if plan is None:
            raise HostError(f"the host cannot plan this link: {url}")
        return plan

    def known_hosts(self) -> list[str]:
        return [plugin.name for plugin in self.plugins]
