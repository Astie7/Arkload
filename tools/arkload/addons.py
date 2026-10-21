"""A small add-on system.

An add-on is a folder in ``~/.mediafire-dl/addons`` with an ``addon.json`` and a Python file::

    ~/.mediafire-dl/addons/hello/addon.json
    ~/.mediafire-dl/addons/hello/main.py

    # addon.json
    {"name": "Hello", "version": "1.0", "description": "Says hello",
     "entry": "main.py"}

    # main.py
    def setup(api):
        api.log("hello add-on loaded")
        api.register_hook("after_download", lambda record: api.log(record.title))

The application calls ``setup(api)`` for every enabled add-on at start-up and offers the
add-ons in the "Add-ons" window, where they can be enabled, disabled and reloaded.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ADDONS_DIR = Path.home() / ".mediafire-dl" / "addons"


@dataclass
class Addon:
    """One discovered add-on."""

    folder: Path
    name: str = ""
    version: str = "0.0"
    description: str = ""
    author: str = ""
    entry: str = "main.py"
    enabled: bool = False
    error: str = ""
    module: object = None

    @property
    def id(self) -> str:
        return self.folder.name


class AddonAPI:
    """What an add-on may use. It never gets the raw window without a reason."""

    def __init__(self, addon: Addon, app, manager: "AddonManager"):
        self.addon = addon
        self.app = app
        self.manager = manager
        self.settings: dict = {}

    # -- helpers --------------------------------------------------------------
    def log(self, message: str) -> None:
        self.manager.log(f"[{self.addon.name}] {message}")

    def add_menu_action(self, label: str, callback: Callable[[], None]) -> None:
        self.manager.register_menu_action(self.addon, label, callback)

    def register_hook(self, name: str, callback) -> None:
        self.manager.register_hook(self.addon, name, callback)

    def state_file(self, name: str) -> Path:
        folder = self.addon.folder / "state"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / name

    def read_state(self, name: str, default=None):
        try:
            return json.loads(self.state_file(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_state(self, name: str, data) -> None:
        try:
            self.state_file(name).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


class AddonManager:
    """Discovers the add-ons, loads the enabled ones and keeps their hooks."""

    HOOKS = ("before_download", "after_download", "status_change")

    def __init__(self, folder: Path | None = None, app=None):
        self.folder = Path(folder) if folder else ADDONS_DIR
        self.app = app
        self.addons: list[Addon] = []
        self.hooks: dict[str, list[tuple[Addon, Callable]]] = {name: [] for name in self.HOOKS}
        self.menu_actions: list[tuple[Addon, str, Callable]] = []
        self.messages: list[str] = []

    # -- discovery ------------------------------------------------------------
    def discover(self) -> list[Addon]:
        """Read every add-on folder; broken ones are listed with their error."""
        found: list[Addon] = []
        try:
            folders = sorted(path for path in self.folder.iterdir() if path.is_dir())
        except OSError:
            folders = []
        for folder in folders:
            manifest = folder / "addon.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                addon = Addon(folder=folder, name=folder.name, error=f"addon.json: {e}")
                found.append(addon)
                continue
            addon = Addon(
                folder=folder,
                name=str(data.get("name") or folder.name),
                version=str(data.get("version") or "0.0"),
                description=str(data.get("description") or ""),
                author=str(data.get("author") or ""),
                entry=str(data.get("entry") or "main.py"),
            )
            found.append(addon)
        self.addons = found
        return found

    def enabled(self) -> list[Addon]:
        return [addon for addon in self.addons if addon.enabled]

    # -- loading --------------------------------------------------------------
    def load_enabled(self) -> list[Addon]:
        """Import and start the enabled add-ons."""
        loaded = []
        for addon in self.addons:
            if not addon.enabled:
                continue
            if self._load(addon):
                loaded.append(addon)
        return loaded

    def _load(self, addon: Addon) -> bool:
        entry = addon.folder / addon.entry
        if not entry.is_file():
            addon.error = f"{addon.entry} is missing"
            return False
        try:
            spec = importlib.util.spec_from_file_location(
                f"arkload_addon_{addon.id}", entry)
            if spec is None or spec.loader is None:
                raise ImportError("the add-on could not be imported")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setup = getattr(module, "setup", None)
            if setup is None:
                raise AttributeError("the add-on has no setup() function")
            addon.module = module
            setup(AddonAPI(addon, self.app, self))
            addon.error = ""
            self.log(f"{addon.name} {addon.version} loaded")
            return True
        except Exception as e:  # noqa: BLE001 - a broken add-on must not stop the app
            addon.error = f"{type(e).__name__}: {e}"
            addon.module = None
            self.log(f"{addon.name} failed: {addon.error}")
            traceback.print_exc()
            return False

    def reload(self, addon: Addon) -> bool:
        """Load one add-on again (after an edit or a toggle)."""
        self._clear(addon)
        if not addon.enabled:
            return True
        return self._load(addon)

    def unload(self, addon: Addon) -> None:
        self._clear(addon)
        addon.module = None

    def _clear(self, addon: Addon) -> None:
        module = addon.module
        if module is not None:
            teardown = getattr(module, "teardown", None)
            if teardown is not None:
                try:
                    teardown(AddonAPI(addon, self.app, self))
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        for name in self.HOOKS:
            self.hooks[name] = [entry for entry in self.hooks[name] if entry[0] is not addon]
        self.menu_actions = [entry for entry in self.menu_actions if entry[0] is not addon]

    # -- hooks and menu -------------------------------------------------------
    def register_hook(self, addon: Addon, name: str, callback) -> None:
        if name not in self.hooks:
            self.hooks[name] = []
        self.hooks[name].append((addon, callback))

    def register_menu_action(self, addon: Addon, label: str, callback) -> None:
        self.menu_actions.append((addon, label, callback))

    def fire(self, name: str, *args) -> None:
        for addon, callback in list(self.hooks.get(name, [])):
            try:
                callback(*args)
            except Exception as e:  # noqa: BLE001 - an add-on must not break the download
                self.log(f"{addon.name} failed in {name}: {e}")

    def log(self, message: str) -> None:
        self.messages.append(message)
        if len(self.messages) > 200:
            del self.messages[:-200]
