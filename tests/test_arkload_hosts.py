"""Tests for the host plugin layer: the contract, the registry and the MediaFire host.

    uv run --with pytest pytest -q tests/test_arkload_hosts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.hosts import (DownloadLink, HostError, HostPlugin, HostRegistry,  # noqa: E402
                                MediafireHost)


# --------------------------------------------------------------------------- the contract

def test_a_download_link_carries_the_file_information():
    link = DownloadLink(url="https://www.mediafire.com/file/abc/x.jar", name="x.jar", size=10,
                        hash="a" * 64, source="MediaFire")
    assert link.name == "x.jar"
    assert link.size == 10
    assert link.verified is True
    assert DownloadLink(url="u", name="n").verified is False


def test_a_host_plugin_recognises_its_own_links():
    host = MediafireHost()
    assert host.matches("https://www.mediafire.com/folder/abc") is True
    assert host.matches("https://mediafire.com/?abc") is True
    assert host.matches("https://example.com/file.zip") is False


def test_the_base_plugin_has_to_be_implemented():
    with pytest.raises(NotImplementedError):
        HostPlugin().resolve("https://example.com/x")


# --------------------------------------------------------------------------- the registry

def test_the_registry_knows_the_built_in_hosts():
    registry = HostRegistry()
    registry.discover()
    assert "MediaFire" in registry.known_hosts()


def test_an_unknown_link_is_refused_with_a_clear_message():
    registry = HostRegistry()
    registry.discover()
    with pytest.raises(HostError) as error:
        registry.resolve("https://example.com/file.zip")
    assert "no host plugin" in str(error.value)


def test_a_broken_addon_does_not_stop_the_registry(tmp_path):
    addon = tmp_path / "broken"
    addon.mkdir()
    (addon / "addon.json").write_text(json.dumps({"name": "Broken", "entry": "main.py"}),
                                     encoding="utf-8")
    registry = HostRegistry(tmp_path)
    registry.discover()
    assert "MediaFire" in registry.known_hosts()
    assert registry.errors and "main.py" in registry.errors[0]


def test_an_addon_can_add_a_host(tmp_path):
    addon = tmp_path / "example"
    addon.mkdir()
    (addon / "addon.json").write_text(json.dumps({"name": "Example", "entry": "main.py"}),
                                     encoding="utf-8")
    (addon / "main.py").write_text(
        "from tools.arkload.hosts import DownloadLink, HostPlugin\n"
        "\n"
        "\n"
        "class ExampleHost(HostPlugin):\n"
        "    name = 'Example'\n"
        "    domains = ('example.org',)\n"
        "\n"
        "    def resolve(self, url):\n"
        "        return [DownloadLink(url=url, name='file.bin', size=1, source=self.name)]\n"
        "\n"
        "\n"
        "def create_host_plugin():\n"
        "    return ExampleHost()\n", encoding="utf-8")
    registry = HostRegistry(tmp_path)
    registry.discover()
    assert "Example" in registry.known_hosts()
    links = registry.resolve("https://example.org/file.bin")
    assert links[0].name == "file.bin"


def test_the_mediafire_host_resolves_a_file_link():
    """A real MediaFire file link becomes one download link with its name and size."""
    from tools import mediafire_dl as dl

    source = dl.parse_source("https://www.mediafire.com/file/padqwka2lz0ktki/H2DB.jar")
    info = dl.file_info(source.key)
    host = MediafireHost()
    links = host.resolve("https://www.mediafire.com/file/padqwka2lz0ktki/H2DB.jar")
    assert len(links) == 1
    assert links[0].name == "H2DB.jar"
    assert links[0].size == int(info["size"])
    assert links[0].verified is True          # MediaFire publishes the SHA-256
    assert links[0].hash == info["hash"]


def test_the_mediafire_host_resolves_a_folder_link():
    """A real MediaFire folder link becomes one link per file."""
    host = MediafireHost()
    links = host.resolve("https://www.mediafire.com/folder/dpqhf3ughd4j5/libs")
    assert len(links) >= 5
    assert all(link.name for link in links)
    assert all(link.size for link in links)
    assert all(link.verified for link in links)


def test_the_mediafire_host_reports_a_broken_link():
    host = MediafireHost()
    with pytest.raises(HostError):
        host.resolve("https://www.mediafire.com/folder/this-key-does-not-exist")


def test_a_bare_folder_key_works_as_well():
    host = MediafireHost()
    links = host.resolve("dpqhf3ughd4j5")
    assert links
    assert links[0].source == "MediaFire"
